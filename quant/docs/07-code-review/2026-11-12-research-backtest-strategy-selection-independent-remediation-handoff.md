# 研究回测与策略择优独立复审整改实施交接

> 日期：2026-11-12
>
> 原交接状态：`LOCALLY IMPLEMENTED / INDEPENDENT ACCEPTANCE FAILED / REMEDIATION REQUIRED / NOT COMMITTED / NOT DEPLOYED`
>
> 实施结果：[`2026-11-11-research-backtest-strategy-selection-implementation-report.md`](2026-11-11-research-backtest-strategy-selection-implementation-report.md) 的 IR01–IR08 二次整改章节。
>
> 独立验收：[`2026-11-12-research-backtest-strategy-selection-second-remediation-independent-review.md`](2026-11-12-research-backtest-strategy-selection-second-remediation-independent-review.md)。本地测试全绿，但真实入口与负向探针仍确认 6 个 P1、3 个 P2，本文中的 `LOCALLY IMPLEMENTED` 不等于验收通过。
>
> 后续状态（2026-11-12）：Codex 已亲自完成 AR01–AR09 并本地独立复验通过；见
> [`Codex 整改报告`](2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md)。整改已提交推送并纳入生产 v26 镜像；真实三市场研究 bounded replay 仍待验证。本文其他 `NOT COMMITTED / NOT DEPLOYED` 为原交接时的状态或实施要求。
>
> 问题来源：[`2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md`](2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md)
>
> 首轮实施报告：[`2026-11-11-research-backtest-strategy-selection-implementation-report.md`](2026-11-11-research-backtest-strategy-selection-implementation-report.md)
>
> 实施者：DeepSeek；独立验收：Codex
>
> 性质：IR01–IR08 的二次本地整改交接；不是策略晋级、Modal 运行、提交或部署授权。

## 1. 目标与交付边界

本轮只关闭 Codex 独立复审确认的 IR01–IR08：

1. spine job/slice/assembly 的完整实验身份与自校验 receipt；
2. 可语义验证的 strategy contract、research bundle 和 `GATE_PASS`；
3. bear-block 交易日、成本后账户和拼接连续性；
4. correction inventory 可重跑生成、实际 Qlib 环境身份和 Gate0 交易日完整性。

必须先把已复现场景变成会在当前代码上失败的回归测试，保留修复前失败证据，再修实现。
本轮不重新挑选策略，不改三市场当前模型、TopK/DropN、费用、本金、重训周期或执行时序。

DeepSeek 完成代码、测试、实施报告和文档索引后必须停止，交给 Codex 独立验收。不得自行提交、push、
运行 Modal、部署 App 或写入生产数据。

## 2. 开始前必须阅读

按顺序完整阅读：

1. 仓库根目录 `AGENTS.md`；
2. `quant/docs/00-strategy-handbook/STRATEGY_ATLAS.md`；
3. `quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-review.md`；
4. `quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-remediation-handoff.md`；
5. `quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-implementation-report.md`；
6. `quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md`；
7. 本文。

如实际工作区与本文快照不一致，以实际状态为准，但必须先理解并保护所有新增修改。

## 3. 接手前必须保护的工作区

本文起草时：

- Git HEAD：`4f4163bb1`；本地 `main` 领先 `origin/main` 3 个提交。
- RR01–RR09 的实现、新模块、测试、release JSON、legacy/correction inventory 都仍在未提交工作区。
- Codex 又新增了独立复审文档并更新状态索引，这些也必须保留。

DeepSeek 开始前先把以下输出记录到实施报告：

```bash
git status --short --branch
git log -4 --oneline
git diff --check
```

禁止：

- `git reset --hard`、`git restore`、`git clean`、覆盖式 checkout；
- 只在纯 HEAD 或新建空 worktree 上重做；
- 把未跟踪新模块/测试当作临时文件删除；
- 修改上游 `qlib/`、`tests/`、`examples/` 来规避 extension 层问题。

修改范围只限 `quant/`。若发现用户或其他会话的新 diff，先保护并在实施报告说明，不要覆盖。

## 4. 必须保留的合同与非目标

必须保留：

