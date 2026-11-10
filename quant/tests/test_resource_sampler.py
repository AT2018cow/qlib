"""Resource sampler tests with a synthetic /proc and cgroup tree."""

from __future__ import annotations

import json
import time
from pathlib import Path

from resource_sampler import ResourceSampler


def _stat(pid: int, ppid: int, utime: int = 10, stime: int = 5) -> str:
    return f"{pid} (test proc) S {ppid} {pid} {pid} 0 -1 4194560 0 0 0 0 {utime} {stime} 0 0 20 0 1 0 100 0\n"


def make_tree(root: Path, cgroup: Path) -> tuple[Path, Path]:
    proc = root / "proc"
    for child, ppid, rss, pss, utime, stime in (
        (100, 1, 1000, 800, 10, 5),
        (101, 100, 2000, 1500, 20, 10),
        (102, 101, 500, 400, 5, 2),
    ):
        directory = proc / str(child)
        directory.mkdir(parents=True)
        (directory / "stat").write_text(_stat(child, ppid, utime, stime))
        (directory / "status").write_text(f"Name:\ttest\nVmRSS:\t{rss} kB\n")
        (directory / "smaps_rollup").write_text(f"Rss:\t{rss} kB\nPss:\t{pss} kB\n")
    cgroup.mkdir(parents=True)
    (cgroup / "memory.current").write_text("4096\n")
    (cgroup / "memory.peak").write_text("8192\n")
    (cgroup / "cpu.stat").write_text("usage_usec 2500000\nnr_throttled 3\n")
    (cgroup / "cpu.max").write_text("400000 100000\n")
    (cgroup / "memory.max").write_text("8589934592\n")
    return proc, cgroup


def test_process_tree_and_cgroup_are_aggregated(tmp_path):
    proc, cgroup = make_tree(tmp_path, tmp_path / "cgroup")
    sampler = ResourceSampler(interval=0.05, proc_root=proc, cgroup_root=cgroup, pid=100)
    with sampler:
        time.sleep(0.12)
    summary = sampler.summary()
    assert summary["status"] == "ok"
    assert summary["pid_count_max"] == 3
    assert summary["rss_kb_max"] == 3500
    assert summary["pss_kb_max"] == 2700
    assert summary["cgroup_memory_max_bytes"] == 4096
    assert summary["cgroup_memory_peak_bytes"] == 8192
    assert summary["cpu_max"] == "400000 100000"
    assert summary["memory_max"] == "8589934592"


def test_stage_records_error_and_still_summarises(tmp_path):
    proc, cgroup = make_tree(tmp_path, tmp_path / "cgroup")
    sampler = ResourceSampler(interval=0.05, proc_root=proc, cgroup_root=cgroup, pid=100)
    sampler.start()
    try:
        with sampler.stage("boom"):
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    summary = sampler.stop()
    assert summary["status"] == "ok"
    assert summary["stages"][0]["name"] == "boom"
    assert "RuntimeError" in summary["stages"][0]["error"]
    assert summary["stages"][0]["wall_seconds"] >= 0


def test_unreadable_metrics_are_unavailable_not_zero(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    sampler = ResourceSampler(interval=0.05, proc_root=empty / "proc", cgroup_root=empty / "cgroup", pid=12345)
    with sampler:
        pass
    summary = sampler.summary()
    assert summary["status"] == "ok"
    assert summary["rss_kb_max"] is None
    assert summary["cgroup_memory_max_bytes"] is None
    assert "proc" in summary["unavailable"]
    assert "cgroup_memory" in summary["unavailable"]


def test_write_json_is_strict(tmp_path):
    proc, cgroup = make_tree(tmp_path, tmp_path / "cgroup")
    sampler = ResourceSampler(interval=0.05, proc_root=proc, cgroup_root=cgroup, pid=100)
    with sampler:
        pass
    target = tmp_path / "metrics.json"
    sampler.write(target)
    payload = json.loads(target.read_text())
    assert payload["status"] == "ok"
