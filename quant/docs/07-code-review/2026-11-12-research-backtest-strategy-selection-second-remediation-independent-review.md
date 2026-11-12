# 研究回测与策略择优二次整改独立验收报告

> 日期：2026-11-12
>
> 验收基线：Git HEAD `4f4163bb1`，叠加 DeepSeek 的 RR01–RR09 与 IR01–IR08 未提交工作区修改
>
> 原验收状态：`INDEPENDENT ACCEPTANCE FAILED / REMEDIATION REQUIRED`
>
> 后续状态（2026-11-12）：`LOCAL REMEDIATED / LOCAL INDEPENDENTLY RE-VERIFIED / COMMITTED / PUSHED / SOURCE IN PRODUCTION IMAGE / RESEARCH CLOUD REPLAY NOT VERIFIED`。
>
> 原二次实施者：DeepSeek；原独立验收及 AR01–AR09 后续整改：Codex
>
> 关闭报告：[`2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md`](2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md)
>
> 二次整改合同：[`2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md`](2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md)
>
> 性质：本地研究代码、证据链和策略发布门禁的独立复验；不是策略晋级、Modal 执行、生产部署或券商成交认证。

## 1. 验收结论

本报告以下第 2–13 节保留 **Codex 接手整改前** 的独立失败证据：当时不能验收通过。IR07 的实际 Qlib/依赖环境身份已达到本地合同，IR05、IR06、IR08 也各有一部分
实现有效；但独立负向探针仍确认 6 个 P1 和 3 个 P2 缺口。最直接的阻断是：真实 G1 非 smoke 路径
会把错误的 identity 对象传给 assembler，且 worker 写出的 slice receipt schema 与 validator 不一致；即使
单元测试全绿，实际分片也无法完成有效 assembly。

Codex 随后按“先失败回归、再修实现”的顺序完成 AR01–AR09；当前关闭结论见第 14 节和关闭报告。保留原始
`FAILED` 表格与探针，是为了记录二次实施为何未通过，并不代表当前工作区仍存在这些缺口。

| 原 ID | 独立结论 | 状态 |
|---|---|---|
| IR01 | assembler 调用参数错误，slice receipt producer/validator schema 不一致 | `FAILED / P1` |
| IR02 | 空业务 payload 的 PROMOTION bundle 仍可授权 `GATE_PASS` | `FAILED / P1` |
| IR03 | canonical contract 仍以版本字符串代替实际规则实体，声明版本可篡改 | `FAILED / P1` |
| IR04 | bundle 只验证通用外壳；G2 真实路径仍可绕过，summary 比较不完整 | `FAILED / P1` |
| IR05 | 日历、成本和账户核心校验有效，但 block 身份未绑定、可跨市场拼接 | `PARTIAL / FAILED / P1` |
| IR06 | generator 可重生当前文件，但缺单元仍可生成 PASS inventory | `PARTIAL / FAILED / P2` |
| IR07 | 实际 import root、`.py/.pyx/.so` 和必需依赖版本本地验证通过 | `LOCAL PASS / EXTERNAL UNVERIFIED` |
| IR08 | helper 可验 expected sessions，但真实 CLI 未传入 expected dates | `PARTIAL / FAILED / P2` |

另有一个跨 IR02/IR03 的 P2：release evidence 路径未在 `resolve()` 后校验 root containment，指向仓库外的
symlink 仍能通过。

在这些问题关闭前，候选状态只能写为：

```text
LOCAL IMPLEMENTED / INDEPENDENT ACCEPTANCE FAILED / NOT COMMITTED / NOT DEPLOYED
```

## 2. 工作区与验证边界

