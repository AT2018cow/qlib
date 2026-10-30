# `quant/docs/` — 研发与生产文档总索引

本目录按研发演进阶段整理了全部实验报告、审计记录与生产合同：

1. **[`00-strategy-handbook/`](00-strategy-handbook/) — 策略选型与历史回测全景手册（首选必读）**
   - [`STRATEGY_ATLAS.md`](00-strategy-handbook/STRATEGY_ATLAS.md)：三大主力策略家族（`c98856b4 Top10/Drop1`、`23b92de0 Baseline`、`4e908173 旧生产`）、全实验前五强、2025–2026 逐月（含 2026 年 7–8 月）真实净收益对照、132 套策略淘汰规律及历史数据调取指南。
2. **[`01-foundation-and-fixes/`](01-foundation-and-fixes/) — 阶段 1：底层审计修复与基础机制摸底（Doc 01 ~ 15）**
   - 标签成熟度隔离（purge）、20 交易日滚动重训、A 股涨跌停与整手执行归因、调仓频率与重训相位敏感度分析、科创/创业板卫星池摸底。
3. **[`02-round1-stage-ab/`](02-round1-stage-ab/) — 阶段 2：第一轮 Stage-A（74候选）与 Stage-B（10相位）超参搜索（Doc 16 ~ 20）**
   - Stage-A 4 折超参筛选、Stage-B 10 相位验证合同与早期 `stage_b_winner` 晋升记录。
4. **[`03-independent-audit-f1-f6/`](03-independent-audit-f1-f6/) — 阶段 3：独立账本审计与 F1 出池股断流修复（Doc 24 ~ 39）**
   - Stage-B 原始账本独立复算、发现并修复“成分股调出中证1000后缺少报价导致仓位冻结”的 F1 关键缺陷、F5/F6 SHA 门禁与 28 模型全量修正重跑。
5. **[`04-mvp-and-website/`](04-mvp-and-website/) — 阶段 4：线上 MVP 发布合同、模拟组合净值验收与前端看板（Doc 21 ~ 23, 40 ~ 49）**
   - 每日信号四件套强校验合同、模拟组合统一会计核算、前向净值独立审计与 GitHub Pages 静态站点设计。
6. **[`05-round2-layered-search/`](05-round2-layered-search/) — 阶段 5：第二轮预算受控分层策略搜索与终局报告（Doc 50 ~ 60）**
   - `P0 -> L1 -> L2 -> L3 -> L4` 完整实验方案、L1（132套策略）持仓规则筛选、L2 超参搜索、L3 重训周期验证、L4 五相位终审及 [`60-csi1000-two-round-strategy-research-final-report-20261015.md`](05-round2-layered-search/60-csi1000-two-round-strategy-research-final-report-20261015.md) 终局技术报告。
7. **[`08-current-window-backtest/`](08-current-window-backtest/) — 当前短窗口候选策略同期回测**
   - [`01-csi1000-csi300-csi500-current-window-candidate-backtest-20261030.md`](08-current-window-backtest/01-csi1000-csi300-csi500-current-window-candidate-backtest-20261030.md)：2026-10-16 ~ 10-29 三市场、13 个模型/组合构造单元的同期净收益、生产 paper 校验、历史风险收益权衡与“不因 10 天切换”的决策边界。
   - [`02-csi300-october-2026-early-baseline-backtest-20261030.md`](08-current-window-backtest/02-csi300-october-2026-early-baseline-backtest-20261030.md)：第一版网站 bank-heavy baseline 应用于 CSI300 的 2026 年 10 月回测（16 个交易日）及与当前 CSI300 生产的对照。
