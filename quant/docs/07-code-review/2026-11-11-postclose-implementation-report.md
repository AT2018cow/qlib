# 收盘后统一生产、网站身份与公开榜单整改实施报告

> 日期：2026-11-11
>
> 状态：`LOCAL REMEDIATED / NOT DEPLOYED / NOT SCHEDULED-VERIFIED`
>
> 性质：本地实现与测试报告，不是部署批准、生产 PASS、收益认证或自然调度验收。
>
> 交接合同：[`2026-11-11-postclose-production-remediation-handoff.md`](2026-11-11-postclose-production-remediation-handoff.md)
>
> 基线：`95b55855e`（PC01–PC08 本地实现；独立验收 PR01–PR06 整改前）
>
> 独立验收整改：[`2026-11-11-postclose-independent-review-remediation.md`](2026-11-11-postclose-independent-review-remediation.md)

## 1. 结论摘要

PC01–PC08 已在本地实现，并完成独立验收 PR01–PR06 整改。当前状态为
`LOCAL REMEDIATED / NOT DEPLOYED`，保留以下既有合同：

- 唯一自建 overlay provider；日常路径不回退 chenditc，不恢复主备双源。
- `qlib-prod-daily` / `qlib-prod-website` 两个生产 App；未合并容器、未做镜像瘦身、未增加多容器抓取。
- CSI300 / CSI500 / CSI1000 三市场、三个独立 1M 账户、各自 TopK/DropN。
- T 日收盘评分、U 日开盘执行、20-session 重训、标签成熟度/purge、PIT 成分、板块/日期涨跌停、5bp 买 / 15bp 卖与最低 5 元费用。
- Provider 2 CPU / 8 GiB、Website incremental 4 CPU / 12 GiB、full/smoke/retrain 8 CPU / 16 GiB、Signal 8 CPU / 16 GiB。
- R01–R10、S01–S05 与既有复权/来源边界未回退。

本轮**没有**部署、push、运行付费 Modal、写生产 Volume、发布 GitHub 信号或执行归档删除。

## 2. PC01–PC08 状态

| ID | 本地状态 | 实现要点 | 主要测试 | 剩余外部门 |
|---|---|---|---|---|
| PC01 | 已实现 | 18:00 `daily_cron`；23:00 `daily_postclose_check`；23:15 `daily_evening_retry` 只转发同一 `daily_cron`；U 07:00 `daily_readonly_check`；v2 `postclose_status` marker | `test_cron_gate.py`、`test_daily_pipeline.py`、`test_modal_structure.py` | 部署后核对四个自然时点与邮件实际到达 |
| PC02 | 已实现 | `release_source_sha256` / `website_semantic_sha256` / `account_contract_sha256`；replay core 移出 Modal launcher；显式语义闭包与运行时未登记 import fail-closed | `test_research_identity.py`、`test_website_simulation.py` | 首轮有界 full + 下一交易日 4/12 incremental 实测 |
| PC03 | 已实现 | 删除 USD 阻断与 `SKIPPED_BUDGET`；保留只读 billing telemetry；保留 fresh-fit/timeout/数据 gate | `test_website_budget.py`、`test_website_simulation.py` | 部署后人工查询 `billing_status` |
| PC04 | 已实现 | 公开 CSV = `ranking_full[:topk]`；T-close 涨跌停仅 informational；账户继续使用完整 ranking；历史 v1 不重写 | `test_csi1000_mvp_contract.py`、`test_website_simulation.py` | 首轮新旧 CSV 差异逐项解释 |
| PC05 | 已实现 | website 所有 `/data` mount 使用 `data_volume_readonly`；`/simulation` 保持可写 | `test_website_simulation.py::test_every_website_data_mount_is_read_only` | 部署后回读 function info |
| PC06 | 已实现 | 单容器 8 workers；线程级 Session/连接池；429/502/503/504 指数退避 + full jitter；`Retry-After` clamp；8 req/s token bucket；per-host 指标；单次 build history 缓存 | `test_sina_fast.py` | 隔离 probe 验证真实源站 429/延迟；本地 mock 不代表源站限流消失 |
| PC07 | 已实现 | 代码身份失败即拒绝发布；`retention_plan` 只生成 keep/remove 原因、路径、digest、size；`prune_archives(dry_run=False)` 直接拒绝 | `test_provider_store.py`、`test_daily_pipeline.py` | Codex 验收 retention 固定 fixture；首次 apply 需单独操作者批准 |
| PC08 | 已实现 | 方法页、文档索引、网站交接、旧 evening 调度历史标记、本报告 | 文档审阅 + 前端测试 | 部署验收时核对公网页面 |

### 2.1 独立验收 PR01–PR06 关闭状态

