# `quant/docs/` — 研发与生产文档总索引

本目录按研发演进阶段整理了全部实验报告、审计记录与生产合同：

**上线后20项复核**：[`逐项结论与本地修补验收`](07-code-review/2026-11-06-post-rollout-review-resolution.md)区分确认缺陷、审核结论修正与外部证据缺口；922 项 Python、50 个子用例、30 项前端通过，60 份网站日账本及 519 份历史报告对账无误。此次改动已提交 `5ca4b892`，并已部署主应用 v31/网站应用 v6；F19 外部认证仍待完成；[`发布候选说明`](07-code-review/2026-11-06-post-rollout-release-candidate.md)列出具体范围与剩余发布事实。

**统一每日调度**：[`07:00 主跑 / 08:00 补跑`](07-code-review/2026-11-06-unified-daily-schedule.md)记录已完成的合并部署及两入口实际调用验收；网站收益随主跑和补跑统一更新，云端已确认取消独立 08:15 调度。

**最新扩展层审查与修复**：[`计算与逻辑审查`](07-code-review/2026-11-06-extension-calculation-and-logic-audit.md)记录修复前 Q01–Q16；[`2026-11-06 修复与验收`](07-code-review/2026-11-06-extension-repair-acceptance.md)记录实现、回归、380 份历史账本影响及外部证据待办。代码已修复；后续生产部署与网站收益修正见下方“网站模拟收益上线”。

**使用前复验与择优**：[`已批准方案`](07-code-review/2026-11-06-prelive-revalidation-plan.md)、[`输入与候选登记`](07-code-review/2026-11-06-prelive-execution-register.json)、[`执行与结果`](07-code-review/2026-11-06-prelive-revalidation-results.md)、[`具体使用与部署审核包`](07-code-review/2026-11-06-limited-use-review-package.md)。已完成 72 个登记场景与 10/08 起四账户回填；实盘使用包保留为历史审核记录。用户随后明确本轮用途为三个独立 1M 网站模拟账户，已批准并完成下方网站部署，未实施券商账户迁移或下单。

**网站模拟收益上线**：[`10/08 起连续账户修正与部署记录`](07-code-review/2026-11-06-website-rollout.md)记录已完成的源码绑定、生产备份与恢复演练、三市场核算及线上部署验收；网站已使用自 10/08 起、各 100 万元的独立历史重建账户替换旧收益。

**四项复审修复**：[`修复与验收`](07-code-review/2026-11-06-review-followup-fixes.md)记录行情身份强校验、共享源码身份 v2、归因对照数量隔离和按报告本金审计；最终 836 项 Python 测试、25 项前端测试通过，511 份报告审计无误报，原策略择优结论不变。旧使用包的源码清单保留为历史冻结记录，部署前须重新绑定当前版本。

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
   - [`03-why-colleague-alpha158-csi300-bank-basket-was-not-selected-20261030.md`](08-current-window-backtest/03-why-colleague-alpha158-csi300-bank-basket-was-not-selected-20261030.md)：同事银行/价值组合的 10 月收益核验、默认 Alpha158 排序复现检查，以及“搜索空间/Top10 截断/风格暴露”三层原因。
   - [`04-csi300-csi500-csi1000-bear-market-drawdown-20261102.md`](08-current-window-backtest/04-csi300-csi500-csi1000-bear-market-drawdown-20261102.md)：2021-02-18～2024-02-05 熊市压力测试；当前三套策略的熊市最大回撤与基准对照。
   - [`05-balanced-strategy-reselection-plan-20261102.md`](08-current-window-backtest/05-balanced-strategy-reselection-plan-20261102.md)：2018–2026 全周期信号脊柱 + 12 套候选策略（日频/月度/低波防御/板块上限/趋势门控）的均衡重选方案。
