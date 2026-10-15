"""Audit package bridge: exposes both quant/code/audit and quant/code/research/layered_search under `audit.*`."""
from pathlib import Path

_layered = str(Path(__file__).resolve().parent.parent / "research" / "layered_search")
if _layered not in __path__:
    __path__.append(_layered)
