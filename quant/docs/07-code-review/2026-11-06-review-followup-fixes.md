# 四项复审问题修复与验收（2026-11-06）

状态：`FIXED_AND_VERIFIED / NOT_DEPLOYED`。范围为本轮复审的四项 P2；此前修复、策略复验与历史证据仍保留。本次不改变三市场 canonical、资金规模或用户明确取消的回撤/单股仓位上限，不部署或发送实盘订单。

## 修复与证据

| 编号 | 问题与修复 | 验收 |
|---|---|---|
| PR01 | `prelive_local.py` 运行前未对照注册的 provider。现在必须传入 `--provider-manifest`，校验文件清单摘要、文件数量、完整日历末日及全部登记文件内容；每个 worker 在使用缓存前和完成任务前重复核验，并检查实际读取的文件都在登记集合内。缓存身份加入 provider 和共享源码摘要。 | 短日历、同路径改报价、未登记文件均被拒绝；缓存也不能绕过校验。正式登记下三市场 × 三成本的新 smoke 通过。 |
| PR02 | 入口脚本摘要遗漏共享实现。新增 `research_identity.py`，源码身份 v2 覆盖 `quant/code/` 全部 Python 文件；训练另绑定实际使用的 Alpha158 YAML。启动端和 worker 独立核验；smoke、fit cache、回放结果及当前结果汇总绑定同一 manifest。 | 只改 `configure_asof` 的文件、保持入口不变，也会使旧 smoke、fit cache 和 prepared-data cache 失效。真实 Modal SDK 的本地 worker 在下载数据前拒绝旧身份。 |
| PR03 | B 归因对照继承了板块数量规则，使 A→B 同时改变信息时点和股数。新增独立 `enforce_board_quantities` 开关；仅 `uniform_open_exchange()` 显式关闭，恢复 native Exchange 数量行为。普通研究执行显式开启，生产合法数量规则保留。 | 平价、不触发限价时，三个板块 × 199/201/299 股的 A/B 成交金额、股数和费用一致；大额零股卖出也保持 native 行为。STAR 现代执行仍拒绝 199 股、接受 201 股。 |
| PR04 | 审计递归扫描时写死 1 亿本金。现在读取 Parquet 属性或同目录本金/指标合同，并为已知旧档案建立逐文件 SHA 绑定的明确本金登记；未知、冲突、非有限或被篡改的合同单独报错，不猜本金。基础指标和成本压力使用同一个实际本金。 | 原有 502 份报告包含 76 份 1M、426 份历史 100M，检查无误报；新 smoke 另增加 9 份 1M 后，最终 511 份报告也无本金合同、指标或成本压力错误。 |

### 输入修订与缓存规则

旧执行登记保持不变。允许数据修订时，应创建新的登记版本：在 `provider_identity.allowed_amendment_sha256` 中明确列出修订文件的 SHA，再通过 `--provider-amendment` 提供该文件。修订文件须包含 `schema_version=1`、原始 `base_files_manifest_sha256` 和逐文件 `replacements`，每项明确 `before_sha256` / `after_sha256`。未登记的修订、错误原文件和修订文件自身变化均拒绝。修订版本需要自己的匹配 smoke，不复用原数据版本的 smoke。

共享源码身份 v2 是保守的内容合同：增加、删除或修改任意扩展 Python 实现都会使旧执行缓存失效；文档和行情工件不属于源码集合。旧 v1 收据仍是历史证据，不能授权新回放、云训练或缓存复用。未来训练的登记应同时绑定 `training_driver_sha256` 和 `training_source_manifest_sha256`，近期账户重建工具会核对二者。

读取此前已归档的 v1 结果时必须显式提供 `--historical-source-manifest`。该选项仅用于离线汇总旧证据，会标记 `historical_evidence_only=true`；不进入任务执行或缓存匹配逻辑：

```bash
python quant/code/research/regime_reselection/analyze_prelive_results.py \
  --results "$REPLAY_CONTROL_ROOT" \
  --register quant/docs/07-code-review/2026-11-06-prelive-execution-register.json \
  --historical-source-manifest quant/data/prelive_revalidation/20261106/source-manifest.json \
  --output "$NEW_ANALYSIS_PATH"
```

## 结果与验证范围

- 最终 Python 全量回归：**836 passed、1 skipped、50 subtests passed**，真实退出码 0；1 条原有 Modal 本地挂载提示。前端 **25 个用例通过**。
- 三市场新 smoke 共 **9 个成本场景**，每场景 21 个执行交易日；每日 NAV、现金、费用、逐笔订单及解析后的日账本与原长历史回放对应前缀完全一致。
- 用修正后的汇总器显式重读原 72 场景：主排序、各项替换条件、扩展与相位指标均与正式归档一致，保留现任的研究结论不变。没有重写旧收据的源码身份，也没有把旧结果标为 v2 执行。
- 最终审计：**511 份报告**，其中 85 份 1M（含本次 9 个 smoke）和 426 份 100M；本金合同错误、指标错误、费用压力错误均为 0，426 份带相应字段的报告完成压力复算。
- 本轮没有新增付费云任务。云训练代码的身份阻断使用真实 SDK 本地入口及回归测试验证；下一次实际云训练仍须在新源码身份下重新完成前台 smoke，再运行有界批次。

证据见[本轮归档](../../data/review_fixes/20261106/README.md)、[最终审计](../../data/review_fixes/20261106/account-audit.json)、[前缀与排名复核](../../data/review_fixes/20261106/parity.json)和[当前源码清单](../../data/review_fixes/20261106/source-manifests.json)。旧档案的明确本金合同在[逐文件登记](../../data/audit_account_contracts.json)中，新增或变化的报告不会自动继承旧本金。

此前[有限使用包](2026-11-06-limited-use-review-package.md)及其数据目录内的源码清单保留为修复前的冻结记录。正式部署准备时须以本次提交源码重新形成包并补齐目标日证据；不能用旧源码摘要认证当前版本。尚未完成的正式状态迁移、生产部署及目标交易日验收仍按原范围处理。