- HEAD 仍为 `4f4163bb1`，本地 `main` 领先 `origin/main` 3 个提交；本轮候选仍在未提交工作区。
- 验收过程中未执行 `reset`、`restore`、`clean`，未覆盖 DeepSeek 或此前修复。
- 原二次验收时 Codex 只新增/更新审查文档；后续 AR01–AR09 整改已修改候选实现、测试、数据和 release JSON。
- 原二次验收阶段未运行 Modal、未部署 App、未写生产 Volume、未提交或 push。
- 当时现网生产端不受这些尚未部署研究候选缺陷影响；后续 AR01–AR09 已整改并随源码部署，研究晋升仍须独立证据。

本地命令结果：

```text
.venv/bin/python -m pytest -q <11 个交接定向测试文件>
167 passed, 1 warning in 3.84s

.venv/bin/python -m pytest -q quant/tests
1234 passed, 2 skipped, 24 warnings, 50 subtests passed in 42.42s

.venv/bin/black quant/code quant/tests -l 120 --check --exclude qlib/_version.py
250 files would be left unchanged

git diff --check
PASS
```

全绿测试只能证明现有断言通过；下列探针直接经过生产共用 validator 或检查真实入口调用，仍能复现缺口。

## 3. AR01（P1，IR01）：真实 G1 assembly 路径自相矛盾

### 证据

`spine_and_replay_modal.py` 同时构造了 `identity_contexts` 和完整的 `assembly_identities`，但实际汇总调用传入：

```python
assemble_spine.remote(..., identity_contexts.get((market, spine)))
```

`assemble_spine()` 随后把该对象当成完整 assembly identity，并由 `validate_slice_set()` 比较
`run_id/run_end/profile/horizon/seed/runtime/...`。`identity_contexts` 不含这些字段，因此合法切片也会在第一个
`run_id` 比较处失败。

同时，`fit_slice()` 写出：

```python
{"schema_version": 1, "kind": "g1_spine_slice_v2", ...}
```

而 `spine_identity.SCHEMA_VERSION == 2`，`validate_slice_receipt()` 与
`validate_slice_receipt_self()` 都要求 schema 2。worker 虽可返回刚写入的 dict，但恢复扫描和 assembler
会拒绝该 receipt。

独立探针：

```text
PROBE_IR01_MAIN_ASSEMBLY_ARGUMENT=identity_contexts.get((market, spine))
PROBE_IR01_SLICE_RECEIPT_SCHEMA_PRODUCER=1:VALIDATOR=2
```

### 必须整改与验证

1. 实际调用传 `assembly_identities[(market, spine)]`，并在 assembler 入口验证 assembly identity 自哈希。
2. slice receipt 的 producer 使用 `spine_identity.SCHEMA_VERSION`，禁止再次手写 schema 常量。
3. 新增一个经过真实 coordinator 参数构造、producer receipt 和 assembler 共用 validator 的 Modal-free
   集成回归；必须证明非 smoke 路径可以 assembly，而不是只测独立 helper。
4. 回归同时覆盖恢复扫描：刚生成的 receipt 必须可被 `completed_jobs()` 和 assembler 接受；schema 1 仍拒绝。

## 4. AR02（P1，IR04）：bundle v2 未做 role 业务语义验证

### 证据

`research_bundle._load_role()` 只检查 `schema_version/kind/status/identity`，不校验任何 role 的 `payload`。
因此 research contract、provider、calendar、model、strategy、receipt 和 signal 全部使用空 payload 仍可建成并
验证 G1 bundle：

```text
PROBE_IR04_G1_ALL_EMPTY_SEMANTIC_PAYLOADS=True:ACCEPTED
```

`rebuild_summary()` 虽由 report/ledger 重算部分值，但只比较总收益、总成本、最终账户、误差和几个 identity。
已声明的 `dates`、`daily_net_return` 等字段可与重算结果矛盾而仍返回 `REBUILT`：

```text
PROBE_IR04_FALSE_SUMMARY_DATES_AND_DAILY_NET=ACCEPTED
```

