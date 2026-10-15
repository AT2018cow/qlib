"""Static quotation coverage for a dynamic index-member trading strategy.

F1: A dynamic membership dictionary passed to Qlib Exchange/D.features removes
quotes after a name leaves the index. Existing shares then become untradeable
and keep their last mark, although exit from the index is not a suspension.

This helper is deliberately ONLY for the exchange's price/valuation universe.
The strategy's dated signal universe must remain the original point-in-time
index membership; do not expose future index members as today's buy candidates.
"""
from __future__ import annotations


def execution_quote_codes(market: str, start_time: str, end_time: str, *, provider=None) -> list[str]:
    """Get a stable union of index members present in the backtest interval.

    A list passed to Exchange as `codes` lets Qlib query the full date span of
    each stock's *underlying* price bins. A dynamic D.instruments(market) dict
    instead clips those bins to historical index-membership intervals.

    This changes neither the frozen signal nor the eligible daily buy universe.
    The union is used only to price/hold/sell names the strategy has already
    selected while they were eligible.

    Args:
        market: Qlib instrument-universe name, for example "csi1000".
        start_time: First execution day, inclusive.
        end_time: Last execution day, inclusive.
        provider: Optional injected data facade for pure unit tests.

    Returns:
        Lexically sorted unique stock codes with any membership overlapping
        the interval.

    Raises:
        ValueError: For an empty or invalid quote universe.
    """
    if not isinstance(market, str) or not market.strip():
        raise ValueError("market must be a nonempty instrument-universe name")
    if not isinstance(start_time, str) or not isinstance(end_time, str) or start_time > end_time:
        raise ValueError("expected nonempty start_time <= end_time")
    if not start_time or not end_time:
        raise ValueError("execution dates are required")

    if provider is None:
        from qlib.data import D
        provider = D

    # IMPORTANT: as_list=True is what removes membership-date restrictions.
    # The Qlib Exchange accepts static lists and D.features then queries each
    # member across the entire backtest interval (not only its index tenure).
    members = provider.list_instruments(
        provider.instruments(market),
        start_time=start_time,
        end_time=end_time,
        freq="day",
        as_list=True,
    )
    if members is None:
        raise ValueError(f"no instrument data returned for market {market!r}")
    codes = sorted(set(members))
    if not codes or any(not isinstance(code, str) or not code for code in codes):
        raise ValueError(f"invalid execution quote universe for {market!r}")
    return codes