- 唯一自建 overlay provider；研究 chenditc 和生产 overlay 保持不同 lineage，不拼接数据、信号或账户。
- `qlib-prod-daily` / `qlib-prod-website` 两个生产 App，不合并容器、不做镜像瘦身。
- CSI300 Top10/Drop1、CSI500 Top20/Drop2、CSI1000 Top10/Drop1，三个独立 1M 账户。
- T-close 评分→T+1-open 执行、20-session 重训、标签成熟度/purge、PIT universe、板块/日期限价、
  上市日豁免、整手、5bp/15bp 和最低 5 元费用。
- CSI300/CSI500 保持 `RISK_ACCEPTED_OVERRIDE`；CSI1000 保持 `INCUMBENT_RETAINED`。
- RR06 `fit_start`、RR08 partial-map、非有限数拒绝、legacy artifact 原字节/SHA 和此前 R/S/PC/PR/CR 修复。
- 原交接时须保留的资源候选：Provider 2 CPU / 8 GiB、Website incremental 4 CPU / 12 GiB、full/smoke/retrain 8/16；这些规格后续已在 v26 回读确认。

明确非目标：

- 不重新选模型、调参、修改 TopK/DropN 或重写历史收益。
- 不把 paper simulation 描述为券商成交或官方价格认证。
- 不给 legacy G1/G2/bear 补签缺失证据。
- 不为了兼容旧缓存、旧 release 或旧 summary 而放宽 gate。

## 5. 整改矩阵与依赖顺序

| ID | 级别 | 目标 | 依赖 | 顺序 |
|---|---|---|---|---|
| IR03 | P1 | canonical strategy contract 和正确 market lineage | 无 | 第一批 |
| IR07 | P2 | 实际 Qlib/依赖环境身份 | 无 | 第一批 |
| IR01 | P1 | job/slice/assembly 完整身份 | IR03、IR07 | 第二批 |
| IR04 | P1 | 分层语义 evidence bundle 并接入真实路径 | IR01、IR03、IR07 | 第三批 |
| IR02 | P1 | `GATE_PASS` 必须依赖真 gate + bundle | IR03、IR04 | 第四批 |
| IR05 | P1 | bear calendar/account 拼接校验 | 无 | 可并行 |
| IR06 | P2 | correction inventory 可重生 | IR05 的 metric helper | IR05 后 |
| IR08 | P2 | Gate0 期望 session 完整性 | 无 | 可并行 |

所有项都先加当前失败的回归。建议先修 identity 基础，再修 bundle 和 `GATE_PASS`；IR05/06/08 可在本地独立收敛。

## 6. 全局实施原则

1. 新 identity/receipt/bundle 使用新 schema version；legacy 可审计读取，但不得授权 cache hit、assembly、
   promotion 或 release PASS。
2. canonical JSON 统一 `sort_keys=True`、固定 separators、`allow_nan=False`；写入先临时文件再原子替换。
3. SHA 必须为 64 位小写十六进制，不能只检查长度。
4. 路径必须在 `resolve()` 后验证仍在声明 root 内，拒绝绝对路径、`..` 和 symlink 越界；不记录私人绝对路径。
5. identity 来自实际加载的源码、配置、provider 和运行参数，不信任调用方单独传入的 SHA。
6. 无关 Modal 资源/schedule/文档可改 release/source identity，但不得改 strategy/account contract identity。
7. 至少有一层测试执行生产共用 validator，不得只测一个与真实调用无关的 helper。
8. 继续拒绝 bool/NaN/Inf/非正 NAV；不得因新 schema 放宽现有 gate。

## 7. IR03：canonical strategy contract 和 market lineage

### 目标文件

- `quant/code/execution/strategy_release.py`
- 建议新增 Modal-free canonical contract 模块，或在现有 execution 模块中提取唯一 builder
- `quant/code/production/csi1000_production_config.py`
- `quant/code/production/csi1000_mvp_contract.py`
- `quant/code/production/website_simulation.py`
- `quant/code/research/regime_reselection/spine_and_replay_modal.py`
- `quant/data/strategy_releases/{csi300,csi500,csi1000}.json`
- `quant/tests/test_strategy_release_manifest.py`、`test_csi1000_production_config.py`、`test_lineage_market_binding.py`

### 实现合同

1. 定义 versioned `canonical_strategy_contract()`，只包含能改信号、排名、下单、成本或 NAV 的业务字段，
   不含发布时间、文档和 Modal 资源。
2. 至少绑定 market/profile/candidate/model SHA、完整 label expression + horizon、PIT universe、TopK/DropN、
   retrain sessions、T-close→T+1-open、board/date limits、listing exemptions、lot/quantity、liquidity/missing-quote behavior、
   open/close/min fee、initial cash 和 account start。
