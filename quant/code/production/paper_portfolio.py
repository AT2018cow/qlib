"""Persistent paper portfolio aligned with Qlib TopkDropoutStrategy semantics.

The live cron runs before the execution day's open is known.  Therefore state
has two phases:

1. plan_signal(): store yesterday-close ranking as orders intended for the next
   execution date.
2. execute_pending(): on the next data refresh, settle those orders at the
   recorded execution day's actual open using buy/sell tradability masks.

This deliberately mirrors Qlib's default TopkDropoutStrategy selection rules:
method_buy=top, method_sell=bottom, only_tradable=False, hold_thresh=1.  The
buy list is fixed before execution; a blocked buy is NOT replaced by the next
ranked stock.  Cash sizing follows Qlib's risk_degree convention.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from csi1000_tuner_core import deterministic_score_order
from paper_accounting import close_valuation, opening_valuation, verify_ledger

STATE_VERSION = 2


class PaperPortfolio:
    """Stateful paper portfolio driven by daily cross-sectional scores."""

    def __init__(
        self,
        topk: int = 20,
        nd: int = 2,
        initial_cash: float = 1_000_000,
        open_cost: float = 0.0005,
        close_cost: float = 0.0015,
        min_cost: float = 5.0,
        risk_degree: float = 0.95,
        hold_thresh: int = 1,
        trade_unit: int = 100,
        high_open_block: Optional[float] = None,
    ):
        if topk < 1 or nd < 0 or initial_cash <= 0 or not (0 < risk_degree <= 1):
            raise ValueError("invalid paper portfolio configuration")
        self.topk = int(topk)
        self.nd = int(nd)
        self.initial_cash = float(initial_cash)
        self.open_cost = float(open_cost)
        self.close_cost = float(close_cost)
        self.min_cost = float(min_cost)
        self.risk_degree = float(risk_degree)
        self.hold_thresh = int(hold_thresh)
        self.trade_unit = int(trade_unit)
        self.high_open_block = None if high_open_block is None else float(high_open_block)
        self.cash = float(initial_cash)
        self.positions: Dict[str, dict] = {}
        self.pending_signal: Optional[dict] = None
        self.history: List[dict] = []
        self.last_execution_date: Optional[str] = None

    # ---------------------------------------------------------------- state

    def _config_dict(self) -> dict:
        return {
            "topk": self.topk,
            "nd": self.nd,
            "initial_cash": self.initial_cash,
            "open_cost": self.open_cost,
            "close_cost": self.close_cost,
            "min_cost": self.min_cost,
            "risk_degree": self.risk_degree,
            "hold_thresh": self.hold_thresh,
            "trade_unit": self.trade_unit,
            "high_open_block": self.high_open_block,
        }

    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "config": self._config_dict(),
            "cash": self.cash,
            "positions": self.positions,
            "pending_signal": self.pending_signal,
            "last_execution_date": self.last_execution_date,
            "history": self.history[-120:],
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> "PaperPortfolio":
        raw = json.loads(Path(path).read_text())
        if raw.get("version") != STATE_VERSION:
            raise ValueError(f"unsupported paper portfolio state version: {raw.get('version')}")
        pp = cls(**raw["config"])
        pp.cash = float(raw["cash"])
        pp.positions = raw.get("positions", {})
        pp.pending_signal = raw.get("pending_signal")
        pp.last_execution_date = raw.get("last_execution_date")
        pp.history = raw.get("history", [])
        return pp

    # --------------------------------------------------------- Qlib selection

    @staticmethod
    def _score_map(ranking: Iterable[Tuple[str, float]]) -> tuple[list[str], dict[str, float]]:
        rows = [(str(s), float(v)) for s, v in ranking]
        scores = dict(rows)
        ordered = deterministic_score_order(rows)
        return [str(s) for s in ordered], scores

    def decision_from_ranking(self, ranking: Iterable[Tuple[str, float]]) -> dict:
        """Return Qlib-style *planned* sell/buy lists before tradability checks."""
        ranked, scores = self._score_map(ranking)
        # Match DeterministicTopkDropoutStrategy exactly: holdings never inherit
        # dict/set iteration order, and score ties resolve by instrument asc.
        held = sorted(self.positions, key=str)
        last = [
            str(s)
            for s in deterministic_score_order(
                (s, scores.get(s, float("-inf"))) for s in held
            )
        ]
        held_set = set(last)

        n_today = self.nd + self.topk - len(last)
        not_held = [s for s in ranked if s not in held_set]
        today = not_held[:n_today] if n_today != 0 else []

        # Avoid collection-order semantics at both the TopK and dropout
        # boundaries.  Sell iteration is instrument ascending, as in the
        # audited Stage-A/Stage-B deterministic strategy.
        comb_codes = sorted(set(last).union(today), key=str)
        comb = [
            str(s)
            for s in deterministic_score_order(
                (s, scores.get(s, float("-inf"))) for s in comb_codes
            )
        ]
        bottom = set(comb[-self.nd:]) if self.nd > 0 else set()
        sell = sorted((s for s in last if s in bottom), key=str)
        n_buy = len(sell) + self.topk - len(last)
        buy = today[:n_buy] if n_buy != 0 else []
        return {"sell": sell, "buy": buy}

    # -------------------------------------------------------------- two-phase

    def plan_signal(
        self,
        signal_date: str,
        execution_date: str,
        ranking: Iterable[Tuple[str, float]],
        metadata: Optional[dict] = None,
    ) -> dict:
        if execution_date <= signal_date:
            raise ValueError("execution_date must be after signal_date")
        if self.pending_signal is not None:
            raise RuntimeError(
                f"unsettled pending signal for {self.pending_signal['execution_date']}; "
                "refusing to overwrite portfolio lineage"
            )
        rows = [[str(s), float(v)] for s, v in ranking]
        decision = self.decision_from_ranking(rows)
        self.pending_signal = {
            "signal_date": str(signal_date),
            "execution_date": str(execution_date),
            "ranking": rows,
            "planned_sell": decision["sell"],
            "planned_buy": decision["buy"],
            "metadata": metadata or {},
        }
        return {
            "signal_date": str(signal_date),
            "execution_date": str(execution_date),
            **decision,
        }

    def execute_pending(
        self,
        execution_date: str,
        open_prices: Dict[str, float],
        buy_tradable: Optional[Dict[str, bool]] = None,
        sell_tradable: Optional[Dict[str, bool]] = None,
        close_prices: Optional[Dict[str, float]] = None,
        *,
        factors: Dict[str, float],
        strict_valuation: bool = False,
        confirmed_halts: Optional[set[str]] = None,
        halt_evidence: Optional[dict] = None,
    ) -> dict:
        """Settle adjusted Qlib share units; factor is mandatory for lot-aligned buys.

        Shares/prices are in Qlib's adjusted coordinate system. A physical
        100-share trading lot represents 100 / $factor adjusted share units.
        Never assume $factor=1 for production fills.

        Failure atomicity (R10): the account is only mutated on a fully
        validated execution.  On any exception the pre-call object state is
        restored byte-for-byte (``to_dict()`` identical before and after).
        """
        snapshot = copy.deepcopy(self.__dict__)
        try:
            return self._execute_pending_impl(
                execution_date,
                open_prices,
                buy_tradable,
                sell_tradable,
                close_prices,
                factors=factors,
                strict_valuation=strict_valuation,
                confirmed_halts=confirmed_halts,
                halt_evidence=halt_evidence,
            )
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(snapshot)
            raise

    def _execute_pending_impl(
        self,
        execution_date: str,
        open_prices: Dict[str, float],
        buy_tradable: Optional[Dict[str, bool]],
        sell_tradable: Optional[Dict[str, bool]],
        close_prices: Optional[Dict[str, float]],
        *,
        factors: Dict[str, float],
        strict_valuation: bool,
        confirmed_halts: Optional[set[str]],
        halt_evidence: Optional[dict],
    ) -> dict:
        if self.pending_signal is None:
            return {"date": execution_date, "status": "no_pending"}
        pending = self.pending_signal
        if str(execution_date) != pending["execution_date"]:
            raise ValueError(
                f"pending execution date {pending['execution_date']} != supplied {execution_date}"
            )
        if self.last_execution_date and execution_date <= self.last_execution_date:
            raise ValueError("execution date is not strictly increasing")

        buy_tradable = buy_tradable or {}
        sell_tradable = sell_tradable or {}
        close_prices = close_prices or {}
        confirmed_halts = set(confirmed_halts or ())
        halt_evidence = halt_evidence or {}
        if not isinstance(factors, dict):
            raise ValueError("execution-day Qlib $factor mapping is required")
        # Reject malformed account/quotes before any mutation (R10): only
        # finite values may enter the ledger; missing opens stay blockable.
        if not math.isfinite(float(self.cash)) or self.cash < 0:
            raise ValueError("paper cash must be a finite non-negative number")
        for mapping_name, mapping in (
            ("open_prices", open_prices),
            ("close_prices", close_prices),
            ("factors", factors),
        ):
            if not isinstance(mapping, dict):
                raise ValueError(f"{mapping_name} must be a mapping")
            for sym, value in mapping.items():
                if value is None:
                    continue
                if not math.isfinite(float(value)):
                    raise ValueError(f"nonfinite {mapping_name} value for {sym!r}")
        decision = self.decision_from_ranking(pending["ranking"])
        # Reject missing/nonfinite factors *before* any sells or cash mutation.
        # Blocked buys and missing-open orders do not require an executable lot.
        for sym in decision["buy"]:
            px = open_prices.get(sym)
            if buy_tradable.get(sym, True) is False or px is None or not math.isfinite(float(px)) or px <= 0:
                continue
            factor = factors.get(sym)
            if factor is None or not math.isfinite(float(factor)) or factor <= 0:
                raise ValueError(f"missing/invalid Qlib $factor for planned buy {sym} on {execution_date}")

        # Validate all current holdings and potentially executable new buys
        # BEFORE mutating the account.  No inferred halts or stale quotes.
        if strict_valuation:
            for sym in confirmed_halts:
                evidence = halt_evidence.get(sym)
                if (not isinstance(evidence, dict) or evidence.get("date") != execution_date
                        or evidence.get("scope") != "FULL_SESSION" or not evidence.get("source")
                        or not (str(evidence.get("start_date", "")) <= execution_date
                                <= str(evidence.get("end_date", "")))):
                    raise ValueError(f"missing/invalid dated halt evidence for {sym}")
            for sym in self.positions:
                op = open_prices.get(sym)
                if (op is None or not math.isfinite(float(op)) or float(op) <= 0) and sym not in confirmed_halts:
                    raise ValueError(f"unverified missing open for held {sym}")
            candidates = {sym: dict(pos) for sym, pos in self.positions.items()}
            for sym in decision["buy"]:
                px = open_prices.get(sym)
                if buy_tradable.get(sym, True) is not False and px is not None and math.isfinite(float(px)) and float(px) > 0:
                    candidates.setdefault(sym, {"shares": 1.0, "last_price": float(px)})
            close_valuation(self.cash, candidates, close_prices, confirmed_halts=confirmed_halts)
        opening_cash = self.cash
        opening_positions = {s: {"shares": float(p["shares"])} for s, p in self.positions.items()}

        # Public forward performance is normalized to the account value at the
        # execution-day open *before* trades.  Missing opens conservatively fall
        # back to the position's last marked price.
        if strict_valuation:
            portfolio_value_pre_trade_open, opening_marks = opening_valuation(
                self.cash, self.positions, open_prices, confirmed_halts=confirmed_halts
            )
        else:
            portfolio_value_pre_trade_open = self.cash + sum(
                float(pos["shares"])
                * float(open_prices.get(sym, pos.get("last_price", pos["entry_price"])))
                for sym, pos in self.positions.items()
            )
            opening_marks = {}

        # A position bought yesterday has one completed bar of holding time today,
        # matching Qlib's default hold_thresh=1 eligibility on the next step.
        for pos in self.positions.values():
            pos["holding_days"] = int(pos.get("holding_days", 0)) + 1

        # Recompute from the stored ranking against the actual current position.
        planned_sell = decision["sell"]
        planned_buy = decision["buy"]
        executed_sell, blocked_sell = [], []
        sell_cost_total = 0.0

        for sym in planned_sell:
            pos = self.positions.get(sym)
            px = open_prices.get(sym)
            if pos is None:
                continue
            if int(pos.get("holding_days", 0)) < self.hold_thresh:
                blocked_sell.append({"instrument": sym, "reason": "hold_thresh"})
                continue
            if sell_tradable.get(sym, True) is False:
                blocked_sell.append({"instrument": sym, "reason": "not_tradable"})
                continue
            if px is None or px <= 0:
                blocked_sell.append({"instrument": sym, "reason": "missing_open"})
                continue
            trade_value = float(pos["shares"]) * float(px)
            cost = max(trade_value * self.close_cost, self.min_cost)
            self.cash += trade_value - cost
            sell_cost_total += cost
            self.positions.pop(sym)
            executed_sell.append({"instrument": sym, "shares": pos["shares"], "price": float(px), "cost": cost})

        # Qlib sizes by the planned buy count, not by the number that later pass
        # tradability.  A blocked order therefore leaves cash idle and is not
        # replaced with the next-ranked stock.
        value_per_buy = self.cash * self.risk_degree / len(planned_buy) if planned_buy else 0.0
        executed_buy, blocked_buy = [], []
        buy_cost_total = 0.0
        for sym in planned_buy:
            px = open_prices.get(sym)
            if buy_tradable.get(sym, True) is False:
                blocked_buy.append({"instrument": sym, "reason": "not_tradable"})
                continue
            if px is None or px <= 0:
                blocked_buy.append({"instrument": sym, "reason": "missing_open"})
                continue
            factor = float(factors[sym])  # validated before any state mutation
            # Parity with Qlib Exchange.round_amount_by_trade_unit():
            # (target_adjusted * factor + 0.1) // trade_unit * trade_unit / factor
            # Keep the lot count integral and the adjusted shares fractional.
            target_adjusted = value_per_buy / float(px)
            lots = int((target_adjusted * factor + 0.1) // self.trade_unit)
            if lots <= 0:
                blocked_buy.append({"instrument": sym, "reason": "rounding"})
                continue
            shares = lots * self.trade_unit / factor
            trade_value = shares * float(px)
            cost = max(trade_value * self.open_cost, self.min_cost)
            if trade_value + cost > self.cash:
                # Retain the existing fail-safe: Qlib's sizing does not reserve
                # open_cost, but the paper account may never overdraw.
                affordable = max(0.0, (self.cash - self.min_cost) / float(px))
                lots = min(lots, int((affordable * factor + 0.1) // self.trade_unit))
                while lots > 0:
                    shares = lots * self.trade_unit / factor
                    trade_value = shares * float(px)
                    cost = max(trade_value * self.open_cost, self.min_cost)
                    if trade_value + cost <= self.cash:
                        break
                    lots -= 1
            if lots <= 0 or trade_value + cost > self.cash:
                blocked_buy.append({"instrument": sym, "reason": "cash"})
                continue
            self.cash -= trade_value + cost
            buy_cost_total += cost
            self.positions[sym] = {
                "shares": shares,
                "entry_price": float(px),
                "last_price": float(px),
                "entry_date": str(execution_date),
                "cost_basis": trade_value + cost,
                "holding_days": 0,
            }
            executed_buy.append({
                "instrument": sym, "shares": shares, "price": float(px), "cost": cost,
                "factor": factor, "physical_shares": lots * self.trade_unit,
            })

        for sym, pos in self.positions.items():
            px = open_prices.get(sym)
            if px is not None and px > 0:
                pos["last_price"] = float(px)

        portfolio_value = self.cash + sum(
            float(pos["shares"]) * float(pos.get("last_price", pos["entry_price"]))
            for pos in self.positions.values()
        )

        # Both historical replay and forward publication call the same
        # valuation rule.  Legacy test/diagnostic callers may opt out, but
        # unverified marks can never appear in the public return series.
        if strict_valuation:
            portfolio_value_close, position_marks = close_valuation(
                self.cash, self.positions, close_prices, confirmed_halts=confirmed_halts
            )
            for sym, mark in position_marks.items():
                self.positions[sym]["last_price"] = mark["price"]
        else:
            for sym, pos in self.positions.items():
                px = close_prices.get(sym)
                if px is not None and math.isfinite(float(px)) and px > 0:
                    pos["last_price"] = float(px)
            portfolio_value_close = self.cash + sum(
                float(pos["shares"]) * float(pos.get("last_price", pos["entry_price"]))
                for pos in self.positions.values()
            )
            position_marks = {
                sym: {"shares": float(pos["shares"]), "price": float(pos["last_price"]),
                      "reason": "UNVERIFIED_LEGACY"}
                for sym, pos in self.positions.items()
            }
        report = {
            "date": str(execution_date),
            "signal_date": pending["signal_date"],
            "planned_sell": planned_sell,
            "planned_buy": planned_buy,
            "executed_sell": executed_sell,
            "blocked_sell": blocked_sell,
            "executed_buy": executed_buy,
            "blocked_buy": blocked_buy,
            "cash": round(self.cash, 2),
            "opening_cash": round(opening_cash, 2),
            "opening_positions": opening_positions,
            "opening_marks": opening_marks,
            "halt_evidence": {s: halt_evidence[s] for s in sorted(confirmed_halts)},
            "position_marks": position_marks,
            "valuation_verified": bool(strict_valuation),
            "portfolio_value_pre_trade_open": round(portfolio_value_pre_trade_open, 2),
            "portfolio_value": round(portfolio_value, 2),
            "portfolio_value_close": round(portfolio_value_close, 2),
            "n_positions": len(self.positions),
            "positions": sorted(self.positions),
            "cost": round(sell_cost_total + buy_cost_total, 2),
            "metadata": pending.get("metadata", {}),
        }
        if strict_valuation:
            verify_ledger(report)
        self.history.append(report)
        self.last_execution_date = str(execution_date)
        self.pending_signal = None
        return report
