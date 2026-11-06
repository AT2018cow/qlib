"""Optional open-auction notional constraints for conservative paper replay."""

import math


def available_notional(evidence, symbol, day, participation):
    if not math.isfinite(participation) or not 0 < participation <= 1:
        raise ValueError("participation must be in (0,1]")
    if not isinstance(evidence, dict) or evidence.get("date") != day or not evidence.get("source"):
        raise ValueError("dated open-auction liquidity evidence required")
    value = evidence.get("open_auction_notional", {}).get(symbol)
    if value is None or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"missing/invalid open-auction notional for {symbol}")
    return float(value) * participation
