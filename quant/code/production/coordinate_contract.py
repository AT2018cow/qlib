"""Reject provider revisions that change the coordinate basis of held assets.

This gate never guesses a corporate action or credits cash. An operator may
replay a versioned account from frozen data after a detected revision.
"""

import math


def validate_coordinate_quotes(positions, quotes):
    for symbol, position in positions.items():
        anchor = position.get("coordinate_anchor")
        if not isinstance(anchor, dict):
            raise ValueError(f"missing dated coordinate anchor for {symbol}")
        quote = quotes.get(symbol)
        if not isinstance(quote, dict) or quote.get("date") != anchor.get("date"):
            raise ValueError(f"missing historical coordinate quote for {symbol}")
        for field in ("close", "factor"):
            old = anchor.get(field)
            if old is None and field == "factor":
                continue  # Legacy state may establish a close-only anchor.
            new = quote.get(field)
            if (
                old is None
                or new is None
                or not math.isfinite(float(old))
                or not math.isfinite(float(new))
                or min(float(old), float(new)) <= 0
                or not math.isclose(float(old), float(new), rel_tol=5e-7, abs_tol=1e-10)
            ):
                raise ValueError(f"provider coordinate revision for held {symbol} ({field}); replay required")
