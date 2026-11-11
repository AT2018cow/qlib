"""Optional open-auction notional constraints for conservative paper replay."""

import math

LIQUIDITY_POLICY_VERSION = "missing_quote_fail_closed"


def liquidity_rule_contract() -> dict:
    """Canonical missing-quote and optional auction-liquidity behavior."""
    return {
        "liquidity_policy_version": LIQUIDITY_POLICY_VERSION,
        "missing_quote": "FAIL_CLOSED",
        "auction_evidence": {
            "required_fields": ["date", "source", "open_auction_notional"],
            "participation_range": "0<participation<=1",
            "missing_symbol": "FAIL_CLOSED",
            "negative_or_nonfinite_notional": "FAIL_CLOSED",
        },
    }


def available_notional(evidence, symbol, day, participation):
    if not math.isfinite(participation) or not 0 < participation <= 1:
        raise ValueError("participation must be in (0,1]")
    if not isinstance(evidence, dict) or evidence.get("date") != day or not evidence.get("source"):
        raise ValueError("dated open-auction liquidity evidence required")
    value = evidence.get("open_auction_notional", {}).get(symbol)
    if value is None or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"missing/invalid open-auction notional for {symbol}")
    return float(value) * participation
