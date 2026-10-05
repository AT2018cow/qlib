"""Stateful paper portfolio for daily rankings (R28).

Transforms the daily Top-20 ranking into an executable portfolio with persistent
state (cash, positions, holding days), producing actionable buy/sell orders.
Validates against Qlib's TopkDropoutStrategy under the same execution assumptions.

Usage:
    pp = PaperPortfolio(topk=20, nd=2)
    report = pp.step(date, ranking, open_prices, prev_closes)
    pp.save(path)

The TopkDropout algorithm (verified against qlib/contrib/strategy/signal_strategy.py):
1. Sort current holdings by today's score (descending).
2. Candidates to buy = top-ranked stocks NOT in holdings (enough to fill topk).
3. Combine holdings + candidates, sort by score.
4. Sell the nd lowest-scoring held stocks (from the combined list, only stocks
   actually held, subject to tradability).
5. Buy enough candidates to fill topk after selling.
6. Equal-weight allocation among all held positions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

STATE_VERSION = 1


class PaperPortfolio:
    """Stateful paper trading portfolio driven by daily Top-N rankings."""

    def __init__(self, topk: int = 20, nd: int = 2, initial_cash: float = 1_000_000,
                 open_cost: float = 0.0005, close_cost: float = 0.0015,
                 min_cost: float = 5.0, high_open_block: float = 0.05):
        self.topk = topk
        self.nd = nd
        self.initial_cash = initial_cash
        self.open_cost = open_cost
        self.close_cost = close_cost
        self.min_cost = min_cost
        self.high_open_block = high_open_block
        self.cash = initial_cash
        self.positions: Dict[str, dict] = {}  # {symbol: {shares, entry_price, entry_date, cost_basis}}
        self.history: List[dict] = []
        self.last_step_date: Optional[str] = None

    # ------------------------------------------------------------------ state

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps({
            "version": STATE_VERSION,
            "cash": self.cash,
            "positions": self.positions,
            "last_step_date": self.last_step_date,
            "topk": self.topk, "nd": self.nd,
            "initial_cash": self.initial_cash,
            "history": self.history[-60:],  # keep recent history
        }, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str) -> "PaperPortfolio":
        s = json.loads(Path(path).read_text())
        pp = cls(topk=s["topk"], nd=s["nd"], initial_cash=s["initial_cash"])
        pp.cash = s["cash"]
        pp.positions = s["positions"]
        pp.last_step_date = s.get("last_step_date")
        pp.history = s.get("history", [])
        return pp

    # ------------------------------------------------------------------ step

    def step(self, date: str, ranking: List[Tuple[str, float]],
             open_prices: Dict[str, float], prev_closes: Dict[str, float],
             tradable: Optional[Dict[str, bool]] = None) -> dict:
        """Process one trading day and return a report.

        Args:
            date: trading date (YYYY-MM-DD)
            ranking: [(symbol, score)] sorted by score descending (from the model)
            open_prices: {symbol: today's open price}
            prev_closes: {symbol: previous trading day's close}
            tradable: {symbol: bool} — False = blocked (limit/suspension); None = all tradable

        Returns:
            Daily report dict with sells, buys, portfolio state, P&L.
        """
        if self.last_step_date and date <= self.last_step_date:
            raise ValueError(f"date {date} <= last_step_date {self.last_step_date}")

        tradable = tradable or {}
        scores = dict(ranking)
        held = sorted(self.positions.keys(), key=lambda s: scores.get(s, -1e9), reverse=True)
        held_set = set(held)

        # 1. Build buy candidates: high-scoring stocks not held
        cand_all = [s for s, _ in ranking if s not in held_set and s in open_prices]
        n_to_fill = self.topk - len(held)
        today_buy_pool = cand_all[: max(self.nd + n_to_fill, 0)]

        # 2. Combined list (holdings + candidates), sorted by score
        comb = sorted([s for s in held if s in scores] + today_buy_pool,
                       key=lambda s: scores.get(s, -1e9), reverse=True)

        # 3. Sell: the nd lowest-scored held stocks from comb (bottom method)
        comb_held = [s for s in comb if s in held_set]
        sell_candidates = comb_held[-self.nd:] if len(comb_held) >= self.nd else comb_held
        sells = []
        for s in sell_candidates:
            if tradable.get(s, True) is False:
                continue  # blocked (limit down / suspension)
            sells.append(s)

        # 4. Buy: fill topk after selling
        n_buy = min(self.topk - len(held) + len(sells), len(today_buy_pool))
        buys = []
        for s in today_buy_pool:
            if len(buys) >= n_buy:
                break
            if tradable.get(s, True) is False:
                continue  # blocked (high open / limit up / suspension)
            buys.append(s)

        # 5. Execute sells (at open price, with close_cost)
        for s in sells:
            pos = self.positions.pop(s, None)
            if pos is None:
                continue
            price = open_prices.get(s)
            if price is None or price <= 0:
                self.positions[s] = pos  # can't sell, restore
                continue
            trade_val = pos["shares"] * price
            cost = max(trade_val * self.close_cost, self.min_cost)
            self.cash += trade_val - cost

        # 6. Execute buys (at open price, with open_cost)
        if buys:
            n_targets = len(self.positions) + len(buys)
            alloc = self.cash / max(n_targets, 1) if self.cash > 0 else 0
            for s in buys:
                price = open_prices.get(s)
                if price is None or price <= 0 or alloc <= 0:
                    continue
                shares = int(alloc / price / 100) * 100  # A-share lot size
                if shares <= 0:
                    continue
                trade_val = shares * price
                cost = max(trade_val * self.open_cost, self.min_cost)
                if trade_val + cost > self.cash:
                    continue
                self.cash -= trade_val + cost
                self.positions[s] = {
                    "shares": shares, "entry_price": price,
                    "entry_date": date, "cost_basis": trade_val + cost,
                }

        # 7. Compute portfolio value
        pv = self.cash + sum(
            p["shares"] * open_prices.get(s, p["entry_price"])
            for s, p in self.positions.items()
        )
        report = {
            "date": date, "cash": round(self.cash, 2),
            "portfolio_value": round(pv, 2),
            "n_positions": len(self.positions),
            "sells": sells, "buys": buys,
            "held": sorted(self.positions.keys()),
        }
        self.history.append(report)
        self.last_step_date = date
        return report

    # -------------------------------------------------------------- validation

    def portfolio_value_series(self, prices_by_date: Dict[str, Dict[str, float]]) -> List[Tuple[str, float]]:
        """Reconstruct the daily portfolio value from history."""
        out = []
        for h in self.history:
            d = h["date"]
            prices = prices_by_date.get(d, {})
            pv = h["cash"] + sum(
                self.positions.get(s, {}).get("shares", 0) * prices.get(s, 0)
                for s in h["held"]
            ) if d == self.last_step_date else h["portfolio_value"]
            out.append((d, pv))
        return out
