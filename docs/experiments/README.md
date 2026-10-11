# 实验文档导览

> **2026-10-12 独立历史策略择优研究提案（待评审、待费用授权）：** [53 — CSI1000 有预算上限的分层搜索合同](53-csi1000-budgeted-layered-strategy-search-contract-20261012.md) 规定了四层搜索、Modal 的训练/信号缓存、预算与止损、最终产品选择及新会话启动指令。该提案是**单独研究工作线**，不改动现有 canonical、不宣称已运行、不把消费过的 Stage-B 历史样本称为 OOS；只有审核批准后才启动费用和计算。

> **2026-10-11：CSI1000 当前唯一最新交接入口是 [42 — MVP handoff after ledger pass](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md)。**
>
> 历史审计已按 Audit Closure Register 正式收口。后续 CSI1000 工作不继续扩展历史审计、不对已消费 confirmation tail 重新排序调参；当前只推进每日数据新鲜度、PIT 股票池与标签成熟、Top20 lineage、paper 状态、ST/停牌风险、网站展示和端到端 smoke。
>
> [43 — daily publication contract](43-csi1000-mvp-daily-publication-contract-20261011.md) 是 42 的**实现 runbook**，不是新的 handoff，也不替代 42。
>
> 22/23/24 及更早交接继续保留作历史上下文，但不再是 CSI1000 当前操作入口。

## 当前项目状态

- **CSI1000**：canonical Stage-B winner 保持冻结；Audit Closure 后不做事后重选或重新调参，当前进入 forward research MVP。
- **ChiNext / 创业板**：旧模型的每日 production 更新与网页展示已暂停；历史 artifacts 和研究代码保留。
- **STAR / 科创板**：corrected reproducibility gate 尚未通过，当前 alpha 结论为**未知**，不是“已证伪”。
- **执行协议**：T 日收盘形成信号，T+1 开盘执行；Top20 / Drop2；board/date-aware price limits；无 5% high-open overlay。
- **重训协议**：2016-01-01 起 expanding train，252-session validation，20-session purge，每 20 个交易日重训，生产原点 2026-09-18。
- **生产 freshness**：CSI1000 只在前一真实交易日数据完整可验证时发布；陈旧数据 fail closed。
- **网站**：当前公开页面仅展示 CSI1000；新 MVP sidecar 会同时暴露 freshness / PIT / Label20 / lineage / paper / risk 状态，旧 artifact 不伪装成新合同 PASS。

## 当前权威文档

| 文档 | 用途 |
|---|---|
| [42-csi1000-mvp-handoff-after-ledger-pass-20261011.md](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md) | **唯一最新 CSI1000 交接入口**：Audit Closure 后的 forward MVP 范围、禁止项与验收条件 |
| [43-csi1000-mvp-daily-publication-contract-20261011.md](43-csi1000-mvp-daily-publication-contract-20261011.md) | 42 的实现 runbook：每日 fail-closed 合同、风险边界、smoke 与失败恢复 |
| [22-handoff-star-chinext-research-20261008.md](22-handoff-star-chinext-research-20261008.md) | **研究对话入口**：STAR reproducibility、ChiNext 新模型、冻结/OOS 约束 |
| [23-web-ui-handoff-20261008.md](23-web-ui-handoff-20261008.md) | **网页对话入口**：深色响应式、Top20/走势图/forward 累计收益、Pages 发布与视觉验收 |
| [24-csi1000-independent-sharpe-audit-handoff-20261009.md](24-csi1000-independent-sharpe-audit-handoff-20261009.md) | **独立核验对话入口**：Sharpe、IR、账户逐日收益、交易数据/成本与时间完整性审计（PENDING） |
| [21-next-conversation-handoff-star-chinext-20261008.md](21-next-conversation-handoff-star-chinext-20261008.md) | 综合历史交接与研究背景（请优先使用 22/23 分工） |
| [20-csi1000-stage-b-winner-canonical-promotion-20261008.md](20-csi1000-stage-b-winner-canonical-promotion-20261008.md) | CSI1000 Stage-B winner canonical promotion 决策 |
| [19-csi1000-stage-b-final-audit-forward-contract-20261008.md](19-csi1000-stage-b-final-audit-forward-contract-20261008.md) | Stage-B 最终审计；其中 shadow deployment topology 已由 20 supersede |
| [18-next-conversation-handoff-stage-b-20261007.md](18-next-conversation-handoff-stage-b-20261007.md) | Stage-B 启动前冻结合同与资源约束（历史但仍可用于追溯） |
| [17-csi1000-stage-a-expanded-screen-runbook-20261007.md](17-csi1000-stage-a-expanded-screen-runbook-20261007.md) | Stage-A 80-candidate 扩展执行记录 |
| [16-csi1000-stage-a-tuning-contract-20261007.md](16-csi1000-stage-a-tuning-contract-20261007.md) | Stage-A 调参合同 |
| [14-pre-tuner-stage-summary-20261007.md](14-pre-tuner-stage-summary-20261007.md) | STAR/ChiNext corrected evidence 的关键历史入口 |
| [12-pre-tuner-audit.md](12-pre-tuner-audit.md) | corrected reproducibility / execution audit 基础 |

## 历史文档

`01`–`15`、旧 `HANDOVER*.md`、Batch A/B/C 和早期 universe-expansion 记录保留用于工程追溯，
但其中以下内容不得当作当前事实：

- 旧 CSI1000 +11% 级 rolling baseline 是当前“最终模型”；
- ChiNext 与 CSI1000 同等成熟并应双池 production；
- STAR 已被证明无 alpha；
- production cron 仍应发布两个 pool；
- 旧 baseline paper state 应继续给 Stage-B winner 使用。

这些结论均已被 2026-10-07/08 的 corrected audit、Stage-A/B 和 canonical promotion supersede。

## Public-repository hygiene

本仓库是公开代码仓库。文档不记录真实访问凭据、私有 workspace/profile 名称、账户余额、个人目录或其他非必要身份信息。
部署凭据只通过托管平台的凭据管理功能配置，不进入仓库文档。
网站只公开研究方法、当前状态与必要风险说明；精确 lineage/hash 如需审计，以 versioned research docs / source manifests 为准。

## 免责声明

本仓库全部内容仅用于学术研究与实验性工程验证，不构成投资建议。
历史回测、Stage-B 结果、模型分数和 paper artifacts 均不能保证未来收益。
