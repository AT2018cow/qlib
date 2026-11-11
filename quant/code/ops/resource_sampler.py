"""Process-tree and cgroup resource sampling for production stages.

The prior probe used a sampler that could return ``null``/``0`` when a cgroup
file was unreadable, which is indistinguishable from "no resource use".  This
module records unreadable metrics explicitly as ``unavailable`` and always
produces a summary, including when the stage raises.

Metrics are process-tree sums (RSS may double-count shared pages) plus cgroup
v2 memory/CPU when available.  It is deliberately stdlib-only so it can be
tested and imported in every worker.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(float(text.strip().split()[0]))
    except (ValueError, IndexError):
        return None


class ResourceSampler:
    """Sample the current process tree and its cgroup while a stage runs."""

    def __init__(
        self,
        *,
        interval: float = 2.0,
        proc_root: str | Path = "/proc",
        cgroup_root: str | Path = "/sys/fs/cgroup",
        pid: int | None = None,
    ):
        self.interval = float(interval)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        self.pid = int(pid if pid is not None else os.getpid())
        self._samples: list[dict] = []
        self._stages: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._started_wall: str | None = None
        self._unavailable: set[str] = set()
        self._stage_stack: list[dict] = []
        self._cpu_seen: dict[tuple[int, int], float] = {}
        self._cpu_lifetime_max = 0.0

    # ---- low-level readers -------------------------------------------------
    def _children_map(self) -> dict[int, list[int]]:
        children: dict[int, list[int]] = {}
        try:
            entries = list(self.proc_root.iterdir())
        except OSError:
            self._unavailable.add("proc")
            return children
        for entry in entries:
            if not entry.name.isdigit():
                continue
            stat = _read_text(entry / "stat")
            if not stat:
                continue
            try:
                # comm may contain spaces/parentheses; ppid is field 4 after ')'
                tail = stat.rsplit(")", 1)[1].strip().split()
                ppid = int(tail[1])
            except (IndexError, ValueError):
                continue
            children.setdefault(ppid, []).append(int(entry.name))
        return children

    def _tree(self) -> list[int]:
        children = self._children_map()
        output, stack = [], [self.pid]
        while stack:
            current = stack.pop()
            output.append(current)
            stack.extend(children.get(current, []))
        return output

    def _proc_cpu_entries(self, pids: list[int]) -> dict[tuple[int, int], dict] | None:
        """Return lifetime CPU per (pid, starttime), including reaped children.

        ``utime+stime`` is the process lifetime CPU; ``cutime+cstime`` is the
        CPU of already-reaped children.  Keying by starttime avoids treating a
        reused PID as the same process.
        """
        ticks = os.sysconf("SC_CLK_TCK")
        entries: dict[tuple[int, int], dict] = {}
        for pid in pids:
            stat = _read_text(self.proc_root / str(pid) / "stat")
            if not stat:
                continue
            try:
                tail = stat.rsplit(")", 1)[1].strip().split()
                starttime = int(tail[19])
                entries[(pid, starttime)] = {
                    "self": (int(tail[11]) + int(tail[12])) / ticks,
                    "reaped": (int(tail[13]) + int(tail[14])) / ticks,
                }
            except (IndexError, ValueError):
                continue
        if not entries:
            self._unavailable.add("proc_cpu")
            return None
        return entries

    def _update_cpu(self, entries: dict[tuple[int, int], dict] | None) -> float | None:
        if entries is None:
            return None
        for key, item in entries.items():
            self._cpu_seen[key] = max(self._cpu_seen.get(key, 0.0), float(item["self"]))
        # The live tree's self+reaped sum captures children that were reaped
        # between samples.  The per-key self maximum captures exited children
        # observed earlier.  Taking the maximum avoids double-counting the same
        # child; the result is a lower bound, never a negative delta.
        live_lifetime = sum(float(item["self"]) + float(item["reaped"]) for item in entries.values())
        self._cpu_lifetime_max = max(self._cpu_lifetime_max, live_lifetime)
        return max(sum(self._cpu_seen.values()), self._cpu_lifetime_max)

    def _proc_memory_kb(self, pids: list[int], field: str) -> int | None:
        total = 0
        found = False
        for pid in pids:
            root = self.proc_root / str(pid)
            if field == "rss":
                status = _read_text(root / "status")
                if not status:
                    continue
                for line in status.splitlines():
                    if line.startswith("VmRSS:"):
                        try:
                            total += int(line.split()[1])
                            found = True
                        except (IndexError, ValueError):
                            pass
                        break
            else:
                smaps = _read_text(root / "smaps_rollup")
                if not smaps:
                    continue
                for line in smaps.splitlines():
                    if line.startswith("Pss:"):
                        try:
                            total += int(line.split()[1])
                            found = True
                        except (IndexError, ValueError):
                            pass
                        break
        if not found:
            self._unavailable.add(f"proc_{field}")
            return None
        return total

    def _cgroup(self) -> dict:
        memory_current = _read_int(self.cgroup_root / "memory.current")
        memory_peak = _read_int(self.cgroup_root / "memory.peak")
        cpu_stat = _read_text(self.cgroup_root / "cpu.stat")
        cpu_seconds = None
        throttled = None
        if cpu_stat:
            values = {}
            for line in cpu_stat.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].replace(".", "", 1).isdigit():
                    values[parts[0]] = float(parts[1])
            if "usage_usec" in values:
                cpu_seconds = values["usage_usec"] / 1_000_000.0
            throttled = values.get("nr_throttled")
        cpu_max = _read_text(self.cgroup_root / "cpu.max")
        memory_max = _read_text(self.cgroup_root / "memory.max")
        if memory_current is None:
            self._unavailable.add("cgroup_memory")
        if cpu_seconds is None:
            self._unavailable.add("cgroup_cpu")
        return {
            "memory_current_bytes": memory_current,
            "memory_peak_bytes": memory_peak,
            "cpu_seconds": cpu_seconds,
            "throttled_periods": throttled,
            "cpu_max": (cpu_max or "").strip() or None,
            "memory_max": (memory_max or "").strip() or None,
        }

    def sample_once(self) -> dict:
        pids = self._tree()
        entries = self._proc_cpu_entries(pids)
        return {
            "epoch": time.time(),
            "pid_count": len(pids),
            "rss_kb_sum": self._proc_memory_kb(pids, "rss"),
            "pss_kb_sum": self._proc_memory_kb(pids, "pss"),
            "cpu_seconds_sum": self._update_cpu(entries),
            "cgroup": self._cgroup(),
        }

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> "ResourceSampler":
        if self._thread is not None:
            return self
        self._started_at = time.monotonic()
        self._started_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._samples.append(self.sample_once())

        def loop() -> None:
            while not self._stop.wait(self.interval):
                self._samples.append(self.sample_once())

        self._thread = threading.Thread(target=loop, name="resource-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=max(1.0, self.interval * 2))
            self._thread = None
            self._samples.append(self.sample_once())
        return self.summary()

    def __enter__(self) -> "ResourceSampler":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    @contextmanager
    def stage(self, name: str) -> Iterator[dict]:
        record = {"name": name, "started_epoch": time.time(), "started_monotonic": time.monotonic()}
        before = self.sample_once()
        record["before"] = before
        self._stage_stack.append(record)
        try:
            yield record
        except Exception as exc:  # noqa: BLE001 - record and re-raise
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            after = self.sample_once()
            record["after"] = after
            record["wall_seconds"] = time.monotonic() - record.pop("started_monotonic")
            before_cpu = before.get("cpu_seconds_sum")
            after_cpu = after.get("cpu_seconds_sum")
            record["cpu_seconds"] = after_cpu - before_cpu if before_cpu is not None and after_cpu is not None else None
            self._stage_stack.remove(record)
            self._stages.append(record)

    # ---- summary -----------------------------------------------------------
    def summary(self) -> dict:
        wall = None if self._started_at is None else time.monotonic() - self._started_at
        if not self._samples:
            return {
                "status": "unavailable",
                "reason": "no samples collected",
                "interval_seconds": self.interval,
                "unavailable": sorted(self._unavailable),
            }
        last = self._samples[-1]
        cpu_values = [item.get("cpu_seconds_sum") for item in self._samples if item.get("cpu_seconds_sum") is not None]
        rss_values = [item.get("rss_kb_sum") for item in self._samples if item.get("rss_kb_sum") is not None]
        pss_values = [item.get("pss_kb_sum") for item in self._samples if item.get("pss_kb_sum") is not None]
        cgroup_memory = [
            (item.get("cgroup") or {}).get("memory_current_bytes")
            for item in self._samples
            if (item.get("cgroup") or {}).get("memory_current_bytes") is not None
        ]
        cgroup_peak = [
            (item.get("cgroup") or {}).get("memory_peak_bytes")
            for item in self._samples
            if (item.get("cgroup") or {}).get("memory_peak_bytes") is not None
        ]
        process_cpu_delta = (cpu_values[-1] - cpu_values[0]) if len(cpu_values) >= 2 else None
        cgroup_start = next(
            (
                (item.get("cgroup") or {}).get("cpu_seconds")
                for item in self._samples
                if (item.get("cgroup") or {}).get("cpu_seconds") is not None
            ),
            None,
        )
        cgroup_end = next(
            (
                (item.get("cgroup") or {}).get("cpu_seconds")
                for item in reversed(self._samples)
                if (item.get("cgroup") or {}).get("cpu_seconds") is not None
            ),
            None,
        )
        cgroup_delta = cgroup_end - cgroup_start if cgroup_start is not None and cgroup_end is not None else None
        cpu_delta = cgroup_delta if cgroup_delta is not None else process_cpu_delta
        cpu_method = "cgroup" if cgroup_delta is not None else "process_lifetime_lower_bound"
        summary = {
            "status": "ok",
            "started_utc": self._started_wall,
            "wall_seconds": round(wall, 3) if wall is not None else None,
            "interval_seconds": self.interval,
            "samples": len(self._samples),
            "pid_count_max": max(item.get("pid_count") or 0 for item in self._samples),
            "cpu_seconds": round(cpu_delta, 3) if cpu_delta is not None else None,
            "cpu_seconds_method": cpu_method,
            "cpu_seconds_is_lower_bound": cpu_method != "cgroup",
            "process_cpu_seconds_lower_bound": (round(process_cpu_delta, 3) if process_cpu_delta is not None else None),
            "avg_cores": round(cpu_delta / wall, 3) if cpu_delta is not None and wall else None,
            "rss_kb_max": max(rss_values) if rss_values else None,
            "pss_kb_max": max(pss_values) if pss_values else None,
            "cgroup_memory_max_bytes": max(cgroup_memory) if cgroup_memory else None,
            "cgroup_memory_peak_bytes": max(cgroup_peak) if cgroup_peak else None,
            "cgroup_cpu_seconds_start": next(
                (
                    (item.get("cgroup") or {}).get("cpu_seconds")
                    for item in self._samples
                    if (item.get("cgroup") or {}).get("cpu_seconds") is not None
                ),
                None,
            ),
            "cgroup_cpu_seconds_end": next(
                (
                    (item.get("cgroup") or {}).get("cpu_seconds")
                    for item in reversed(self._samples)
                    if (item.get("cgroup") or {}).get("cpu_seconds") is not None
                ),
                None,
            ),
            "cpu_max": (last.get("cgroup") or {}).get("cpu_max"),
            "memory_max": (last.get("cgroup") or {}).get("memory_max"),
            "throttled_periods_end": (last.get("cgroup") or {}).get("throttled_periods"),
            "stages": [
                {key: value for key, value in stage.items() if key in ("name", "wall_seconds", "cpu_seconds", "error")}
                for stage in self._stages
            ],
            "unavailable": sorted(self._unavailable),
        }
        return summary

    def write(self, path: str | Path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.summary(), indent=2, sort_keys=True, allow_nan=False) + "\n")
        return str(target)