3. 版本字符串不能代替完整实体；版本 + 实体都进入 hash。
4. 类型/范围 fail-closed：`topk > 0`、`0 <= dropn <= topk`、费用非负、cash 为正、timing 已知，
   bool 不得冒充数字。
5. 分离通用 production contract version 和 CSI1000 Stage-B research lineage。CSI300/500 不得出现
   `csi1000_stage_b_*` path/token/commit/version。
6. 三个 release JSON 用确定性 builder 重生 contract/manifest SHA，不手改哈希；原 research/decision 文件 SHA 不变。
7. `spine_and_replay_modal.py` 中手工构造的简化 strategy SHA 改用同一 canonical builder。

### 必须测试

- 逐一改 label/PIT/universe/limits/lot/liquidity/fees/timing/cash/TopK/DropN：SHA 改变。
- 改文档、Modal CPU/memory/schedule：strategy/account SHA 不变。
- 负 topk/dropn、dropn>topk、非法费率、bool 数字、未知 timing：重新 seal 也拒绝。
- 三个 canonical profile 的 market/release/contract version 正确；CSI300/500 无 Stage-B 标识。
- release 状态仍为 override/override/incumbent，profile/TopK/DropN/费用/cash 未变。

## 8. IR07：绑定实际导入的 Qlib 环境

### 目标文件

- `quant/code/execution/research_identity.py`
- `quant/tests/test_research_identity.py`
- 必要时扩展 `test_regime_reselection_cache_identity.py`

### 实现合同

1. 从 `import qlib` 的 `qlib.__file__` 解析实际 package root，并验证它与声明 repo/image root 一致。
   意外 site-packages/其他 checkout 导入时失败，不得退回另一棵源码。
2. manifest 用相对文件清单绑定 Qlib Python、Cython `.pyx` 和实际加载的编译扩展产物；
   不记录本地绝对路径。
3. 只包含可改变运行语义的源/加载产物，不哈希 build 临时噪声。
4. 保留 Python、pyqlib、NumPy、Pandas、LightGBM、PyArrow 版本；缺必需依赖或扩展时失败关闭。
5. 升级 environment schema；旧 manifest 可审计，不得授权新 cache/bundle。

### 必须测试

- 仅改实际 package 内 `.py`、`.pyx` 或已加载 extension 任一项：SHA 改变。
- 改无关文档：SHA 不变。
- 实际 import root 与声明 root 不同：拒绝。
- 缺必需扩展/依赖版本：拒绝，不用空/零占位。
- manifest/receipt 不含绝对路径。

## 9. IR01：job、slice 和 assembly 完整身份

### 目标文件

- `quant/code/research/regime_reselection/spine_identity.py`
- `quant/code/research/regime_reselection/spine_jobs.py`
- `quant/code/research/regime_reselection/spine_and_replay_modal.py`
- `quant/tests/test_regime_reselection_cache_identity.py`
- `quant/tests/test_regime_reselection_spine_jobs.py`

### 实现合同

1. 在纯 Python 模块分开 `job_identity`、`assembly_identity`、`slice_receipt`、`assembly_receipt`。
2. assembly identity 包含 run id/end、market/spine/profile/horizon、data/fit start、seed、threads/kernels/backend、
   source/environment/provider/model YAML/strategy contract。
3. job identity 再增加 `retrain_asof`、`signal_end` 和切片边界。
4. 先应用所有 CLI/config override，再一次性 seal identity。当前 `main()` 在 `build_jobs()` seal 后才改
   `kernels/joblib_backend`，必须修正并加回归。
5. worker 从实际 source/environment/provider/YAML/canonical contract 重算 context，与 coordinator 完整相等后才训练/命中缓存。
6. `validate_slice_receipt_self()` 重算 canonical job SHA，要求
   `job_identity.sha256 == job_sha256 == recomputed_sha`，再验 receipt/prediction SHA。
7. coordinator 构建并传入 expected assembly identity。assembler 不得用第一个 receipt 定义期望身份，
   不得只比较当前 6 个字段。
8. assembly receipt 绑定 start/end、calendar/expected-days SHA、assembly identity SHA、有序 job + slice receipt SHA、signal SHA。
9. 新 schema 使用新身份目录；不原地改写 legacy receipt。

### 必须测试

