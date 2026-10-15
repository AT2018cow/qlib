"""Pytest path configuration for the organized `quant/` workspace."""
import sys
from pathlib import Path

QUANT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = QUANT_DIR.parent

SEARCH_PATHS = [
    REPO_ROOT,
    QUANT_DIR,
    QUANT_DIR / "code",
    QUANT_DIR / "code" / "production",
    QUANT_DIR / "code" / "execution",
    QUANT_DIR / "code" / "research" / "stage_ab",
    QUANT_DIR / "code" / "research" / "layered_search",
    QUANT_DIR / "code" / "research" / "pre_tuner",
    QUANT_DIR / "code" / "audit",
    QUANT_DIR / "code" / "ops",
    QUANT_DIR / "website",
]

for p in reversed(SEARCH_PATHS):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