| ID | 本地状态 | 修复 |
|---|---|---|
| PR01 | 已修复 | `_build_market_impl` 未缓存 full 路径改用 `cache_sha` 并显式传 `semantic_sha`/`account_sha`；新增真实调用点测试覆盖 FULL、INCREMENTAL、4/12 无祖先 fail-closed |
| PR02 | 已修复 | `source_manifest("training")` 与 website semantic 从 `qlib_runtime.MODEL_CFG` 派生实际生产 YAML，包含 `workflow_config_lightgbm_Alpha158_csi500.yaml`；删除未使用的 `release_manifest` 重复抽象；负控证明 `_code_source_sha()` 与 website `daily()` 使用同一实现 |
| PR03 | 已修复 | 新增唯一 `validate_publication_receipt()` / `checked_publication_receipt()`；receipt 绑定 schema、release、semantic、三市场 account、provider、latest、12 文件及三市场 result/series/ledger/final-state 哈希；`daily()`、`status()`、postclose marker 共用 |
| PR04 | 已修复 | 23:15 `daily_evening_retry` timeout 改为 `BACKUP_TIMEOUT_SECONDS`，覆盖排队 + 一次完整恢复；仍只 `.remote()` 同一 `daily_cron(max_containers=1)` |
| PR05 | 已修复 | 新增只读 `collect_retention_references()`，从 legacy anchor、archive base lineage、journal、postclose/preopen marker、website predecessor/recovery receipt 收集 reachability；生产 dry-run 自然调用 collector；损坏/未知引用全部 keep，仍禁止 apply |
| PR06 | 已修复 | 调度测试注入固定 `_trading_days()`，禁网全量套件通过；前端报告改为逐文件循环；新增固定 old/new fixture 证明公开 CSV 允许差异而账户 trades/fees/holdings/cash/NAV 精确一致 |

## 3. 目标调度与日期合同

```text
T 18:00  daily_cron           写 Volume / 带发布 secret / 唯一重计算协调器
T 23:00  daily_postclose_check 只读 / 无 secret / 不执行 worker
T 23:15  daily_evening_retry   只读 / 无 secret / 仅 .remote() 排队同一 daily_cron
U 07:00  daily_readonly_check  只读 / 无 secret / 最终核验同一 U/T
```

日期解析集中在 `quant/code/production/daily_pipeline.py`：

- `resolve_postclose_dates(calendar, now=...)`：普通日、周五、长假前最后交易日、非交易工作日、17:59 拒绝 / 18:00 允许、手动双日期相邻校验。
- `resolve_check_dates(calendar, now=...)`：U 日早晨反推上一真实交易日 T。
- 旧 `preopen_status` marker 仅作历史证据；新写入 `postclose_status/<usage_date>.json`，schema v2 明确 `session_date`、`usage_date`、`provider_calendar_end`、`website_latest_date`、`journal_signal_date`。

## 4. 身份拆分

| 身份 | 计算 | 用途 |
|---|---|---|
| `release_source_sha256` | 完整 `quant/code/**/*.py` + `qlib_runtime.MODEL_CFG` 实际引用的全部训练 YAML（含 `_Alpha158_csi500.yaml`；沿用 research `source_manifest("training")` v2 文件集） | 部署一致性、审计、receipt |
| `website_semantic_sha256` | `WEBSITE_SEMANTIC_DEPENDENCIES` 显式闭包（website identity schema v3） | model/result cache、incremental ancestor |
| `account_contract_sha256` | market/profile/horizon/TopK/DropN/费用/本金/起点/重训/执行规则（website identity schema v3） | 防止语义 hash 漏绑业务合同 |

`SOURCE_IDENTITY_VERSION` 保持 2，避免让既有研究 receipt 因本轮网站整改而失效；新增网站 semantic/account 文档使用 `WEBSITE_IDENTITY_VERSION=3`。生产 `_code_source_sha()`、website `daily()` 和测试负控使用同一个 `source_manifest("training")`，不再保留未使用的 `release_manifest` 重复实现。

关键行为：

- 只改 Modal `cpu`/`memory`/`schedule`/launcher 或文档：release 改变，semantic/account 不变，增量祖先可复用。
- 改模型、特征、费用、replay、board/execution、账户合同：semantic 或 account 改变，旧祖先拒绝并进入受控 full。
- worker 在 import 后校验本次新增的仓库内业务模块属于声明闭包；未登记 import fail-closed。
- `publish_results` 同时校验 release、semantic、account、provider 和 series；receipt 记录三类身份。
- `website_replay_core.py` / `qlib_runtime.py` / `provider_asset.py` / `github_config.py` 已从 Modal launcher 拆出，replay core 不 import Modal。

## 5. 公开榜单合同 v2

- `daily_standalone` 不再执行 `day = day.loc[~limited]` 或从 TopK+1 补位。
- `published_selection = ranking_full[:topk]`；CSV 仍为 `rank,instrument,score`。
- `paper_planner_input = ranking_full`，账户规划、成交、费用、持仓、cash、NAV 代码未改变。
- `mvp_metadata` 增加 `ranking_contract_version=2`、`published_selection_instruments`、`published_selection_sha256`；`t_close_limit_annotations` 强制 `informational_only=true`。
- 历史 v1 文件、journal、账户状态不重写；v1 CSV 仍可由 `parse_top20_csv` 读取。

