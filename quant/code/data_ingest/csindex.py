"""Official CSIndex constituent and weight snapshots."""

from __future__ import annotations

import time
from io import BytesIO
from typing import Callable

import pandas as pd
import requests

from data_ingest.manifest import sha256_bytes
from data_ingest.model import Membership
from data_ingest.symbols import normalize_code

CSINDEX_BASE = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile"
MARKETS = {
    "csi300": ("000300", 300),
    "csi500": ("000905", 500),
    "csi1000": ("000852", 1000),
}


class CSIndexError(RuntimeError):
    """Raised when the official constituent file is missing or malformed."""


def _default_fetch(url: str) -> bytes:
    """Fetch one CSIndex file with bounded retries.

    The official OSS host is occasionally slow (observed 60s+ read timeouts
    from Modal while the same file returned in ~9s locally).  A single hard
    timeout aborts the whole overlay build, so retry with a longer read
    timeout before failing closed.
    """
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(10, 120))
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _as_date(value) -> str:
    text = str(value).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _as_code(value) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) > 6:
        digits = digits[-6:]
    return normalize_code(digits.zfill(6))


def _parse_cons(content: bytes) -> tuple[str, list[str]]:
    # The official XLS has a bilingual header row.  Read positionally rather
    # than depending on a localized column name.
    frame = pd.read_excel(BytesIO(content))
    if frame.shape[1] < 6:
        raise CSIndexError(f"constituent file has too few columns: {frame.shape[1]}")
    dates = sorted({_as_date(value) for value in frame.iloc[:, 0].tolist()})
    if len(dates) != 1:
        raise CSIndexError(f"constituent file must contain one date, got {dates[:5]}")
    members = sorted({_as_code(value) for value in frame.iloc[:, 4].tolist()})
    return dates[0], members


def _parse_weights(content: bytes) -> str:
    frame = pd.read_excel(BytesIO(content))
    if frame.shape[1] < 6:
        raise CSIndexError(f"weight file has too few columns: {frame.shape[1]}")
    dates = sorted({_as_date(value) for value in frame.iloc[:, 0].tolist()})
    if len(dates) != 1:
        raise CSIndexError(f"weight file must contain one date, got {dates[:5]}")
    return dates[0]


def fetch_membership(
    market: str,
    *,
    fetch: Callable[[str], bytes] | None = None,
    require_weights: bool = True,
) -> Membership:
    """Fetch one official index snapshot and its content hashes."""
    if market not in MARKETS:
        raise CSIndexError(f"unsupported market: {market}")
    symbol, expected = MARKETS[market]
    fetcher = fetch or _default_fetch
    cons_url = f"{CSINDEX_BASE}/cons/{symbol}cons.xls"
    content = fetcher(cons_url)
    asof, members = _parse_cons(content)
    if len(members) != expected:
        raise CSIndexError(f"{market} expected {expected} members, got {len(members)}")
    weights_url = f"{CSINDEX_BASE}/closeweight/{symbol}closeweight.xls"
    weights_date = None
    weights_sha = None
    if require_weights:
        weights_content = fetcher(weights_url)
        weights_date = _parse_weights(weights_content)
        weights_sha = sha256_bytes(weights_content)
    return Membership(
        market=market,
        asof=asof,
        members=tuple(members),
        weights_date=weights_date,
        raw_sha256=sha256_bytes(content),
        weights_sha256=weights_sha,
        source_url=cons_url,
        weights_source_url=weights_url if require_weights else None,
        raw_bytes=len(content),
        weights_bytes=len(weights_content) if require_weights else None,
        fetched_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def fetch_all_memberships(
    markets: tuple[str, ...] = ("csi300", "csi500", "csi1000"),
    *,
    fetch: Callable[[str], bytes] | None = None,
    require_weights: bool = True,
) -> dict[str, Membership]:
    return {market: fetch_membership(market, fetch=fetch, require_weights=require_weights) for market in markets}
