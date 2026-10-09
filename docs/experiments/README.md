# 实验文档导览

> **2026-10-09：按任务拆分为三个相互独立的对话入口。**
>
> - **[22 — STAR/ChiNext 模型研究交接](22-handoff-star-chinext-research-20261008.md)**：科创板 reproducibility 和创业板新模型研究。
> - **[23 — 网站/UI 交接](23-web-ui-handoff-20261008.md)**：深色响应式网页、图表和 forward performance 展示。
> - **[24 — CSI1000 Sharpe 独立核验交接](24-csi1000-independent-sharpe-audit-handoff-20261009.md)**：从逐日报告独立复算 Sharpe、收益、IR、波动率与执行/账户完整性；**核验尚未完成**。
>
> [21 — 综合历史交接](21-next-conversation-handoff-star-chinext-20261008.md) 继续保留详细研究背景，但不再是两条并行工作的共同操作入口。
> 旧 handover、Batch A/B/C 与已 supersede 的双池生产描述只作历史记录。

## 当前项目状态

- **CSI1000**：Stage-A / Stage-B 已完成并冻结；Stage-B winner 已晋升为唯一 canonical production profile。
- **ChiNext / 创业板**：旧模型的每日 production 更新与网页展示已暂停；历史 artifacts 和研究代码保留。
- **STAR / 科创板**：corrected reproducibility gate 尚未通过，当前 alpha 结论为**未知**，不是“已证伪”。
- **执行协议**：T 日收盘形成信号，T+1 开盘执行；Top20 / Drop2；board/date-aware price limits；无 5% high-open overlay。
- **重训协议**：2016-01-01 起 expanding train，252-session validation，20-session purge，每 20 个交易日重训，生产原点 2026-09-18。
- **生产 freshness**：CSI1000 只在前一真实交易日数据完整可验证时发布；陈旧数据 fail closed。
- **网站**：当前公开页面仅展示 CSI1000；方法论页描述当前冻结 Stage-B 协议，ChiNext 标记为 paused。

## 当前权威文档

| 文档 | 用途 |
|---|---|
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