当前 `test_arbitrary_role_payload_is_rejected` 把整个 role 文件改成 `{}`，只证明外壳缺失会失败；没有覆盖
“外壳合法但业务 payload 为空/伪造”的原始复现场景。

### 必须整改与验证

1. 为每个 role 建立具体 validator，至少验证必需字段、类型、lowercase SHA、窗口和底层 artifact 身份；空
   payload 必须逐 role 失败。
2. `signal/report/ledger/gate` role 必须验证其引用文件本身的 path、SHA 和语义 receipt，不能只把 SHA 当字符串。
3. `rebuild_summary()` 必须比较完整 canonical summary，或完全忽略声明 summary 并只输出重算结果，同时明确
   bundle 内声明与重算结果完全一致后才能 PASS；日期、逐日 gross/cost/net/account 都须覆盖。
4. 回归必须保留合法外壳，只逐项清空或伪造 payload，不能再用整个 `{}` 代替语义负控。

## 5. AR03（P1，IR04）：G2/selection 真实路径仍可绕过 bundle

### 证据

- `select_per_market.py` 的 `--g2-bundle` 没有 `required=True`；不提供时继续读取固定文件名。
- 即使提供 bundle，代码只调用 `verify_bundle()` 检查 kind，随后仍从 `g1_baseline_summary.json` 等固定文件
  排名，没有从验证后的 bundle 读取并绑定 summary。
- `quant/code` 内只有 G1 成功路径调用 `build_bundle()`；没有生产/研究调用点创建 G2 bundle。
- `rebuild_summary()` 在非测试代码中无调用点。

独立探针：

```text
PROBE_IR04_G2_BUNDLE_REQUIRED=False
```

### 必须整改与验证

1. 选择入口必须要求一个或多个已验证 G2 bundle，候选数据只能来自 bundle 中验证后的 summary/identity。
2. 为实际 replay/G2 成功路径接入 G2 builder；未生成 bundle、身份不一致或 summary 重建失败时不得排名。
3. 增加端到端本地 fixture：G1 signal → G2 report/ledger → G2 bundle → 空目录重建 summary → selection；删除
   bundle 或替换固定 summary 时必须失败。

## 6. AR04（P1，IR02）：空 PROMOTION 业务闭包仍可授权 `GATE_PASS`

### 证据

`strategy_release.validate_release()` 确实调用了 promotion gate 和 `verify_bundle()`，这是有效进展；但 bundle
没有 role 语义校验，且 release 只对比 `market/profile/strategy_contract_sha256`。bundle common identity 不含
`candidate_id`、gate SHA、report/ledger/summary SHA，gate、bundle 和 release 没有形成同一闭包。

真实 validator 接受了这样一个合成 release：promotion gate 的外部文件可过当前 gate；PROMOTION bundle
所有 19 个 role 的 `payload` 都是 `{}`：

```text
PROBE_IR02_GATE_PASS_ALL_PROMOTION_PAYLOADS_EMPTY=True:ACCEPTED
```

### 必须整改与验证

1. 先完成 AR02 的 per-role 语义验证，再让 release 校验 gate/bundle/release 的
   market/profile/candidate/contract/source/environment/provider/window 和所有底层 SHA 一致。
2. promotion evidence 的每个 receipt 不能只验证一个任意文件的 SHA；必须验证 receipt schema、kind、PASS、
   identity 及其真正引用的 frozen predictions/order replay/ST/liquidity/cost/multiple-testing artifact。
3. `GATE_PASS` 回归必须证明任一 role 空 payload、candidate 错配、window/provider/environment 错配、底层
   receipt 或 artifact 替换均失败。

## 7. AR05（P1，IR03）：strategy contract 仍未绑定完整业务实体

### 证据

`canonical_strategy_contract()` 已增加 label、PIT、selection、fees 和 execution 字段；但 board limits、上市日
豁免、整手和流动性仍只是字符串，例如 `cn_equity_board_date_aware_v2`，没有把实际规则参数/表和缺报价行为
实体纳入 hash。新增或改变实际规则实体字段时 SHA 不变：

