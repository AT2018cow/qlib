# Universe expansion handoff (historical; superseded)

> 这份文档原先记录 2026-09/10-02 的 Batch A/B/C、双池化与 STAR/ChiNext 早期实验。
> 其中的生产结论和部分 alpha 数字已被 corrected reproducibility audit 与后续 Stage-A/B 工作 supersede。

当前请使用：

- [21-next-conversation-handoff-star-chinext-20261008.md](21-next-conversation-handoff-star-chinext-20261008.md)：下一阶段 STAR / ChiNext 工作入口；
- [14-pre-tuner-stage-summary-20261007.md](14-pre-tuner-stage-summary-20261007.md)：两池 corrected 证据的历史来源；
- [12-pre-tuner-audit.md](12-pre-tuner-audit.md)：reproducibility / execution audit 基础。

## 当前结论摘要

- **ChiNext**：corrected reproducibility gate PASS，但冻结 top20/nd3 在 sampled phases 0/5/10/15 的 relative CAGR 全为负。旧 +11% 级 Batch C 不能作为当前生产证据。生产日更与网页展示已暂停。
- **STAR**：corrected prediction reproducibility gate 未通过；当前 alpha 结论未知。不能写成“已证伪”或“应永久关闭”。
- **CSI1000**：已完成独立 Stage-A/B 并冻结为唯一当前 production stream；STAR/ChiNext 研究不得反向改写已消费的 CSI1000 Stage-B confirmation tail。

board/date-aware limit、listing-day exemption、custom universe 与 equal-weight benchmark 的实现仍在 `board_rules.py` / `board_execution.py` / 相关测试中。
原始长版工程记录保留在 Git history，不再在当前树中重复环境特定的运行信息或过时双池部署说明。
