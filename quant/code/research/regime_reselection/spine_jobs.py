"""Job planning for G1 stress-first spine generation (2021-02 .. 2024-02)."""

from __future__ import annotations

from bisect import bisect_left

RETRAIN_EVERY = 20

# Spine A = current production profile/horizon; Spine B = same profile with the
# complementary label horizon (20d <-> 10d), as pre-registered in the protocol.
MARKETS = {
    "csi300": {"profile": "59b93929_top10_drop1", "horizon": 20, "benchmark": "SH000300"},
    "csi500": {"profile": "raw10d_c98856b4_top10_drop1", "horizon": 10, "benchmark": "SH000905"},
    "csi1000": {"profile": "c98856b4_top10_drop1", "horizon": 20, "benchmark": "SH000852"},
}
SPINES = {"A": "current", "B": "swapped"}


def swapped_horizon(horizon: int) -> int:
    if horizon == 20:
        return 10
    if horizon == 10:
        return 20
    raise ValueError(f"no complementary horizon for {horizon}")


def build_jobs(
    calendar,
    start: str,
    end: str,
    run_id: str,
    code_sha: str,
    markets=("csi300", "csi500", "csi1000"),
    spines=("A", "B"),
    num_threads: int = 8,
) -> list[dict]:
    """Return deterministic fit jobs; each job predicts [asof, signal_end]."""
    if start not in calendar or end not in calendar:
        raise ValueError("start/end must be trading days in the calendar")
    start_i = bisect_left(calendar, start)
    end_i = bisect_left(calendar, end)
    if start_i > end_i:
        raise ValueError("start must not be after end")
    jobs = []
    for market in markets:
        spec = MARKETS[market]
        for spine in spines:
            if spine not in SPINES:
                raise ValueError(f"unknown spine {spine}")
            horizon = spec["horizon"] if spine == "A" else swapped_horizon(spec["horizon"])
            index = start_i
            while index <= end_i:
                signal_end_i = min(index + RETRAIN_EVERY - 1, end_i)
                jobs.append(
                    {
                        "run_id": run_id,
                        "code_sha": code_sha,
                        "market": market,
                        "spine": spine,
                        "profile": spec["profile"],
                        "label_horizon": horizon,
                        "retrain_asof": calendar[index],
                        "signal_end": calendar[signal_end_i],
                        "num_threads": int(num_threads),
                    }
                )
                index += RETRAIN_EVERY
    return jobs


def expected_days(calendar, start: str, end: str) -> list[str]:
    start_i = bisect_left(calendar, start)
    end_i = bisect_left(calendar, end)
    return calendar[start_i : end_i + 1]


def validate_assembly(calendar, start: str, end: str, signal_days) -> None:
    expected = expected_days(calendar, start, end)
    actual = sorted(set(str(day)[:10] for day in signal_days))
    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:5]
        extra = sorted(set(actual) - set(expected))[:5]
        raise ValueError(f"spine coverage mismatch: missing={missing} extra={extra}")
