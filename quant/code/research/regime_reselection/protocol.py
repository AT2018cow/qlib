"""Protocol constants for the 2026-11 balanced strategy reselection.

Frozen before any candidate result is inspected (pre-registration).  The
period split is deliberately different from the 2026-11-02 draft plan:

- Discovery/stress: 2021-02-18 .. 2024-02-05 (binding bear market) and the
  full 2018-01-02 .. 2024-12-31 cycle for finalists.
- Holdout: 2025-01-02 .. 2026-09-30 (sealed; October 2026 excluded).
- 2026-10 is a *known-event stress report only*; it may veto via a
  pre-registered disaster bound but must not be used for ranking.
"""

from __future__ import annotations

DISCOVERY_FULL = ("2018-01-02", "2024-12-31")
DISCOVERY_BEAR = ("2021-02-18", "2024-02-05")
HOLDOUT = ("2025-01-02", "2026-09-30")
KNOWN_EVENT_STRESS = ("2026-10-08", "2026-10-30")
RETRAIN_EVERY_SESSIONS = 20
SESSIONS_PER_YEAR = 238

# Date-aware all-in cost schedule: (effective_from, buy_bps, sell_bps, min_fee).
# Pre-2023-08-28 A-share stamp duty was 0.1%; afterwards 0.05%.  The sell leg
# also carries commission (~2.5bp) and a small slippage allowance.
COST_SCHEDULE = (
    ("2018-01-01", 5.0, 15.0, 5.0),
    ("2023-08-28", 5.0, 10.0, 5.0),
)
LEGACY_FLAT_COST = (5.0, 15.0, 5.0)  # for comparability with earlier research

CANDIDATES = {
    "S01": "现役 Top10/Drop1（对照）",
    "S02": "现役 Top20/Drop2",
    "S03": "10d 互补 Top10/Drop1",
    "S04": "初代基线 23b92de0 Top20/Drop2",
    "S05": "官方 2d Top50/Drop5",
    "S06": "官方 2d Top20/Drop2",
    "S07": "官方 2d 月度 Top10",
    "S08": "现役月度全换仓 Top10",
    "S09": "现役月度缓冲 Top10/Buffer20",
    "S10": "基线月度全换仓 Top10",
    "S11": "双创板块上限 40%",
    "S12": "相关性簇上限 + 反波动率加权",
    "S13": "纯低波防御 Top20 月度",
    "S14": "模型 + 低波复合 Top10/Drop1",
    "S15": "70/30 攻守哑铃",
    "S16": "趋势/波动率门控",
    "S17": "三市场等权/风险平价组合（新增）",
    "S18": "账户级回撤熔断（新增）",
}

# Gate names used by the runner/report.  Thresholds live here so the report and
# code cannot drift.
GATES = {
    "D1_mdd_improvement_pp": 12.0,
    "D1_benchmark_mdd_slack_pp": 3.0,
    "D2_2018_excess_floor_pp": -2.0,
    "D3_benchmark_excess_cagr_pp": 8.0,
    "D4_post_best_year_excess_pp": 4.0,
    "D5_bootstrap_quantile": 0.05,
    "D7_stress10_cagr_floor": 0.0,
    "D7_stress20_cagr_floor": 0.0,
    "D8_adv_participation": 0.05,
    "H1_bull_retention": 0.65,
    "H2_information_ratio": 0.5,
    "SHADOW_WEEKS": 4,
    "SHADOW_TRACKING_ERROR": 0.02,
}
