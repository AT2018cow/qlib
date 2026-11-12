# 研究回测与策略择优整改实施报告

> 日期：2026-11-11
>
> 状态（2026-11-12 后续核对）：`LOCAL REMEDIATED / LOCAL INDEPENDENTLY RE-VERIFIED / COMMITTED / PUSHED / SOURCE IN PRODUCTION IMAGE / RESEARCH CLOUD REPLAY NOT VERIFIED`
>
> 交接合同：[`2026-11-11-research-backtest-strategy-selection-remediation-handoff.md`](2026-11-11-research-backtest-strategy-selection-remediation-handoff.md)
>
> 审查发现：[`2026-11-11-research-backtest-strategy-selection-review.md`](2026-11-11-research-backtest-strategy-selection-review.md)
>
> 独立验收：[`2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md`](2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md)
>
> 二次整改交接：[`2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md`](2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md)
>
> 二次整改独立验收：[`2026-11-12-research-backtest-strategy-selection-second-remediation-independent-review.md`](2026-11-12-research-backtest-strategy-selection-second-remediation-independent-review.md)
>
> Codex 最终整改：[`2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md`](2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md)
>
> 性质：本地代码、测试和证据链整改；不是策略晋升、Modal 执行、生产部署或券商成交认证。

> 历史说明：DeepSeek 两轮完成时，本报告的 `LOCALLY_FIXED` 只是实施者自证，Codex 负向探针仍复现缺口。
> Codex 后续已亲自完成 AR01–AR09 并本地独立复验；`80014b09f` 已提交推送，源码包含于生产 v26 镜像。本地通过和源码部署均不能替代研究云端复验或策略晋升验收。

## 1. RR01–RR09 状态矩阵

| ID | 状态 | 本地实现 |
|---|---|---|
| RR01 | `LOCALLY_FIXED` | 新增 `spine_identity.py`：canonical job identity 绑定 source/environment/provider/model YAML/完整 strategy contract/日期/seed/threads/runtime；slice 路径含 `job_sha256`，receipt 自哈希；assembly 绑定请求窗口、calendar、期望日期、有序 slice receipt 和 signal 哈希；legacy receipt 不再命中 |
| RR02 | `LOCALLY_FIXED` | 新增 `current_window_net.py`，current-window 与 bear block 统一使用 `portfolio_performance()`；输出 gross/cost/net/account 校验；bear 拼接拒绝重复/断裂/跨块；13 单元 correction inventory 与 bear `GROSS_ONLY_WITHDRAWN` 状态生成，旧文件原字节不改 |
| RR03 | `LOCALLY_FIXED` | 新增 `strategy_release.py` 与 `quant/data/strategy_releases/`；三市场 release 分别标记 `RISK_ACCEPTED_OVERRIDE`/`INCUMBENT_RETAINED`；`GATE_PASS` 必须绑定 gate receipts + bundle；override 必须绑定 reason/scope/gaps/decision/authority |
| RR04 | `LOCALLY_FIXED` | `profile_manifest()` 按 `(market, profile)` 解析 release；CSI300/CSI500 不再携带 CSI1000 Stage-B path/token/contract；完整 strategy contract SHA 绑定模型、label、universe、TopK/DropN、重训、费用、本金和执行时序 |
| RR05 | `LOCALLY_FIXED` | 新增 `research_bundle.py`（builder/verifier/summary rebuild/legacy inventory verifier）；G1/G2/bear legacy 清单标记 `LEGACY_*`/`GROSS_ONLY_WITHDRAWN`，原件 SHA 不变；不补签历史证据 |
| RR06 | `LOCALLY_FIXED` | 新增 `training_window.py`；spine `fit_start` 同时进入 handler `fit_start_time`、`segments.train[0]`、job identity 和 receipt；日期/顺序/最小样本 fail-closed |
| RR07 | `LOCALLY_FIXED` | 新增 `finite_values.py`；Gate-0、候选评分、JSON 输出拒绝 bool/NaN/Inf/非正 NAV/重复或乱序日期；`allow_nan=False` |
| RR08 | `LOCALLY_FIXED` | `failed_jobs()` 先校验 jobs/results 长度；coordinator map 后长度不等立即抛错，不打印完成、不进入 assembly |
| RR09 | `LOCALLY_FIXED` | `research_identity.environment_manifest()` 绑定 Python、实际 Qlib 源码 digest、NumPy/Pandas/LightGBM/PyArrow 版本；不写本地绝对路径；job identity 与 bundle 使用 environment SHA |

