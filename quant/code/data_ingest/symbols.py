"""Symbol normalization shared by all provider adapters."""

from __future__ import annotations

import re

_SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SZ_PREFIXES = ("000", "001", "002", "003", "300", "301", "302")
_BJ_PREFIXES = ("4", "8", "920")

_CODE_RE = re.compile(r"^(?:(SH|SZ|BJ)[.]?)?(\d{6})$", re.IGNORECASE)


class SymbolError(ValueError):
    """Raised for an unsupported security code."""


def normalize_code(value: str) -> str:
    """Return a Qlib-style ``SH600519`` / ``SZ000001`` / ``BJ920000`` code."""
    text = str(value).strip().upper().replace("_", ".")
    if "." in text and not text.startswith(("SH", "SZ", "BJ")):
        # BaoStock form such as ``sh.600519``.
        left, right = text.split(".", 1)
        if left in ("SH", "SZ", "BJ"):
            text = left + right
    match = _CODE_RE.match(text)
    if not match:
        raise SymbolError(f"unsupported security code: {value!r}")
    prefix, digits = match.groups()
    if prefix is None:
        if digits.startswith(_SH_PREFIXES):
            prefix = "SH"
        elif digits.startswith(_SZ_PREFIXES):
            prefix = "SZ"
        elif digits.startswith(_BJ_PREFIXES):
            prefix = "BJ"
        else:
            raise SymbolError(f"cannot infer exchange for code: {value!r}")
    return f"{prefix}{digits}"


def to_sina_symbol(code: str) -> str:
    """Return Sina's ``sh600519`` form."""
    normalized = normalize_code(code)
    return normalized[:2].lower() + normalized[2:]


def to_baostock_symbol(code: str) -> str:
    """Return BaoStock's ``sh.600519`` form."""
    normalized = normalize_code(code)
    return normalized[:2].lower() + "." + normalized[2:]


def exchange(code: str) -> str:
    """Return ``SH``, ``SZ`` or ``BJ``."""
    return normalize_code(code)[:2]
