# 45 — CSI1000 Audit Residual & Strategy Decision：独立新会话交接（2026-10-11）

> **工作线 B / 审计遗留与策略决策交接入口。** 本文不替代 [42](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md) 的 Audit Closure Register；不阻塞 [44](44-csi1000-mvp-forward-operations-handoff-20261011.md) 的每日 MVP 运行。目标是将遗留风险整理为可判定的证据，再决定**是否需要**新策略研究，而非预设重调参。

## 冻结事实与禁止事项

- 当前 canonical `stage_b_winner`、lineage `stage_b_winner_canonical` 保持不变。不得因为 corrected 历史排名自动切换 baseline 或重训。
- 424 个交易日、1,700 笔冻结订单的 baseline phase0 独立账本 `PASS_RESEARCH_ACCOUNTING_ONLY`；只证明研究账务算术，不证明真实可成交、OOS 或策略最优。
- 9 个公告支持的历史停牌估值已接受；4 笔 ST/复牌 SELL 的真实可成交性 `BLOCKED_MARKET_EXECUTION_NOT_CERTIFIED`，历史 benchmark 外部价格 `DEFERRED`；其余 54 格逐格账本复核 `CLOSED / NOT REQUIRED FOR MVP`。
- 已消费的 Stage-B 2025–2026 区间 corrected 55 格诊断排名：baseline 第 1、旧 winner 第 10。这是**事后诊断**，不是新的 OOS，不允许以其为依据静默选优或再调参。
- 新的真正前向观察从 **2026-10-12** 开始；旧数据不得回填为 forward。
- 不重启全面历史 halt-by-halt 审计、55 格账本扩审或大规模参数搜索，除非出现 42 号文档规定的具体重新启动触发条件。

## 工作目标：先分类，再定向证据，最后决策

### B1. 建立 Audit Residual Register

逐项记录 `issue_id / 原审计状态 / 风险机制 / 对研究收益或排名影响 / 证据文件+SHA / 重启条件 / owner / 结论 / 对 MVP 影响`。优先：

1. **ST/复牌四笔 SELL**：逐笔确认冻结订单、当日规则与已知限制，评估“假设不能成交”对策略收益/换手/排名的敏感性；不得把没有盘口/真实成交数据的情景称为成交认证。保留已关闭的公告估值证据，不重复逐日审计。
2. **Benchmark**：独立价格/复权/交易日对齐检查，区分 `saved benchmark returns` 与真正独立认证的相对收益；必要时只修正对外表述，不直接改生产模型。
3. **winner vs corrected baseline**：明确为什么 corrected 账务后排名变化，区分代码 bug、数据口径、执行假设、随机性与 consumed-tail 选优偏差；给出可重复的诊断，不使用历史排名直接重选。
4. **PIT/ST/IPO/涨跌停/流动性/冲击成本**：将当前 MVP 的 P0 数据正确性（转交 A 线立即修）与实盘准备阶段 P2 认证分离。未经独立验证的项目保持 `DEFERRED`，不伪装 PASS。

### B2. 风险分级与触发条件

- **P0**：发现当前数据泄漏、PIT/label/时钟/模型 lineage 错误、会影响当日榜单；立即通知 A 线阻断/修复。
- **P1**：有具体证据可能实质改变研究收益、排名或策略可信度；进行范围明确的定向敏感性/复核，提交证据。
- **P2**：仅在自动下单/实盘准备时才必须完整认证的流动性、冲击、全历史 IPO/ST；记录限制，不阻塞研究 MVP。
- 每项必须写出 `CLOSED / DEFERRED / TRANSFERRED / REOPENED_WITH_TRIGGER`，以及可验证的重启条件；不得只因“还有疑问”就重开已关闭的历史审计。

### B3. 策略决策关口（先冻结标准）

候选动作：`KEEP_CANONICAL`、`FORWARD_SHADOW_COMPARE`、`NEW_RESEARCH_PROTOCOL`、`PROPOSE_SWITCH`。形成决策前需：

1. 列明旧 winner 与 baseline 的固定 candidate/config/hash，保证未来并行预测**同时生成并不可回填**。
2. 预注册前向观察的最小样本期、指标、交易/成本假设、风险阈值、失效条件和多重比较控制；样本不足则 `INSUFFICIENT_FORWARD_EVIDENCE`。
3. 说明哪些审计发现足以推翻现役策略的正确性，哪些只是扩大不确定性。
4. 仅当预设标准满足、并有未被候选选择消费的新证据时，才提议切换；新调参必须使用新的时间切分和隔离测试集，另立 PR/研究合同，**不得改写已有历史报告**。
5. 决策由明确评审 PR/文档承载；任何 `PROPOSE_SWITCH` 不等于自动改生产。

## 交付与验收

- 一份带 SHA/来源/风险分级/触发条件的 Audit Residual Register。
- 对 ST 四笔、benchmark、排名差异的定向证据与可重复脚本/负控；明确哪些仍未认证。
- 一份预注册的 `Strategy Decision Protocol`：指标、前向窗口、成本假设、判断阈值、决策选项和回滚。
- 当前时点明确结论可为 `KEEP_CANONICAL_PENDING_FORWARD`，而不是必须重调参。
- 只读保护既有 audit JSON、Stage-B 冻结报告、生产 config、历史信号；除非独立 PR 和明确批准，不修改这些文件。

## 与 A 线接口

A 线继续每日 MVP 和 forward 记录。B 线可以要求冻结并行观察的候选，但必须在 A 线稳定后通过单独审查集成；不得回填。若 B 线发现当前链路 P0，提交可复现问题给 A 线立即修复；B 线的历史 P1/P2 不能阻塞 A 线研究榜单。

## 新会话第一条消息（可直接复制）

请以 GitHub 仓库 `AT2018cow/qlib` 的 `docs/experiments/45-csi1000-audit-residual-strategy-decision-handoff-20261011.md` 作为本会话工作线 B 交接入口，并严格遵守 42 号文档的 Audit Closure Register。先重新核对 main、冻结的 Stage-B 证据、canonical 模型和审计状态；直接建立 Audit Residual Register，优先对四笔历史 ST/复牌 SELL、benchmark 独立价格、旧 winner 与 corrected baseline 排名差异做**定向**复核和影响评估，记录 SHA、负控、风险分级及重启条件。随后拟定可执行且预注册的 forward shadow comparison 与 Strategy Decision Protocol，明确 KEEP / SHADOW / NEW_RESEARCH / PROPOSE_SWITCH 的触发证据。不要重启全面历史逐日审计、扩 55 格、使用已消费的 Stage-B 区间再调参选优、修改当前 canonical 或回填 forward；当前证据不足时明确保持 canonical、等待 2026-10-12 起的新前向数据。与 A 线只通过明确的 P0 缺陷和冻结证据交接。
