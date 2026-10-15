"""Dated third-party full-session suspension evidence for missing paper quotes.

The only automatic carry-forward permitted is for a held stock with BOTH
unavailable open and close and an explicit dated full-session suspension row.
A data-provider hole, intraday halt, missing end date or empty feed is never
treated as an exchange halt. This module performs no network requests.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date


SOURCE = "akshare.stock_tfp_em / Eastmoney dated suspension listing"


def _ymd(value):
    s = str(value).strip().replace("/", "-")[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def certify_full_session_halts(day, missing_symbols, raw_rows, *, fetched_utc):
    if _ymd(day) != day:
        raise ValueError("execution day must be ISO date")
    if not fetched_utc:
        raise ValueError("halt feed observation timestamp required")
    missing = set(missing_symbols)
    out = {}
    for raw in raw_rows:
        code = str(raw.get("代码", "")).strip().split(".")[0].zfill(6)
        matched = [s for s in missing if s[2:] == code]
        if not matched:
            continue
        start = _ymd(raw.get("停牌时间"))
        end = _ymd(raw.get("停牌截止时间"))
        duration = str(raw.get("停牌期限", ""))
        if (not start or not end or not (start <= day <= end)
                or any(x in duration for x in ("盘中", "临时", "分钟", "小时"))):
            continue
        for symbol in matched:
            if symbol in out:
                raise ValueError(f"ambiguous multiple halt records for {symbol}")
            canonical = {
                "code": code, "start_date": start, "end_date": end,
                "duration": duration, "reason": str(raw.get("停牌原因", "")),
            }
            fingerprint = hashlib.sha256(json.dumps(
                canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            out[symbol] = {
                "date": day, "symbol": symbol, "source": SOURCE,
                "scope": "FULL_SESSION", "start_date": start, "end_date": end,
                "duration": duration, "reason": canonical["reason"],
                "fetched_utc": str(fetched_utc), "row_sha256": fingerprint,
            }
    unresolved = missing.difference(out)
    if unresolved:
        raise ValueError(f"missing independent full-session suspension evidence: {sorted(unresolved)}")
    return out
