# 44 — CSI1000 MVP Forward Operations：独立新会话交接（2026-10-11）

> **工作线 A / MVP 交接入口。** 本文仅负责真实每日 CSI1000 研究选股、paper、网站及运维验收。原始决策与 Audit Closure 以 [42](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md) 为准；实现运行手册见 [43](43-csi1000-mvp-daily-publication-contract-20261011.md)。不要把 B 线的历史审计变成 A 线阻塞项。

## 已知基线（启动新会话必须重新核对）

- PR #58 `feat: start CSI1000 forward MVP publication contract` 已合并到 `main`，merge SHA `9316751309e35e4906d1d544118cbed4d190001f`；以新会话查到的实时 `main` 为准。
- 合并后 GitHub Actions：MVP contract #8、tuner core #117、Pages deploy #60 均 success。**这些不是 Modal 在线证明**。
- 合并的代码提供 provider freshness、数据日 PIT CSI1000=1000、20-session label maturity、冻结 canonical lineage、Top20/full-ranking 双 lineage、paper 日期绑定、ST/停牌未知状态、网站 MVP contract 展示和负控测试。
- 目前已核实的旧信号为使用日 2026-10-09、数据日 2026-10-08。旧文件缺少新 sidecar 时应标记 legacy/incomplete，不能回填制造 PASS。
- 生产 canonical 仍为 `stage_b_winner` / `stage_b_winner_canonical`。**不得自动切换、重新调参或真实下单**。
- Modal 部署、cron 当前健康和新代码真实运行**尚未直接验收**。前向观测只能从 **2026-10-12** 开始，不能接上历史 Stage-B 曲线。

## 本线目标及边界

交付每日可追溯、失败可见的 Top20 研究服务，完整的 pending→settled/blocked paper 循环，以及真实网站与 forward NAV。遇到当前 PIT/标签/时钟/数据错误，立即 fail-closed 并修复；不在本线扩大历史审计或做策略优选。

## 优先任务与验收证据

1. **Git/线上基线**：核对最新 `main`、最近两次 `results/signals`、Pages 实际部署内容、provider 最近完整交易日；记录 commit SHA、时间（北京时间）及 artifact SHA256。
2. **Modal 授权环境**：确认 app `qlib-cn-daily-a10g` 的实际 deployed SHA、schedule、最近运行、失败日志、secret 可用性。无法直接访问时明确 `NOT_DIRECTLY_VERIFIED`，请求有权限的账户执行 [43](43-csi1000-mvp-daily-publication-contract-20261011.md) 中的 `modal app info/history/logs`、`daily_standalone` preflight、reviewed deploy 和 `daily_cron` publication smoke，并回传脱敏日志。
3. **第一条真实前向 publication**：2026-10-12 的 usage-day 发布应使用 2026-10-09 最后完整 bar（前提：可信交易日历确认），生成同日期不可变四件套：`2026-10-12_top20_lgb158.csv`、`2026-10-12_chart.json`、`2026-10-12_paper_portfolio.json`、`2026-10-12_mvp_metadata.json`。校验 provider fingerprint、PIT、Label20、模型 hash、paper、网站日期一致。**2026-10-12 未到/无真实 bar 时标 pending，不模拟造数**。
4. **paper 第二段**：等待 2026-10-12 实际开盘/收盘 bar 经 provider 可用后才结算对应 pending；检查重复发布/重复结算、停牌、涨跌停、缺价、无法成交、持仓报价 union、现金/持仓/NAV 连续。明确模拟与真实成交区别。
5. **网页/forward**：实际打开 Pages 检查最新日、模型身份、PIT/Label、风险未知、paper 状态、contract fail 的警告；forward NAV 从 2026-10-12 的真实 paper 估值开始，不回填历史。
6. **持续运行**：至少连续数个交易日检查 cron、数据新鲜度、幂等、异常恢复；每次记录 Git/Modal/provider/页面的证据，发现 P0 即停止错误发布。

## 最终交付

- 每日 `date → provider snapshot/hash → PIT/label → model lineage → Top20 → paper → Git commit → Pages` 可复核证据表。
- 一次真实首段 E2E smoke 和一次后续 bar 到齐后的结算 smoke；负路径与回滚/重试证据。
- 明确 `PASS / BLOCKED / NOT_VERIFIED / PENDING_MARKET_DATA`，不可把静态 CI 当线上 PASS。
- MVP 完成定义：可持续每日运行、失败可见、paper 结算可追踪、网站风险清楚；不要求历史审计全数认证或模型重选。

## 与 B 线接口

若出现可能改变策略结论的审计风险，向 B 线提交具体问题和冻结证据；若出现**当前** PIT/标签/时钟/报价严重错误，A 线立即阻断并修复。B 线的历史诊断不得直接改 A 线 canonical、历史 artifact 或 forward 记录。

## 新会话第一条消息（可直接复制）

请以 GitHub 仓库 `AT2018cow/qlib` 的 `docs/experiments/44-csi1000-mvp-forward-operations-handoff-20261011.md` 作为本会话唯一工作线 A 交接入口，并以 42 号 Audit Closure 和 43 号 publication runbook 为约束。先重新核对当前 main、PR #58 合并后的 Actions、最新信号、真实网站和可访问的 Modal 部署/cron；随后直接推进 CSI1000 MVP 的真实运行验收：provider freshness、PIT=1000、Label20、冻结模型 lineage、2026-10-12 起的 Top20/metadata/paper pending 发布、后续 bar 到齐后的 paper settlement、forward NAV 和网站 E2E。请按 PASS/BLOCKED/NOT_VERIFIED/PENDING_MARKET_DATA 留下 SHA、日志和测试证据；若无 Modal 权限，明确说明并给出授权账户要执行的精确命令，不能把 CI 代替线上验收。不要扩历史审计、重新调参、切换 canonical、回填前向数据或执行真实交易。
