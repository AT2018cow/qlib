"""R08 behavioral tests: CLIs must run with no PYTHONPATH and no site injection."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPTS = (
    "quant/code/ops/build_forward_performance.py",
    "quant/code/ops/accept_forward_nav.py",
    "quant/code/audit/mvp_entry_review.py",
    "quant/code/ops/audit_paper_nav.py",
)


@pytest.mark.parametrize("script", SCRIPTS)
def test_cli_help_runs_without_pythonpath(script):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, "-S", str(ROOT / script), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"{script} failed:\n{proc.stderr}"
    assert "usage" in proc.stdout.lower()


def test_bootstrap_adds_only_repo_owned_paths():
    code_root = Path(__file__).resolve().parents[1] / "code"
    sys.path.insert(0, str(code_root))
    from quant_bootstrap import REPO_ROOT, TRUSTED_QUANT_PATHS

    for path in TRUSTED_QUANT_PATHS:
        assert str(path).startswith(str(REPO_ROOT)), f"untrusted path injected: {path}"
