# `quant/data/` — 策略回测数据库与线上信号归档字典

本目录集中存放我们自己的全部策略实验数据、日频回测账本（`fixed_report.parquet`）、统一策略排行榜以及线上每日发布的信号文件，与 Qlib 框架自身文件彻底隔离。

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
└── signals/                               # 线上每日自动发布的 CSI1000 信号四件套 (CSV + Chart + PaperPortfolio + Metadata)
```
