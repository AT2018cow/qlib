# 四项复审修复证据

对应[修复与验收说明](../../../docs/07-code-review/2026-11-06-review-followup-fixes.md)。这是修复后的独立版本，不覆盖原策略回放、注册表或历史收据，不授予实盘准入。

| 文件 | 含义 |
|---|---|
| `source-manifests.json` | 当前回放/训练的共享实现身份 v2；训练另覆盖 Alpha158 YAML |
| `smoke/`、`smoke-results.json` | 三市场 × 三成本的真实本地 smoke；逐笔记录、日账本与输入 SHA 均保留 |
| `parity.json` | 9 个新场景与原长回放前缀一致；显式读取原 72 场景后，指标、排序和条件不变 |
| `historical-reanalysis.json` | 原收据的离线复核，明确保留 v1 与 `historical_evidence_only=true` |
| `account-audit-before-smoke-archive.json` | 原有 502 份报告的修正审计：76 份 1M、426 份 100M |
| `account-audit.json` | 加入 9 份新 smoke 后的最终 511 份报告审计；所有报告按各自明确本金检查 |
| `logs/` | 全量 Python、前端、真实 smoke 与旧结果复核的日志，个人路径已脱敏 |
| `verification.json` | 本轮验证状态、实际退出码与提交范围检查 |
| `SHA256SUMS.json` | 本目录文件 SHA256，清单本身除外 |

复现本地回放时，除原参数外须提供与登记 `provider_identity` 一致的 `--provider-manifest`；数据修订需要登记允许的 amendment SHA。旧 smoke 和缓存不能用于共享实现 v2。

历史审计命令保持为：

```bash
python quant/code/audit/audit_repair_impact.py --output "$NEW_AUDIT_PATH"
python -m pytest -q quant/tests
```

审计中的 100M 合同只适用于 `audit_account_contracts.json` 中逐文件 SHA 明确绑定的旧档案。新增报告使用其自身合同；不存在通用 100M 默认值，也不通过首日净值猜测初始本金。
