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

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

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
        high_open_block: float = 0.05,
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
        self.high_open_block = float(high_open_block)
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
        # Qlib sorts pred_score descending before taking today's candidates.
        rows.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in rows], dict(rows)

    def decision_from_ranking(self, ranking: Iterable[Tuple[str, float]]) -> dict:
        """Return Qlib-style *planned* sell/buy lists before tradability checks."""
        ranked, scores = self._score_map(ranking)
        held = list(self.positions)
        last = sorted(held, key=lambda s: scores.get(s, float("-inf")), reverse=True)
        held_set = set(last)

        n_today = self.nd + self.topk - len(last)
        not_held = [s for s in ranked if s not in held_set]
        today = not_held[:n_today] if n_today != 0 else []

        # pandas Index.union is unique; sort combined symbols by score descending.
        comb = list(dict.fromkeys(last + today))
        comb.sort(key=lambda s: scores.get(s, float("-inf")), reverse=True)
        bottom = set(comb[-self.nd:]) if self.nd > 0 else set()
        sell = [s for s in last if s in bottom]
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

        # A position bought yesterday has one completed bar of holding time today,
        # matching Qlib's default hold_thresh=1 eligibility on the next step.
        for pos in self.positions.values():
            pos["holding_days"] = int(pos.get("holding_days", 0)) + 1

        # Recompute from the stored ranking against the actual current position.
        decision = self.decision_from_ranking(pending["ranking"])
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
            shares = int(value_per_buy / float(px) / self.trade_unit) * self.trade_unit
            if shares <= 0:
                blocked_buy.append({"instrument": sym, "reason": "rounding"})
                continue
            trade_value = shares * float(px)
            cost = max(trade_value * self.open_cost, self.min_cost)
            if trade_value + cost > self.cash:
                # Qlib's generator does not reserve open_cost in sizing.  The
                # paper layer stays fail-safe and refuses an unaffordable fill.
                affordable = int((self.cash - self.min_cost) / float(px) / self.trade_unit) * self.trade_unit
                shares = max(0, affordable)
                trade_value = shares * float(px)
                cost = max(trade_value * self.open_cost, self.min_cost) if shares > 0 else 0.0
            if shares <= 0 or trade_value + cost > self.cash:
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
            executed_buy.append({"instrument": sym, "shares": shares, "price": float(px), "cost": cost})

        for sym, pos in self.positions.items():
            px = open_prices.get(sym)
            if px is not None and px > 0:
                pos["last_price"] = float(px)

        portfolio_value = self.cash + sum(
            float(pos["shares"]) * float(pos.get("last_price", pos["entry_price"]))
            for pos in self.positions.values()
        )
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
            "portfolio_value": round(portfolio_value, 2),
            "n_positions": len(self.positions),
            "positions": sorted(self.positions),
            "cost": round(sell_cost_total + buy_cost_total, 2),
            "metadata": pending.get("metadata", {}),
        }
        self.history.append(report)
        self.last_execution_date = str(execution_date)
        self.pending_signal = None
        return report