- 两个日期不重叠 slice 仅 seed 不同：拒绝，复现 IR01。
- 仅改 profile/horizon/data+fit start/threads/kernels/backend/source/environment/provider/YAML/contract 任一项：拒绝。
- 篡改 `job_identity` 后重新 seal：仍因 canonical SHA 不匹配拒绝。
- CLI kernels/backend override 进入最终 identity，worker 不因 seal 顺序错误失败。
- 合法不同 retrain/signal dates 可组装；同身份中断恢复不新增 fit。
- 测试经过 `assemble_spine()` 使用的共享 validator，不只测 SHA helper。

## 10. IR04：分层语义 evidence bundle

### 目标文件

- `quant/code/execution/research_bundle.py`
- `quant/code/research/regime_reselection/spine_and_replay_modal.py`
- `quant/code/research/regime_reselection/select_per_market.py` 及真实 G2/replay summary 生成点
- `quant/tests/test_research_evidence_bundle.py`
- 必要时新增 Modal-free integration test

### 实现合同

不要继续使用“所有 bundle 一律要求 10 个任意文件”。定义 versioned `bundle_kind`：

| kind | 必需语义角色 |
|---|---|
| `G1_SIGNAL` | research contract/source+environment/provider/calendar/model/strategy/slice+assembly receipts/signal |
| `G2_EVALUATION` | `G1_SIGNAL` + execution/report/ledger/summary/cost/account validation |
| `PROMOTION` | `G2_EVALUATION` + preregistration/promotion evidence/gate result/release candidate |

具体要求：

1. 每个 role 有 schema/kind/status/identity，不是任意 JSON 的 path+SHA。
2. bundle identity 绑定 run/bundle id、market/candidate/profile、window/calendar、source/environment/provider、
   model/strategy/signal/report/ledger/summary/gate SHA（按 kind 增加）。
3. `verify_bundle()` 先验 path containment + SHA，再验 role schema，最后验跨 role identity 一致。
4. `rebuild_summary()` 只适用 G2/PROMOTION；必须从 report/ledger 调 canonical performance helper 重算，
   再与 summary 比较。复制 summary 不能返回 `REBUILT`。
5. G1 assembly 成功路径必须生成/验证 `G1_SIGNAL`；G2/replay/selection 不得读未验 signal/summary。
6. `select_per_market.py` 必须读验证后 G2 bundle/summary identity，不得只按固定文件名直接排名。
7. 路径 `resolve()` 后防 symlink 逃逸；写入中间态不得被 PASS 读取。
8. legacy inventory 仍是单独 SHA/状态清单，不伪装成新 bundle。

### 必须测试

- role 全是 `{}` 或任意 `role/value`：拒绝。
- bundle kind 缺必需 role，或 G1 伪称已有 report/gate：拒绝。
- 跨 role market/window/provider/environment/model/contract/candidate 任一错配：拒绝。
- summary SHA 正确但数字与 report/ledger 不符：重算失败。
- symlink 指向 root 外：拒绝。
- 真实 G1/G2 本地 fixture 走生产共用入口建 bundle，并能在空 output 目录确定性重建 summary。
- 集成测试证明 builder/verifier 有非测试调用点。

## 11. IR02：`GATE_PASS` 依赖真 promotion 闭包

### 目标文件

- `quant/code/execution/strategy_release.py`
- `quant/code/execution/research_governance.py`
- `quant/code/ops/research_promotion_gate.py`
- `quant/tests/test_strategy_release_manifest.py`
- `quant/tests/test_extension_calculation_fixes.py`
- `quant/tests/test_research_evidence_bundle.py`

### 实现合同

1. 把 `research_promotion_gate.check_bundle()` 的语义验证提取到 Modal/CLI-free helper，CLI 和 release 使用同一实现。
2. `GATE_PASS` 必须验：preregistration contract、registration time、label/purge/embargo/min-forward、
   contract SHA/candidate/forward dates，frozen prediction/order replay/dated ST/liquidity/cost stress/multiple testing 的 PASS 和底层 SHA，
   以及 IR04 `PROMOTION` bundle。
3. gate/bundle/release 的 market/profile/candidate/contract/source/environment/provider/window 必须一致。
4. 只有 `promotion_eligible=true`、`failed_gates=[]` 且所有身份一致时才可 PASS。
5. 当前三市场无新 PASS；release 状态不变。override/incumbent 路径仍验 reason/scope/gaps/decision/authority/SHA。

### 必须测试

