"""Small immutable data types shared by source, overlay and gate layers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


@dataclass(frozen=True)
class Bar:
    """One raw daily bar in provider units.

    ``volume`` is shares and ``amount`` is CNY.  The overlay converts them to
    Qlib's ``volume = shares / 100 / factor`` and ``amount = CNY / 1000``.
    """

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    preclose: float | None = None
    tradestatus: int | None = None
    is_st: int | None = None

    def ohlcv_valid(self) -> bool:
        if not all(_finite(x) for x in (self.open, self.high, self.low, self.close, self.volume, self.amount)):
            return False
        if self.close <= 0 or self.volume < 0 or self.amount < 0:
            return False
        return self.high + 1e-9 >= max(self.open, self.close) and self.low - 1e-9 <= min(self.open, self.close)


@dataclass(frozen=True)
class SymbolSeries:
    """Raw and back-adjusted bars for one symbol."""

    raw: Mapping[str, Bar]
    hfq: Mapping[str, Bar]
    source: str = "sina"


@dataclass
class FetchBatch:
    """Source result with per-symbol failures retained for the gate."""

    series: dict[str, SymbolSeries] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def symbols(self) -> set[str]:
        return set(self.series)


@dataclass(frozen=True)
class Membership:
    """Official index membership snapshot."""

    market: str
    asof: str
    members: tuple[str, ...]
    weights_date: str | None = None
    raw_sha256: str = ""
    raw_bytes: int | None = None
    weights_sha256: str | None = None
    weights_bytes: int | None = None
    source_url: str = ""
    weights_source_url: str | None = None
    fetched_at_utc: str | None = None


@dataclass(frozen=True)
class CrossCheck:
    """One symbol/date comparison between the primary and fallback source."""

    symbol: str
    date: str
    fields: Mapping[str, tuple[float, float]]
    tolerance: float = 1e-6

    @property
    def status(self) -> str:
        for left, right in self.fields.values():
            scale = max(abs(left), abs(right), 1.0)
            if abs(left - right) / scale > self.tolerance:
                return "FAIL"
        return "PASS"


@dataclass
class GateReport:
    """Fail-closed validation result."""

    status: str = "PASS"
    checks: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def pass_(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": "PASS", "detail": detail})

    def skip(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": "SKIP", "detail": detail})

    def fail(self, name: str, detail: str) -> None:
        self.status = "FAIL"
        self.failures.append(f"{name}: {detail}")
        self.checks.append({"name": name, "status": "FAIL", "detail": detail})

    def require(self, condition: bool, name: str, detail: str) -> None:
        if condition:
            self.pass_(name, detail)
        else:
            self.fail(name, detail)

    def as_dict(self) -> dict:
        return {"status": self.status, "checks": list(self.checks), "failures": list(self.failures)}