原实施阶段未执行真实 overlay 研究 bounded replay、Modal 部署和券商成交认证；后续生产部署事实见[生产状态记录](2026-11-12-production-deployment-status.md)，研究云端复验与券商认证仍无完成证据。

## 2. 旧实现失败 → 新实现通过证据

| 项 | 旧实现反例 | 新回归测试 |
|---|---|---|
| RR01 | 旧 slice receipt 仅校验 status/prediction SHA；profile/seed/source/provider/窗口变化仍可命中；旧 assembly 只校验 slice 数和 signal SHA | `test_regime_reselection_cache_identity.py::test_any_identity_field_change_invalidates_the_job`、`test_slice_receipt_rejects_identity_or_prediction_changes`、`test_slice_set_and_assembly_receipt_reject_missing_extra_duplicate_or_old_window`、`test_legacy_receipt_is_auditable_but_cannot_authorize_cache_hit` |
| RR02 | 旧 current-window `cumulative_return=(1+return).prod()-1`；bear 只返回 `report["return"]` | `test_current_window_net_performance.py::test_nonzero_cost_net_is_below_gross_and_matches_account`、`test_bear_blocks_combine_only_verified_net_and_reject_gaps_or_duplicates` |
| RR03 | 旧生产配置只有硬编码 profile/TopK/DropN，无 release receipt；CSI300/500 未绑定人工 override 证据 | `test_strategy_release_manifest.py::test_current_release_map_is_override_override_incumbent`、`test_override_or_pass_evidence_is_fail_closed`、`test_gate_pass_requires_gate_receipts_and_bundle` |
| RR04 | 旧 `profile_manifest()` 给所有 profile 无条件附加 CSI1000 Stage-B 字段 | `test_strategy_release_manifest.py::test_market_profile_lineage_is_not_cross_wired` |
| RR05 | 旧 G1/G2 summary 无完整 provider/source/report 闭包 | `test_research_evidence_bundle.py::test_complete_bundle_verifies_and_rebuilds_summary`、`test_any_bundle_artifact_byte_change_fails`、`test_repository_legacy_inventory_is_uncertified_and_bytes_stable` |
| RR06 | 旧 `segments["train"]` 硬编码 `2016-01-01` | `test_training_window.py::test_nondefault_fit_start_enters_train_segment_and_handler_window`、`test_spine_runner_wires_the_same_fit_start_to_both_windows` |
| RR07 | 旧 Gate-0 对 NaN 返回 PASS；`json.dumps` 默认允许 NaN | `test_regime_reselection_gate0.py::test_nonfinite_bool_and_nonpositive_nav_fail_closed`、`test_duplicate_or_unsorted_dates_fail_closed`、`test_finite_json_rejects_nan_tokens` |
| RR08 | 旧 `zip(jobs, results)` 静默丢弃尾部 jobs | `test_regime_reselection_spine_jobs.py::test_partial_or_extra_results_fail_closed`（修复前实际失败：`ValueError not raised`） |
| RR09 | 旧 source identity 只哈希 `quant/code`，不绑定 Qlib/依赖环境 | `test_research_identity.py::test_environment_manifest_binds_qlib_source_and_dependency_versions`、`test_environment_manifest_rejects_missing_package` |

## 3. Legacy artifact 清单

原件均未修改；SHA 来自 `quant/data/research_evidence/legacy_inventory.json`。

| 原 artifact | 状态 | 原 SHA256 |
|---|---|---|
| `quant/data/current_window_backtest/g1_spine_manifest_20261102.json` | `LEGACY_MIXED_SOURCE_UNCERTIFIED` | `c5c848adab77a56477b88f6ccdc42a5a0a23e69461ce98fa31f778f33e6032ff` |
| `quant/data/current_window_backtest/g2_csi1000_summary.json` | `LEGACY_INCOMPLETE_EVIDENCE` | `3c227896212185b013dc5657668b682b486e9e7a3fb07ea6f07dede070f23a7d` |
| `quant/data/current_window_backtest/g2_csi300_summary.json` | `LEGACY_INCOMPLETE_EVIDENCE` | `afe31f9ed0cd8873288013d6eefc9394ee9124da32f1b388f75dabec4c5e0340` |
| `quant/data/current_window_backtest/g2_csi500_summary.json` | `LEGACY_INCOMPLETE_EVIDENCE` | `73bf36658e77dee6b75f9151ee5b9671f2f3c324407c0a41f969a8afe829b2f8` |
| `quant/data/current_window_backtest/g2_1m_summary.json` | `LEGACY_INCOMPLETE_EVIDENCE` | `6d8028e7da20e402a6e6a5e8469803c89d81035aa63293aae1a27ac588b3ba6f` |
| `quant/data/current_window_backtest/2021-02-18_2024-02-05_bear_market_summary.json` | `GROSS_ONLY_WITHDRAWN` | `c73bffec9f55464da37a44d7d5300243aef6c6ae8b3bff6e0048d8874b0fb667` |
| `quant/data/current_window_backtest/2026-10-16_2026-10-29_candidate_results.json` | `RETAINED` | `5f1b32ca91985d4003d37d052bb726571dd3fe04658e3b056a7f5d5769ebba84` |