- gate/bundle 是 `{}`、缺 schema/status、eligible=false、failed gates 非空：拒绝。
- 底层 evidence 缺失、SHA 变更、status 非 PASS：拒绝。
- candidate/contract/market/profile/provider/environment/window/bundle SHA 错配：拒绝。
- forward dates 不足、重复/乱序、消费历史样本或包含未来日：拒绝。
- 仅一个类型、身份和底层文件完整的合成 promotion fixture 通过。
- canonical release map 仍为 override/override/incumbent。

## 12. IR05：bear block 日历、账户与拼接

### 目标文件

- `quant/code/research/current_window/current_window_net.py`
- `quant/code/research/current_window/current_window_modal.py`
- `quant/tests/test_current_window_net_performance.py`

### 实现合同

1. driver 从已验 provider calendar + start/end 独立计算 expected traded sessions，不用 block 实际输出构造 expected。
2. 每块绑定 market/profile/contract/provider/calendar、retrain/eval window、expected dates、cash、gross/cost/net/account/schema。
3. block verifier 验日期完整、数组等长、gross-cost=net、canonical 复利 final account、
   payload `final_account/account_return_max_error` 和明确容差。
4. 不得忽略 block account 后从圆整 daily returns 新造替代证据。
5. 拼接前验块间无缺口/重叠，拼接后再验全窗口 calendar/net/cost/account。

### 必须测试

- 删除两块中间一个 trading day：拒绝，复现 bear-gap 探针。
- 重复、乱序、超窗口、block 边界与 dates 不一致：拒绝。
- 只把 `final_account` 改成 1：拒绝，复现 tampered-account 探针。
- 只改 error/cash/daily net/cost/gross 任一项：拒绝。
- 合法多 block 与全窗口 canonical 重算的 dates/cost/net/final account 一致。

## 13. IR06：correction inventory 确定性重生

### 目标文件

- `quant/code/research/current_window/current_window_net.py` 或新增 Modal-free generator
- 必要时增加 `quant/code/ops/` 薄 CLI
- `quant/tests/test_current_window_net_performance.py`
- `quant/data/current_window_backtest/2026-10-16_2026-10-29_correction_inventory.json`

### 实现与测试

1. 纯函数从原 candidate result/account 计算每个单元旧值、净值、差异、source SHA 和排名；复用 canonical helper。
2. 输出确定性 JSON；CLI 只读写，不含第二套公式。
3. 测试在临时空目录重建，与已登记 inventory 逐字节或 canonical payload 完全一致。
4. 改原 account 任一值会改 correction/source identity；NaN/Inf、缺单元、重复 id 失败。
5. 原 candidate result 原字节/SHA 不变。预期仍是 13 单元、`ranking_changed=false`；不一致时停止，不手改凑数。

## 14. IR08：Gate0 期望交易日完整性

### 目标文件

- `quant/code/research/regime_reselection/gate0_diagnostics.py`
- `quant/tests/test_regime_reselection_gate0.py`

### 实现与测试

1. `reconstruct_paper_series()` 显式接收已验 `expected_dates` 或 calendar + start/end。
2. 验应有 report 的每个 session：缺失、额外、重复、乱序。默认 fail-closed；如审计允许缺日，
   必须显式参数化并返回 `INCOMPLETE` + missing dates，不得 PASS。
3. 不用“下日 pre-open 必须等于上日 close”作为唯一连续条件，因隔夜重估可合法改变市值。
4. 保留 duplicate/unsorted/NaN/Inf/bool/非正 NAV 拒绝。
5. 期望 10-19/20/21 但只给 10-19/21：拒绝或 `INCOMPLETE`；完整日期通过；
   额外/乱序/重复失败；有合法隔夜重估的完整序列通过。

## 15. 必须运行的本地验证

定向套件：

```bash
.venv/bin/python -m pytest -q \
  quant/tests/test_regime_reselection_cache_identity.py \
  quant/tests/test_regime_reselection_spine_jobs.py \
  quant/tests/test_regime_reselection_gate0.py \
  quant/tests/test_current_window_net_performance.py \
  quant/tests/test_strategy_release_manifest.py \
  quant/tests/test_research_identity.py \
  quant/tests/test_research_evidence_bundle.py \
  quant/tests/test_training_window.py \
  quant/tests/test_extension_calculation_fixes.py \
  quant/tests/test_csi1000_production_config.py \
  quant/tests/test_csi1000_mvp_contract.py \
  quant/tests/test_lineage_market_binding.py \
  quant/tests/test_website_simulation.py
```

