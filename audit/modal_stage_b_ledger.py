"""On-demand Modal CPU entrypoint for the read-only CSI1000 ledger audit.

Usage: modal run audit/modal_stage_b_ledger.py --candidate baseline --phases phase0
Never deploy or schedule this app. Existing data Volume is mounted only for reads.
"""
from __future__ import annotations

import json
from pathlib import Path

import modal

REPO_DIR = "/root/qlib"
VOLUME = modal.Volume.from_name("qlib-cn-data", create_if_missing=False)
SNAPSHOT = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("build-essential")
    .pip_install("numpy==1.26.4", "pandas==2.2.3", "pyarrow==17.0.0",
                 "Cython", "setuptools-scm")
    .add_local_dir(".", remote_path=REPO_DIR, copy=True,
                   ignore=lambda path: (
                       any(part in str(path) for part in
                           (".git", ".venv", "__pycache__", "mlruns"))
                       or str(path).endswith((".so", ".cpp"))
                   ))
    .run_commands("cd /root/qlib && pip install . --no-build-isolation")
)

app = modal.App("qlib-stage-b-independent-ledger-check", image=image)


@app.function(cpu=4, memory=16384, timeout=3600, volumes={"/vol": VOLUME})
def audit_once(candidate: str, phases: str) -> dict:
    """Read original frozen Volume data; never write to the mounted Volume."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ledger-") as temp_dir:
        result_dir = Path(temp_dir) / "result"
        proc = subprocess.run([
            "python", "-m", "audit.verify_stage_b_fixed_ledger",
            "--repo-root", REPO_DIR,
            "--provider-uri", "/vol/cn_data",
            "--provider-snapshot",
            f"/vol/csi1000_stage_b/{SNAPSHOT}/provider_snapshot.json",
            "--candidate", candidate,
            "--phases", phases,
            "--output-dir", str(result_dir),
        ], cwd=REPO_DIR, check=False, timeout=3300,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # On mismatches the verifier exits nonzero AFTER writing its report.
        # Always return that evidence to the local CLI before /tmp vanishes.
        accounting = result_dir / "independent_ledger.json"
        failure = result_dir / "audit_failure.json"
        if accounting.is_file():
            result = json.loads(accounting.read_text())
        elif failure.is_file():
            result = json.loads(failure.read_text())
        else:
            result = {"status": "RUN_FAILED_WITHOUT_REPORT"}
        result["modal_command_exit_code"] = proc.returncode
        if proc.returncode != 0:
            result["status"] = "BLOCKED_OR_FAILED_NOT_PASS"
            result["diagnostic_output_tail"] = proc.stdout[-2000:]
        return result


@app.local_entrypoint()
def main(candidate: str = "baseline", phases: str = "phase0",
         output: str = "/tmp/csi1000-independent-ledger.json"):
    if candidate not in ("baseline", "runnerup", "both") or phases not in ("phase0", "all"):
        raise ValueError("unsupported audit scope")
    dest = Path(output).expanduser().resolve()
    if dest.exists() or not dest.parent.is_dir():
        raise ValueError("output path must not exist and parent must exist")
    result = audit_once.remote(candidate, phases)
    dest.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"Audit JSON written locally: {dest}; cells={len(result.get('cells', []))}; "
          f"exit={result['modal_command_exit_code']}")
    if result["modal_command_exit_code"] != 0:
        raise RuntimeError(f"Audit did not pass; inspect the retained local JSON: {dest}")