新增 derivative：

- `quant/data/current_window_backtest/2026-10-16_2026-10-29_correction_inventory.json`：13 单元旧值、account 净收益、差异、原文件 SHA；`ranking_changed=false`。
- `quant/data/current_window_backtest/2021-02-18_2024-02-05_bear_market_status.json`：`GROSS_ONLY_WITHDRAWN`，缺少完整冻结 report/cost 时不补算。
- `quant/data/research_evidence/legacy_inventory.json`：legacy 清单与原件 SHA。

## 4. Canonical release manifest

| 市场 | Release ID | 状态 | Manifest SHA256 |
|---|---|---|---|
| csi300 | `csi300_59b93929_top10_drop1_10d_risk_accepted_override_v1` | `RISK_ACCEPTED_OVERRIDE` | `8a76e344133307befe6a250db442e54f4fc8ad8f812f5be5f610695ac8fbccce` |
| csi500 | `csi500_raw10d_c98856b4_top20_drop2_20d_risk_accepted_override_v1` | `RISK_ACCEPTED_OVERRIDE` | `cdfc90d6ae46e1331e13f21d746066183e52474937ff2e09a6a193ba5100309d` |
| csi1000 | `csi1000_c98856b4_top10_drop1_incumbent_retained_v1` | `INCUMBENT_RETAINED` | `74e292269ce47cf9a6ab5c9e6483822a61fbb42d9ec8b21f5a3d438fa8554502` |

三市场 TopK/DropN、1M 本金、5bp/15bp/最低 5 元费用、20-session 重训和 T-close→T+1-open 均与整改前一致。CSI300/CSI500 是人工风险接受，不是研究 gate PASS；CSI1000 是 incumbent 延续，不是本轮新晋级。

## 5. 修改文件

代码：

- `quant/code/execution/research_identity.py`
- `quant/code/execution/finite_values.py`（新增）
- `quant/code/execution/research_bundle.py`（新增）
- `quant/code/execution/strategy_release.py`（新增）
- `quant/code/production/csi1000_production_config.py`
- `quant/code/production/csi1000_mvp_contract.py`
- `quant/code/production/website_simulation.py`
- `quant/code/ops/image_filters.py`
- `quant/code/research/regime_reselection/spine_jobs.py`
- `quant/code/research/regime_reselection/spine_identity.py`（新增）
- `quant/code/research/regime_reselection/spine_and_replay_modal.py`
- `quant/code/research/regime_reselection/training_window.py`（新增）
- `quant/code/research/regime_reselection/gate0_diagnostics.py`
- `quant/code/research/regime_reselection/select_per_market.py`
- `quant/code/research/current_window/current_window_net.py`（新增）
- `quant/code/research/current_window/current_window_modal.py`

数据与文档：

- `quant/data/strategy_releases/{csi300,csi500,csi1000}.json`
- `quant/data/research_evidence/legacy_inventory.json`
- `quant/data/current_window_backtest/2026-10-16_2026-10-29_correction_inventory.json`
- `quant/data/current_window_backtest/2021-02-18_2024-02-05_bear_market_status.json`
- `quant/docs/08-current-window-backtest/01-...md`、`04-...md`
- 本报告

测试：

- `quant/tests/test_regime_reselection_cache_identity.py`
- `quant/tests/test_current_window_net_performance.py`
- `quant/tests/test_strategy_release_manifest.py`
- `quant/tests/test_research_evidence_bundle.py`
- `quant/tests/test_training_window.py`
- 扩展 `test_regime_reselection_spine_jobs.py`、`test_regime_reselection_gate0.py`、`test_research_identity.py`、`test_image_filters.py`

## 6. 本地验证

```text
.venv/bin/python -m pytest -q quant/tests
1234 passed, 2 skipped, 24 warnings, 50 subtests passed in 40.81s
```

定向套件：

