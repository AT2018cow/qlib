"""Standalone import bootstrap for quant extension CLI scripts (R08).

Several ``quant/code/ops`` and ``quant/code/audit`` CLIs inserted only the
repository root on ``sys.path`` and then imported modules that actually live
under ``quant/code/production`` etc.  They appeared to work because the CI
workflow, ``quant/tests/conftest.py`` and an editable-install ``.pth`` file all
inject the quant directories; a genuinely clean ``python script.py`` failed
with ``ModuleNotFoundError``.

Scripts should call :func:`ensure_quant_import_paths` (after inserting
``quant/code`` on the path themselves) so direct execution works without any
environment variable.  Only trusted repository-owned directories are added.
"""

from __future__ import annotations

import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
QUANT_ROOT = CODE_ROOT.parent
REPO_ROOT = QUANT_ROOT.parent

TRUSTED_QUANT_PATHS = (
    REPO_ROOT,
    QUANT_ROOT,
    CODE_ROOT,
    CODE_ROOT / "production",
    CODE_ROOT / "execution",
    CODE_ROOT / "research" / "stage_ab",
    CODE_ROOT / "research" / "layered_search",
    CODE_ROOT / "research" / "csi300_csi500",
    CODE_ROOT / "research" / "pre_tuner",
    CODE_ROOT / "audit",
    CODE_ROOT / "ops",
    QUANT_ROOT / "website",
)


def ensure_quant_import_paths() -> None:
    """Insert the trusted quant directories at the front of ``sys.path``."""
    for path in reversed(TRUSTED_QUANT_PATHS):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