全量 extension：

```bash
.venv/bin/python -m pytest -q quant/tests
```

格式/工作区：

```bash
.venv/bin/python -m black <本轮变更的 Python 文件> -l 120 --check
git diff --check
git status --short --branch
```

如 sandbox 不允许 Black 多进程，按文件串行检查并在报告说明命令/范围；不把环境失败写成全量 PASS。

本轮不运行 Modal。所有 identity/bundle/release/bear/Gate0 回归先在 Modal-free 本地模块闭合。
若 Codex 验收后用户另行授权云端验证，也必须是有界、前台 attached 运行，严禁 `modal run --detach`。

## 16. 实施报告必须补充的内容

继续更新原实施报告，不新建一份互相矛盾的 PASS 报告。增加“独立复审二次整改”章节，包含：

1. IR01–IR08 状态矩阵，只用 `LOCALLY_FIXED / PARTIAL / BLOCKED`，附代码/测试证据。
2. 每个原负向探针的“修复前失败→修复后通过”记录，不只列最终绿测试。
3. strategy/environment/job/assembly/bundle/release schema 版本及迁移边界，说明 legacy 为何不能命中新 gate。
4. 三市场 release ID/status/contract SHA/manifest SHA，确认 profile/TopK/DropN/费用/cash 未变。
5. correction inventory 重生命令、source SHA、13 个单元、最大误差和 `ranking_changed`；旧文件 SHA 不变。
6. 定向/全量 pytest 完整计数、Black 实际范围、`git diff --check`、最终 status。
7. 明确 `NOT COMMITTED / NOT PUSHED / NOT CLOUD-REPLAYED / NOT DEPLOYED / NOT BROKER-CERTIFIED`。
8. 留给 Codex 的负控重跑、diff、真实调用点和证据边界验收项。

不得将状态改为 `INDEPENDENTLY VERIFIED`、`DEPLOYABLE` 或 `PRODUCTION PASS`。

## 17. 停止条件

以下任一情况出现时停止并报告，不自行扩大范围：

- 需修上游 Qlib 才能关闭问题；
- 当前三市场 release/profile/账户合同与交接不一致；
- correction inventory 重算后排名改变，或原 artifact SHA 不匹配；
- 需云端数据/Modal 才能让本地负控通过；
- 需补造 legacy report/ledger/gate 才能形成 bundle；
- 工作区出现无法安全合并的其他用户修改。

## 18. 原交接时发给 DeepSeek 的简短 prompt（历史，不直接用于当前工作区）

```text
请在当前仓库根目录实施研究回测与策略择优的独立复审整改。

先完整阅读根目录 AGENTS.md，以及：
1. quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-review.md
2. quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-remediation-handoff.md
3. quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-implementation-report.md
4. quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md
5. quant/docs/07-code-review/2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md

当前 HEAD 是 4f4163bb1，RR01–RR09 首轮实现和 Codex 复审文档都在未提交工作区。
先运行 git status --short --branch、git log -4 --oneline、git diff --check，保护所有 tracked/untracked 修改。
严禁 reset/restore/clean，不得只在纯 HEAD 上重做，只修改 quant/。

严格按最后一份 handoff 关闭 IR01–IR08。先把 Codex 的负向探针变成当前会失败的回归测试，
保留失败证据，再修实现。优先完成 canonical strategy/environment identity，再修 job/assembly，
再建立并接入分层语义 evidence bundle，最后让 GATE_PASS 必须依赖真实 promotion 闭包；
同时修复 bear calendar/account、correction inventory 重生和 Gate0 expected-session 校验。

保留 RR06、RR08、NaN/Inf 门禁、legacy 原字节/SHA、当前三市场 release 状态和所有生产/账户合同。
不重新选策略，不修改 profile/TopK/DropN/费用/本金/执行时序，不补签 legacy 证据，不放宽 gate。

完成后运行 handoff 列出的定向套件、全量 .venv/bin/python -m pytest -q quant/tests、
本轮 Python 文件 Black 120 检查和 git diff --check。更新原实施报告中的 IR01–IR08 状态、
修复前失败/修复后通过证据、schema 迁移和未验证边界。

不要提交、push、运行 Modal、部署 App 或写生产 Volume。完成本地实施与报告后停止，交由 Codex 独立验收。
```
