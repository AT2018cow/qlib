"""Read-only provider adapter for pending paper execution and coordinate gates."""

from execution_limits import compute_limit_masks


def build_paper_execution_context(
    data_dir,
    market: str,
    calendar: list[str],
    asof: str,
    *,
    lineage: str = "canonical",
    state_path,
    listing_dates_fn,
) -> dict:
    """Read paper state without mutating it and prepare data for pending execution.

    daily_standalone runs before publication gating, so it must never mutate
    portfolio state.  It only returns the exact historical open/tradability
    context needed by daily_cron after the signal has passed the gate.
    """
    import artifact_io as _json
    import numpy as _np
    import pandas as _pd
    from qlib.data import D

    if not state_path.exists():
        return {"status": "no_state", "execution_date": None}
    raw = _json.loads(state_path.read_text())
    pending = raw.get("pending_signal")
    if not pending:
        return {"status": "no_pending", "execution_date": None}
    execution_date = str(pending["execution_date"])
    if execution_date > str(asof):
        return {"status": "awaiting_data", "execution_date": execution_date}
    if execution_date not in calendar:
        raise RuntimeError(f"paper pending execution date {execution_date} not in provider calendar")

    from coordinate_contract import validate_coordinate_quotes

    anchored = {}
    for symbol, position in raw.get("positions", {}).items():
        position = dict(position)
        if not position.get("coordinate_anchor"):
            day = raw.get("last_execution_date")
            for report in reversed(raw.get("history", [])):
                mark = report.get("position_marks", {}).get(symbol, {})
                if mark.get("reason") == "EXCHANGE_CLOSE":
                    day = report["date"]
                    break
            if not day:
                raise ValueError(f"missing coordinate date for held {symbol}")
            position["coordinate_anchor"] = {"date": day, "close": position["last_price"]}
        anchored[symbol] = position
    historical_quotes = {}
    for day in sorted({p["coordinate_anchor"]["date"] for p in anchored.values()}):
        wanted = [s for s, p in anchored.items() if p["coordinate_anchor"]["date"] == day]
        prior = D.features(wanted, ["$close", "$factor"], start_time=day, end_time=day, freq="day")
        for idx, quote in prior.iterrows():
            symbol = str(idx[0] if isinstance(idx, tuple) else idx)
            historical_quotes[symbol] = {
                "date": day,
                "close": float(quote["$close"]),
                "factor": float(quote["$factor"]),
            }
    validate_coordinate_quotes(anchored, historical_quotes)

    symbols = set(raw.get("positions", {}))
    symbols.update(pending.get("planned_sell", []))
    symbols.update(pending.get("planned_buy", []))
    if not symbols:
        return {
            "status": "ready",
            "execution_date": execution_date,
            "open_prices": {},
            "close_prices": {},
            "factors": {},
            "buy_tradable": {},
            "sell_tradable": {},
        }

    df = D.features(
        sorted(symbols),
        ["$open", "Ref($close,1)", "$close", "$factor"],
        start_time=execution_date,
        end_time=execution_date,
        freq="day",
    )
    rows = {}
    for idx, row in df.iterrows():
        inst = str(idx[0] if isinstance(idx, tuple) else idx)
        rows[inst] = row

    insts = sorted(symbols)
    from csi1000_mvp_contract import (
        REFERENCE_ST_POLICY,
        VERIFIED_ST_POLICY,
        validate_verified_st_snapshot,
    )

    pending_metadata = pending.get("metadata") or {}
    execution_st_policy = pending_metadata.get("execution_st_policy")
    snapshot = pending_metadata.get("execution_st_snapshot") or {}
    if execution_st_policy == REFERENCE_ST_POLICY:
        # Reference P&L deliberately ignores ST risk: NEVER convert a missing
        # feed to an empty *verified* ST list. Continue enforcing price limits,
        # factors, real quotes and independent full-session halt evidence.
        if execution_date < "2026-10-12":
            raise RuntimeError("reference ST policy cannot apply before forward inception")
        st_symbols = set()
    else:
        if execution_date >= "2026-10-12":
            if execution_st_policy != VERIFIED_ST_POLICY:
                return {"status": "unverified_preopen_st", "execution_date": execution_date}
            st_symbols = validate_verified_st_snapshot(snapshot, execution_date)
        else:
            st_symbols = set(snapshot.get("st_symbols", []))
    opens, prevs, close_na, factors = [], [], [], []
    open_prices = {}
    close_prices = {}
    factor_by_symbol = {}
    for inst in insts:
        row = rows.get(inst)
        op = float(row["$open"]) if row is not None and _pd.notna(row["$open"]) else _np.nan
        prev = float(row["Ref($close,1)"]) if row is not None and _pd.notna(row["Ref($close,1)"]) else _np.nan
        close = float(row["$close"]) if row is not None and _pd.notna(row["$close"]) else _np.nan
        factor = float(row["$factor"]) if row is not None and _pd.notna(row["$factor"]) else _np.nan
        opens.append(op)
        prevs.append(prev)
        close_na.append(not _np.isfinite(close))
        factors.append(factor)
        if _np.isfinite(op):
            open_prices[inst] = op
        if _np.isfinite(close):
            close_prices[inst] = close
        if _np.isfinite(factor) and factor > 0:
            factor_by_symbol[inst] = factor

    # Only a dated full-session, independent suspension record can justify
    # carrying a previously held mark. An isolated missing OHLC quote is not
    # enough; never infer a halt from a provider NaN.
    held_missing = {sym for sym in raw.get("positions", {}) if sym not in open_prices or sym not in close_prices}
    halt_evidence = {}
    if held_missing:
        inconsistent = [sym for sym in held_missing if (sym in open_prices) != (sym in close_prices)]
        if inconsistent:
            raise RuntimeError(f"partial held quote is not a certified halt: {inconsistent}")
        import datetime as _datetime
        import akshare as _ak
        from paper_halt_evidence import certify_full_session_halts

        try:
            suspension_rows = _ak.stock_tfp_em(date=execution_date.replace("-", "")).to_dict("records")
            halt_evidence = certify_full_session_halts(
                execution_date,
                held_missing,
                suspension_rows,
                fetched_utc=_datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            )
        except Exception as exc:
            raise RuntimeError(
                f"cannot certify missing held quotes for {execution_date}: {sorted(held_missing)}"
            ) from exc

    listing = listing_dates_fn(data_dir, insts)
    limit_buy, limit_sell = compute_limit_masks(
        insts,
        [execution_date] * len(insts),
        opens,
        prevs,
        close_na,
        high_open_block=None,
        listing_dates=listing,
        calendar=calendar,
        factors=factors,
        st_symbols=st_symbols,
    )
    return {
        "status": "ready",
        "execution_date": execution_date,
        "execution_st_policy": execution_st_policy,
        "open_prices": open_prices,
        "close_prices": close_prices,
        "factors": factor_by_symbol,
        "confirmed_halts": sorted(halt_evidence),
        "halt_evidence": halt_evidence,
        "buy_tradable": {inst: not bool(v) for inst, v in zip(insts, limit_buy)},
        "sell_tradable": {inst: not bool(v) for inst, v in zip(insts, limit_sell)},
    }