## 6. 测试证据

PR01–PR06 均先写失败回归并在 `95b55855e` 上复现：

- PR01：真实 `_build_market_impl` 未缓存 full 调用点报 `NameError: source_sha`（同一路径也缺 `semantic_sha`/`account_sha`）；4 个真实调用点测试先失败。
- PR02：实际 `_Alpha158_csi500.yaml` 不在 release/semantic 中；临时 repo 负控先报 `missing training configuration`。
- PR03：只有 status/source/asset 的最小 receipt 可通过旧 `already_published`；新 validator 测试先因缺少 `checked_publication_receipt` 失败。
- PR04：`daily_evening_retry` 旧 timeout `10 * 60` 小于 `BACKUP_TIMEOUT_SECONDS`；AST 测试先失败。
- PR05：`collect_retention_references` 不存在；手工日期 fixture 无法证明生产自然 dry-run，测试先 ImportError。
- PR06：调度测试在 resolver 前仍调用实时 `_trading_days()`；禁网运行失败。前端报告命令只执行首个 `.cjs`。

整改后本地结果：

```text
HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY= \
  .venv/bin/pytest -q quant/tests
1161 passed, 2 skipped, 23 warnings, 50 subtests passed

for test_file in quant/tests/test_*.cjs; do
  node "$test_file" || exit 1
done
all 5 frontend files passed

.venv/bin/black quant/code quant/tests -l 120 --check --diff --exclude qlib/_version.py
238 files would be left unchanged

git diff --check
passed
```

定向覆盖包括：

- PR01：`test_website_simulation.py` 四个真实调用点测试：FULL 无祖先、合法 INCREMENTAL、4/12 无祖先 fail-closed、cache/semantic/account 参数。
- PR02：`test_research_identity.py` 实际 `_csi500.yaml` 改 release+semantic；resource-only 只改 release；生产 `_code_source_sha()` 与 website `daily()` 同源负控。
- PR03：`test_website_budget.py` 合法三市场 receipt、最小伪造、semantic/account/latest/result/series/ledger/provider/files 篡改全部拒绝；`test_website_simulation.py` daily/status 拒绝；`test_daily_pipeline.py` marker 拒绝 semantic/account/latest 不一致。
- PR04：`test_daily_pipeline.py` retry timeout 等于 `BACKUP_TIMEOUT_SECONDS` 且只转发一次。
- PR05：`test_provider_store.py` 真实目录 fixture 收集 anchor、journal、FAILED marker、website predecessor、recovery receipt；损坏引用全部 keep；`dry_run=False` 仍拒绝。
- PR06：`test_website_simulation.py` 固定 old/new fixture：公开 CSV 从旧过滤/补位变为 raw TopK，账户 trades/fees/holdings/cash/NAV 与完整 ranking 哈希精确一致；禁网全量套件通过。

## 7. 首次部署迁移说明## 7. 首次部署迁移说明

- 新 identity schema 首次部署预期触发一次有界 8/16 smoke + full replay；这不是回归失败。
- 首轮 full 需用同一 frozen provider 逐日比较旧/新 score、排名、成交、费用、持仓、cash、NAV；除新增身份字段外要求精确一致。本轮已用固定 fixture 证明公开 CSV 之外的账户路径不变，真实 provider 的逐日比较仍是部署外部门。
- PC04 只允许公开 CSV 出现可解释差异（原始 TopK 不再删榜/补位）；账户路径必须不变。
- 新 full 结果成为后续增量祖先；下一新交易日必须实际走 4/12 `INCREMENTAL`，证明资源/schedule 变化不再强制 full。
- 新 receipt schema 首次写入后，`daily()` / `status()` / postclose marker 均会重读三市场 immutable result/series/ledger 哈希；任何旧的最小 receipt 会被拒绝，需按审计流程处理，不得静默覆盖。
- 旧 `preopen_status` marker、历史 v1 CSV、journal 和账户状态只读保留，不覆盖、不重写。

## 8. 明确未执行

- 未 `modal deploy`，未运行 `modal run`，未执行付费训练/回放。
- 未 push 到远端，未更新生产 GitHub 信号，未写生产 Volume。
- 未执行归档删除；`prune_archives` 仅 dry-run，`dry_run=False` 直接拒绝。
- 未完成自然 18:00 / 23:00 / 23:15 / 07:00 调度验收。
- 未验证 Modal 失败邮件实际到达；邮件仍是外部配置，仓库不声称自行发送。
- 网站收益仍是因果纸面模拟，不是券商成交、逐日 ST、开盘流动性或官方参考价认证。

## 9. 交付状态

```text
LOCAL REMEDIATED / NOT DEPLOYED
```

Codex 验收通过且用户另行授权前，不得写成已部署、生产 PASS 或自然调度验收通过。
