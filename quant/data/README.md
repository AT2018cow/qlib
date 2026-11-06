# `quant/data/` — 策略回测数据库与线上信号归档字典

本目录集中存放我们自己的全部策略实验数据、日频回测账本（`fixed_report.parquet`）、统一策略排行榜以及线上每日发布的信号文件，与 Qlib 框架自身文件彻底隔离。

**2026-11-06 执行规则证据范围：** `strategy_catalog/`、`round1_stage_ab/`、`round1_audit_evidence/`、`round2_layered_search/`、`pre_tuner_experiments/`、`csi300_csi500_research/` 和 `current_window_backtest/` 中的旧指标默认标记为 `LEGACY_EXECUTION_CONTRACT_NOT_REPLAYED`。它们保留各自生成时的成交与数量规则，不能解释为全部已经使用当前板块数量规则重新成交回放。算术重算不等于成交重放；当前有效证据与例外见[执行规则状态清单](execution_evidence_status.json)及[复核记录](../docs/07-code-review/2026-11-06-post-rollout-review-resolution.md)。

---

## 目录结构一览

```text
quant/data/
├── README.md                              # 本数据字典
├── strategy_catalog/                      # ★【首选入口】统一策略主数据库（CSV / JSON / Parquet）
│   ├── strategy_master_table.csv          #   132 套策略全量排行榜（含超参、TopK/DropN、各相位净收益、CAGR、Sharpe、MaxDD、2026-07/08表现、账本路径）
│   ├── strategy_master_table.json         #   同上，JSON 结构化格式
│   ├── l4_finalists_5phase_summary.csv    #   五大核心策略在全 5 个重训相位 (phase00/04/06/10/15) 的详细表现对照表
│   ├── monthly_returns_2025_2026.csv      #   主力策略与中证1000指数在 2025-01 ~ 2026-09 (21个月) 的逐月真实净收益率
│   └── daily_nav_matrix_424d.parquet      #   主力策略全相位 424 个交易日累计净值矩阵 (起点=1.0)
│
├── round2_layered_search/                 # ★ 第二轮分层搜索 (P0 -> L1 -> L2 -> L3 -> L4) 完整回测账本归档
│   ├── p0_gate/                           #   P0：Baseline 与旧 Winner 的 F1 修正基准核验 (4 套日频账本)
│   ├── l1_132strategies_2phases/          #   L1-A/B/C：11 个模型 × 12 种 TopK/DropN 规则 × 2 相位 = 264 套完整日频账本
│   ├── l2_model_screen/                   #   L2：8 个候选模型超参筛选结果与报告
│   ├── l3_retrain_horizon/                #   L3：重训周期 (10天/40天) 与标签周期实验 (109 次训练筛选 + 4 套完整账本)
│   └── l4_five_phase_final/               #   L4：4 大决赛策略 × 全 5 个重训相位 = 20 套终审完整日频账本 + 终审决策书
│
├── round1_stage_ab/                       # 第一轮 Stage-A (74 超参候选) 与 Stage-B (10 相位) 历史实验结果
├── round1_audit_evidence/                 # 第一轮独立审计证据 (含 28 模型 × 2 相位 = 56 套 Stage-B F1 修正重跑账本)
├── pre_tuner_experiments/                 # 早期调仓频率、重训相位归因、科创/创业板卫星池摸底数据
├── current_window_backtest/               # 2026-10-16 ~ 10-29 三市场候选策略同期回测（研究记录，非生产切换）
└── signals/                               # 线上每日自动发布的 CSI1000 信号四件套 (CSV + Chart + PaperPortfolio + Metadata)
```

### `signals/` 收益文件语义（2026-11-06 起）

- `csi*_website_performance.json`：**2026-10-08 开盘、每市场 100 万现金**的独立网站模拟账户；使用当前 profile 的因果重建，未核实逐日历史 ST、官方除权参考价和开盘集合竞价流动性。
- `csi*_forward_performance.json`、`csi*_backfill_performance.json`：该网站模拟账户的**兼容别名**，内容与 `*_website_performance.json` 相同；**不再表示** canonical live-paper 前向曲线或旧 BACKFILL prefix。
- canonical live-paper 账户仍以 `csi*_paper_portfolio.json` 为账本证据，使用 `audit_paper_nav` 审计。该工具默认从第一条非 `BACKFILL_RESEARCH` 工件起算，也可用 `--start YYYY-MM-DD` 显式指定 live 段起点；`BACKFILL_RESEARCH` 工件不会混入 live 对账。
- 旧文档中把 `*_forward_performance.json` 描述为“2026-10-12 起 live paper”的表述属于历史合同；当前公开网站使用 10/08 独立模拟账户，不能再据此读取 canonical live-paper 收益。

### `current_window_backtest/`

2026-10-30 执行的三市场、13 单元短窗口回测；仅用于同期相对比较，不是生产 paper NAV。
报告见 [`quant/docs/08-current-window-backtest/01-csi1000-csi300-csi500-current-window-candidate-backtest-20261030.md`](../docs/08-current-window-backtest/01-csi1000-csi300-csi500-current-window-candidate-backtest-20261030.md)。

- `2026-10-16_2026-10-29_candidate_results.json`：逐单元日收益/净值结果
- `2026-10-16_2026-10-29_candidate_summary.csv`：跨市场汇总（含外部指数与生产 paper 校验列）
- `2026-10-08_2026-10-29_csi300_october_results.json`：早期 bank-heavy baseline 应用于 CSI300 的 2026 年 10 月逐日结果
- `2026-10-08_2026-10-29_csi300_october_summary.csv`：上述回测的跨构造汇总
- `2026-09-30_csi300_colleague_basket_diagnostic.json`：同事 7 只价值/银行组合的收益、2026-09-30 排序与默认 Alpha158 复现检查
- `2026-10-08_2026-10-29_colleague_basket_summary.csv`：上述 7 只股票的逐票收益与各模型排名
- `2021-02-18_2024-02-05_bear_market_summary.json` / `.csv`：三市场当前策略在上一轮熊市的压力回撤与基准对照（20 日滚动块拼接近似）