```text
test_regime_reselection_spine_jobs.py
test_regime_reselection_cache_identity.py
test_regime_reselection_gate0.py
test_current_window_net_performance.py
test_strategy_release_manifest.py
test_research_identity.py
test_extension_calculation_fixes.py
test_csi1000_production_config.py
test_lineage_market_binding.py
test_training_window.py
test_research_evidence_bundle.py
236 passed, 12 warnings in 6.46s
```

格式与状态：

```text
black quant/code quant/tests -l 120 --check --exclude qlib/_version.py
git diff --check
git status --short --branch
```

## 6.1 独立复审二次整改（IR01–IR08）

历史状态：`IMPLEMENTER-CLAIMED LOCALLY_FIXED / INDEPENDENT ACCEPTANCE FAILED`。当前状态已由后续 AR01–AR09
整改更新为 `LOCAL REMEDIATED / LOCAL INDEPENDENTLY RE-VERIFIED`；后续已提交推送、源码进入生产镜像，但研究 bounded replay 和券商认证仍未完成。

Codex 二次独立验收当时确认：定向与全量测试虽全绿，但 G1 实际 assembly 参数和 receipt schema 自相矛盾；
空业务 payload 的 PROMOTION bundle 仍可授权 `GATE_PASS`；G2/selection、Gate0 CLI 仍可绕过新 helper；
contract、bear identity、correction 单元集合和 release path containment 仍不完整。详情与复现见上述独立验收报告。

| ID | 状态 | 二次修复 |
|---|---|---|
| IR01 | `LOCALLY_FIXED` | 新增 `canonical_assembly_identity`，包含 seed/data+fit start/threads/kernels/backend/source/environment/provider/YAML/contract；job identity 另含 retrain/signal；`validate_slice_receipt_self` 重算 canonical job SHA；coordinator 先应用 CLI override 再 seal；assembly 必须收到 coordinator 期望 identity，不再用首片定义 |
| IR02 | `LOCALLY_FIXED` | `GATE_PASS` 必须解析 `promotion_gate`，调用共享 `research_promotion_gate.check_bundle()`，要求 `promotion_eligible=true`、`failed_gates=[]`，并验证 `PROMOTION` bundle 的 market/profile/contract identity；空 gate/bundle 不再通过 |
| IR03 | `LOCALLY_FIXED` | `canonical_strategy_contract()` 纳入 label expression/horizon、PIT universe、TopK/DropN、retrain、T-close→T+1-open、board/date limits、listing exemptions、lot、liquidity、fees、cash、account start；类型/范围 fail-closed；CSI300/500 使用通用 `cn_equity_strategy_contract_v1`，无 Stage-B 标识 |
| IR04 | `LOCALLY_FIXED` | `research_bundle` v2 分层 `G1_SIGNAL`/`G2_EVALUATION`/`PROMOTION`；每个 role 有 schema/kind/status/identity；跨 role identity 校验；`rebuild_summary()` 从 report/ledger 调用 `portfolio_performance()` 重算，不再复制 summary；`assemble_spine()` 成功路径生成/验证 G1 bundle；`select_per_market --g2-bundle` 验证 G2/PROMOTION |
| IR05 | `LOCALLY_FIXED` | bear block 输出 initial_cash/final_account/account_return_max_error；`combine_bear_blocks()` 验证 block account/error/cash/gross-cost-net，driver 从 provider calendar 独立计算 expected sessions；缺日/重复/篡改 account 拒绝 |
| IR06 | `LOCALLY_FIXED` | 新增 `build_correction_inventory()` 纯函数和 `quant/code/ops/build_current_window_correction.py` CLI；临时重建与已登记 inventory canonical payload 一致，原 candidate result SHA 不变 |
| IR07 | `LOCALLY_FIXED` | environment schema v2：从实际 `qlib.__file__` 解析 package root，校验声明 root 一致，哈希 `.py/.pyx/.so` 实际加载产物；缺扩展/依赖失败；manifest 不含绝对路径 |
| IR08 | `LOCALLY_FIXED` | `reconstruct_paper_series(expected_dates=...)` 校验应有 session；缺日默认失败，`allow_incomplete=True` 返回 `INCOMPLETE`；额外/重复/乱序失败；保留 NaN/Inf/bool/非正 NAV 门禁 |

修复前探针 → 修复后回归：