```text
PROBE_IR03_UNMODELED_RULE_ENTITY_SAME_SHA=True
```

此外 release 顶层 `contract_version` 不在必需字段中，canonical builder 也不校验它是否等于声明版本。篡改并
重新 seal 后仍通过：

```text
PROBE_IR03_TAMPERED_CONTRACT_VERSION=ACCEPTED
```

### 必须整改与验证

1. 由 board/date limits、listing exemptions、quantity/lot、liquidity/missing-quote、fee/account 的唯一实际
   canonical builder 生成实体；版本和完整实体同时进入 strategy hash。
2. release 声明的 `contract_version` 必须存在且与 canonical version 严格一致。
3. 不得靠人工更新版本字符串替代规则变化自动失效；逐一改变实际规则参数时 SHA 必须变化。
4. 对 candidate/model/contract 等 SHA 字段统一验证 64 位小写十六进制，而非仅非空。

## 8. AR06（P1，IR05）：bear block 可跨市场、策略和 provider 拼接

### 已有效部分

- driver 已从 provider calendar 独立计算 expected sessions。
- block 的 gross/cost/net、initial/final account 和 account error 已校验；缺日和篡改账户负控可拒绝。

### 残余证据

`bear_block_payload()` 不绑定 market/profile/contract/provider/calendar/window identity，
`combine_bear_blocks()` 也完全忽略这些字段。两个日期不重叠但来自不同市场、profile、provider 和 contract 的
block 可被拼成一个 PASS 结果：

```text
PROBE_IR05_MIXED_MARKET_PROFILE_PROVIDER_CONTRACT_BLOCKS=ACCEPTED
```

### 必须整改与验证

1. block schema 必须绑定 market/profile/strategy contract/provider/calendar、retrain/eval window 和 expected dates。
2. combine 接收 coordinator 期望 identity，逐块比较完整不变量，并验证 block 边界与日期完全一致。
3. 增加仅改变上述任一 identity 字段的负控；账户和日期均合法时也必须拒绝混拼。

## 9. AR07（P2，IR06）：correction generator 未拒绝缺单元

### 已有效部分

当前 13 单元文件可由 generator 确定性重建，source SHA、排名和原文件字节一致；重复 key、非有限值和缺字段
也可失败。

### 残余证据

generator 只要求非空 list，没有校验已登记的 13 个 `(market, profile, topk, nd)` 单元集合。删除一个单元后
仍生成正常 inventory：

```text
PROBE_IR06_MISSING_UNIT=ACCEPTED:12_UNITS
```

必须把期望单元集合/version 纳入 generator 合同和输出 identity；缺失、额外或替换任一单元都失败。测试需从
13 单元 fixture 删除一项直接复现。

## 10. AR08（P2，IR08）：Gate0 helper 已修，但真实 CLI 未接入 expected sessions

`reconstruct_paper_series(expected_dates=...)` 的缺日、额外日和 incomplete 行为已通过本地测试；但
`gate0_diagnostics.main()` 仍调用：

```python
reconstruct_paper_series(artifacts)
```

没有从已验 provider calendar 与窗口构造/传入 `expected_dates`，因此真实 CLI 对缺交易日仍可返回 PASS。

```text
PROBE_IR08_RECONSTRUCT_CALL_KEYWORDS=[[]]
```

CLI 必须要求可验证的 calendar/window 输入，为三市场构造 expected sessions 并显式传入；缺 provider/calendar
或无法确定窗口时失败关闭。新增测试必须调用真实 `main()`，而不是只调用 helper。

## 11. AR09（P2，跨 IR02/IR03）：release evidence symlink 可逃逸 root

`strategy_release._relative_file()` 只拒绝绝对路径和字面 `..`，没有对 `resolve()` 后的路径执行 root
containment。仓库内相对 symlink 指向仓库外文件时，SHA 正确即可通过：

