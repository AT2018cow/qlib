"""Local orchestration for ChiNext / STAR reproducibility gate + 4-phase screen.

This module intentionally does not import or wrap the canonical Modal functions.
It launches the existing apps through the Modal CLI so each app hydrates in its
own context, then uses a tiny read-only helper to validate and export artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import modal

from satellite_audit_core import (
    SCREEN_PHASES,
    compact_audit_summary,
    frozen_audit_config,
    validate_gate_payload,
    validate_screen_payload,
)

APP_NAME = "qlib-satellite-pre-tuner-audit"
VOL_NAME = "qlib-cn-data"
VOL_ROOT = Path("/vol")

vol = modal.Volume.from_name(VOL_NAME, create_if_missing=False)
image = modal.Image.debian_slim(python_version="3.11")
app = modal.App(APP_NAME, image=image)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


@app.function(volumes={str(VOL_ROOT): vol}, timeout=15 * 60)
def read_and_validate_satellite_audit(
    market: str,
    freq: int,
    eval_from: str,
):
    """Read only the committed Volume artifacts and validate them fail-closed."""
    vol.reload()
    cfg = frozen_audit_config(market)
    topk, nd = int(cfg["topk"]), int(cfg["nd"])

    gate_path = VOL_ROOT / "freq_experiment" / f"results_{market}.json"
    screen_path = (
        VOL_ROOT / "freq_phase_sensitivity" / f"phase_{market}_freq{freq}.json"
    )

    gate = _load_json(gate_path)
    validate_gate_payload(
        gate,
        market=market,
        freq=freq,
        eval_from=eval_from,
        topk=topk,
        nd=nd,
    )

    screen = _load_json(screen_path)
    validate_screen_payload(
        screen,
        gate,
        market=market,
        freq=freq,
        eval_from=eval_from,
        topk=topk,
        nd=nd,
    )

    return {
        "gate": gate,
        "screen": screen,
        "compact_summary": compact_audit_summary(gate, screen, freq=freq),
    }


def _run_checked(command: list[str]) -> None:
    print("[satellite-audit] $ " + " ".join(command))
    subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parent),
        check=True,
    )


@app.local_entrypoint()
def run(
    market: str,
    freq: int = 20,
    eval_from: str = "2021-01-04",
):
    """Run one frozen satellite-pool gate and 0/5/10/15 screen."""
    cfg = frozen_audit_config(market)
    topk, nd = int(cfg["topk"]), int(cfg["nd"])

    modal_bin = shutil.which("modal")
    if not modal_bin:
        raise RuntimeError("modal CLI not found in local PATH")

    print(
        f"[satellite-audit] market={market} freq={freq} "
        f"topk={topk} nd={nd} phases={SCREEN_PHASES}"
    )

    gate_cmd = [
        modal_bin,
        "run",
        "freq_experiment.py::reproducibility_gate_driver",
        "--freq",
        str(freq),
        "--eval-from",
        eval_from,
        "--market",
        market,
        "--topk",
        str(topk),
        "--nd",
        str(nd),
    ]
    _run_checked(gate_cmd)

    # The phase extension entrypoint validates the just-written gate before any
    # non-zero phase fits and preserves canonical freq_experiment.py unchanged.
    screen_cmd = [
        modal_bin,
        "run",
        "phase_audit_extend.py::extend_phase_sensitivity_driver",
        "--freq",
        str(freq),
        "--eval-from",
        eval_from,
        "--market",
        market,
        "--topk",
        str(topk),
        "--nd",
        str(nd),
        "--phases",
        ",".join(str(x) for x in SCREEN_PHASES),
    ]
    _run_checked(screen_cmd)

    bundle = read_and_validate_satellite_audit.remote(
        market=market,
        freq=freq,
        eval_from=eval_from,
    )

    gate_out_dir = Path("results/freq_experiment")
    phase_out_dir = Path("results/freq_phase_sensitivity")
    summary_out_dir = Path("results/satellite_pre_tuner")
    gate_out_dir.mkdir(parents=True, exist_ok=True)
    phase_out_dir.mkdir(parents=True, exist_ok=True)
    summary_out_dir.mkdir(parents=True, exist_ok=True)

    gate_path = gate_out_dir / f"results_{market}.json"
    screen_path = phase_out_dir / f"phase_{market}_freq{freq}.json"
    summary_path = summary_out_dir / f"{market}_freq{freq}_gate_screen.json"

    gate_path.write_text(json.dumps(bundle["gate"], indent=2, ensure_ascii=False))
    screen_path.write_text(json.dumps(bundle["screen"], indent=2, ensure_ascii=False))
    summary_path.write_text(
        json.dumps(bundle["compact_summary"], indent=2, ensure_ascii=False)
    )

    print(f"[satellite-audit] gate PASS: {gate_path}")
    print(f"[satellite-audit] screen PASS: {screen_path}")
    print(f"[satellite-audit] summary: {summary_path}")
    print(json.dumps(bundle["compact_summary"], indent=2, ensure_ascii=False))