- `PROBE_RR01_MIXED_SEED=ACCEPTED` → `test_assembly_identity_rejects_mixed_seed_slices`、`test_resealed_tampered_job_identity_is_rejected` 通过。
- `PROBE_RR03_EMPTY_GATE_AND_BUNDLE=ACCEPTED` → `test_gate_pass_requires_real_gate_and_bundle` 通过。
- `PROBE_RR04_OMITTED_CONTRACT_FIELDS_SAME_SHA=True` / `CSI300_CONTRACT_VERSION=csi1000_stage_b_forward_v1` → `test_canonical_contract_changes_on_every_business_field`、`test_market_profile_lineage_is_not_cross_wired` 通过。
- `PROBE_RR05_ARBITRARY_CONTENT_BUNDLE=ACCEPTED REBUILT {}` → `test_arbitrary_role_payload_is_rejected`、`test_summary_mismatch_and_symlink_escape_are_rejected` 通过。
- `PROBE_RR02_BEAR_GAP=ACCEPTED` / `PROBE_RR02_TAMPERED_BLOCK_ACCOUNT=ACCEPTED` → `test_bear_blocks_combine_only_verified_net_and_reject_gaps_or_duplicates`、`test_bear_block_account_and_cost_tampering_is_rejected` 通过。
- `PROBE_RR09_PYX_CHANGE_SAME_SHA=True` → `test_environment_manifest_binds_qlib_source_and_dependency_versions`、`test_environment_manifest_rejects_wrong_import_root` 通过。
- `PROBE_RR07_MISSING_SESSION=ACCEPTED PASS` → `test_expected_sessions_missing_extra_and_incomplete` 通过。
- IR06 无生成器 → `test_current_window_correction_inventory_is_reproducible_and_keeps_original_bytes` 和临时重建测试通过。

schema 迁移：

- spine job/slice/assembly：`SCHEMA_VERSION=2`；legacy schema 1 receipt 只可审计，不授权 cache/assembly。
- environment：`ENVIRONMENT_IDENTITY_VERSION=2`；旧 manifest 不授权新 job/bundle。
- research bundle：`SCHEMA_VERSION=2`，`G1_SIGNAL/G2_EVALUATION/PROMOTION` 分层。
- strategy contract：`cn_equity_strategy_contract_v1`；release manifest schema 1，三市场 release ID/状态不变。
- 三市场 release manifest SHA 在完整实际规则实体进入 contract 后再次更新：CSI300 `8a76e344…`、CSI500 `cdfc90d6…`、CSI1000 `74e29226…`；状态仍为 override/override/incumbent，TopK/DropN/费用/cash 未变。

## 6.2 Codex AR01–AR09 最终整改与复验

Codex 将二次独立验收的 11 个复现场景先写为回归，修复前为 `11 failed in 1.39s`，再修真实入口和共享
validator。最终完整结果为：

```text
.venv/bin/python -m pytest -q quant/tests
1256 passed, 2 skipped, 24 warnings, 50 subtests passed in 43.57s

本轮涉及的 18 个 Python 文件逐文件 Black --check：PASS
git diff --check：PASS
```

AR01–AR09 均已 `LOCAL PASS`。详细实现矩阵、负向探针、release contract/manifest SHA 和未验证边界见
[`2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md`](2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md)。

提交范围 review 又新增并关闭两项真实路径负控：G2 evaluation identity 现在绑定实际 replay/overlay contract，
不再继承 G1 production strategy；`run_g1_overlays.py` 会从 verified source bundle 派生新的 G2 bundle。selection
中的 CAGR、MaxDD、Sharpe、turnover 和 10/20bp 压力指标均由 bundle 内逐日报表重算；同时篡改 report/summary
中的 selection 数值会被拒绝。新增负控修复前共 `4 failed`，修复后通过并纳入上述全量结果。

## 7. 未验证边界

- `NOT CLOUD-REPLAYED`：未用真实 chenditc/overlay provider 做三市场 bounded replay，也未逐日比较 score/ranking/trades/fees/holdings/cash/NAV。
- `SOURCE IN PRODUCTION IMAGE`：后续生产 v26 已部署包含这些源码的镜像；这不表示研究入口已在云端复验，也不代表批准策略晋升。
- `NOT BROKER-CERTIFIED`：研究/paper 结果仍不是券商成交、竞价流动性或官方参考价认证。
- RR03 的 `GATE_PASS` 路径只有合成 fixture 覆盖；当前三市场没有新的 gate PASS，CSI300/CSI500 仍必须保持 override 状态。
- RR05 bundle builder/verifier 已用合成完整 bundle 验证；真实 G1/G2 原件只做 legacy inventory，不补签。
- RR01/RR09 的真实 Modal worker 环境一致性需要部署前由 Codex 在隔离 probe 中复核。

IR01–IR08 与 AR01–AR09 已完成本地独立复验；研究整改随 `80014b09f` 提交推送，后续生产 v26 已部署。研究云端 replay 与券商认证边界仍按本节保持关闭。