```text
PROBE_GLOBAL_RELEASE_EVIDENCE_SYMLINK_ESCAPE=ACCEPTED
```

`research_promotion_gate.check_bundle()` 对 evidence 内的相对 path 也没有 root containment。两处应复用一个
fail-closed path helper，拒绝绝对路径、`..` 和 resolve 后 symlink 越界，并增加真实 validator 回归。

## 12. IR07 本地关闭证据与外部边界

`research_identity.environment_manifest()` 已从实际 `qlib.__file__` 解析 package root，校验声明 checkout，
哈希实际树中的 `.py/.pyx/.so`，并记录 Python、pyqlib、NumPy、Pandas、LightGBM 和 PyArrow 版本；错误 import
root 和缺依赖会失败。本地测试及代码复核支持将 IR07 标记为 `LOCAL PASS`。

仍未在 Modal 真实 worker/image 中复核 import root 和扩展产物，因此只能写
`LOCAL PASS / NOT CLOUD-VERIFIED`，不能写已部署或生产验收。

## 13. 下轮最低验收门槛

1. 将本报告 AR01–AR09 的复现场景先加入回归，并保留修复前失败输出。
2. 修复后定向测试、全量 `quant/tests`、Black 和 `git diff --check` 全绿。
3. 独立重复本报告全部探针，预期 `ACCEPTED` 项全部变为 fail-closed。
4. 至少增加真实入口级测试：G1 coordinator→receipt→assembly、G2 builder→rebuild→selection、Gate0 CLI。
5. 保持三市场 profile、TopK/DropN、1M 账户、费用、20-session 重训和 T-close→T+1-open 不变。
6. 本地验收通过前不运行 Modal；本地通过后是否做一次集中、attached、有界云端 probe 需另行授权。
7. 本轮仍不得提交、push 或部署，除非 Codex 再次独立验收通过且用户明确授权。

## 14. AR01–AR09 后续关闭结果（2026-11-12）

Codex 已完成实现并再次独立复验。11 个新增复现场景在修复前为 `11 failed`，修复后与完整测试共同通过：

```text
.venv/bin/python -m pytest -q quant/tests
1256 passed, 2 skipped, 24 warnings, 50 subtests passed in 43.57s

本轮涉及的 18 个 Python 文件逐文件 Black --check：PASS
git diff --check：PASS
```

| ID | 当前状态 | 关闭摘要 |
|---|---|---|
| AR01 | `LOCAL PASS` | 真实 coordinator、v2 producer、assembler 与恢复校验使用同一完整 assembly identity 和自哈希 |
| AR02 | `LOCAL PASS` | per-role 业务语义、实际 signal 附件、完整逐日 summary/account 对账 fail-closed |
| AR03 | `LOCAL PASS` | G2 builder 接入真实路径，selection 只消费已验证 bundle summary |
| AR04 | `LOCAL PASS` | gate/bundle/release/prereg/evidence/底层 artifact 形成同一身份与 SHA 闭包 |
| AR05 | `LOCAL PASS` | strategy SHA 绑定实际规则实体和严格 contract version/SHA 格式 |
| AR06 | `LOCAL PASS` | bear block 与 combine 绑定市场、策略、provider、calendar、窗口和账户序列 |
| AR07 | `LOCAL PASS` | versioned 13-unit registry 拒绝缺失、额外和替换单元 |
| AR08 | `LOCAL PASS` | 真实 Gate0 CLI 从 provider calendar 推导并传入 expected sessions |
| AR09 | `LOCAL PASS` | resolve 后 root containment 拒绝 release/promotion evidence symlink 越界 |

当前已关闭本地代码与回归、提交推送并纳入生产镜像；真实三市场研究云端 replay、研究晋升验收和券商认证仍未完成。完整
实现、release 新 SHA、探针和边界见
[`Codex 整改报告`](2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md)。
