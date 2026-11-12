# 研究回测与策略择优独立验收整改清单

> 日期：2026-11-11
>
> 验收基线：Git HEAD `4f4163bb1`，叠加 DeepSeek 的 RR01–RR09 未提交工作区修改
>
> 原独立验收状态：`INDEPENDENT REVIEW FAILED / REMEDIATION REQUIRED / LOCAL ONLY / NOT COMMITTED / NOT DEPLOYED`。
>
> 后续状态（2026-11-12）：Codex 的 AR01–AR09 整改已本地复验、提交推送并纳入生产 v26 镜像；研究云端 bounded replay 与策略晋升仍待独立验证。见[最终整改报告](2026-11-12-research-backtest-strategy-selection-codex-remediation-report.md)。下文“待修复”“不得提交”等均指原验收基线。
>
> 实施者：DeepSeek；独立验收：Codex
>
> 二次整改交接：[`2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md`](2026-11-12-research-backtest-strategy-selection-independent-remediation-handoff.md)
>
> 性质：研究回测、证据包和策略发布整改的独立复审；不是策略晋级、Modal 执行、生产部署或券商成交认证。

## 1. 验收结论

本轮不能验收通过，也不能将候选工作区提交或部署。定向测试和全量 `quant/tests`
全绿，但独立负向探针可绕过五个核心门禁：

| ID | 级别 | 结论 | 状态 |
|---|---|---|---|
| IR01 | P1 | spine assembly 只比较 6 个公共字段，不同 seed/训练窗口/runtime 的 slice 仍可混合 | 已复现，待修复 |
| IR02 | P1 | `GATE_PASS` 只验文件存在和 SHA，空 gate/bundle JSON 也能授权 PASS | 已复现，待修复 |
| IR03 | P1 | 策略合同哈希不完整，且 CSI300/CSI500 仍携带 CSI1000 Stage-B 合同版本 | 已复现，待修复 |
| IR04 | P1 | evidence bundle 只是通用字节容器，不验语义、不重算 summary，也未接入实际路径 | 已复现，待修复 |
| IR05 | P1 | bear 拼接的期望日历来自实际输出，且忽略 block 的 account 校验证据 | 已复现，待修复 |
| IR06 | P2 | correction inventory 没有可重跑生成器，测试只读已写入数字 | 证据缺口 |
| IR07 | P2 | Qlib environment digest 只哈希 checkout 中的 `*.py` | 已复现，待修复 |
| IR08 | P2 | Gate0 不校验期望交易日完整性 | 已复现，待修复 |

在 IR01–IR05 关闭前，实施状态只能写为：

```text
LOCAL IMPLEMENTED / INDEPENDENT REVIEW FAILED / NOT COMMITTED / NOT DEPLOYED
```

原验收时候选变更尚未提交、未部署，当时生产 v20 不受本轮新问题影响，不需要生产回滚；该候选当时在再次验收前不得部署。

## 2. 工作区与不变边界

- HEAD 为 `4f4163bb1`，本地 `main` 领先 `origin/main` 3 个提交；RR01–RR09 仍是未提交修改。
- Codex 本轮只更新审查文档，未修改 DeepSeek 的实现、数据或测试。
- 未运行 Modal、未部署、未写生产 Volume、未提交或 push。
- 下轮必须保留已有 R/S/PC/PR/CR 修复、RR06、RR08、非有限数门禁、legacy SHA、三市场与账户合同。
- 禁止 `reset --hard`、`restore`、`clean` 或只在纯 HEAD 上重做；只修改 `quant/`。

## 3. IR01（P1）：assembly 混合不同实验身份

### 证据

`spine_identity.py` 的单个 `job_identity` 包含 seed、`fit_start`、`data_start`、threads 和 runtime；但
`assemble_spine()` 只比较 source/environment/provider/model/strategy 的 6 个字段。
`validate_slice_receipt_self()` 也没有重算 `job_identity.sha256/job_sha256`。因此两组日期不重叠、但 seed
不同的 receipt 可被同一 assembly 接受：

```text
PROBE_RR01_MIXED_SEED=ACCEPTED
```

### 修复与验证

1. 定义版本化 `assembly_identity`，包含所有跨 slice 应不变的 run/market/spine/profile/horizon/
   data+fit window/seed/threads/runtime/source/environment/provider/model/strategy contract 字段。
2. `retrain_asof/signal_end` 等可变字段仍必须进入各自 job identity 和有序 receipt 列表。
3. 重算 canonical job SHA，要求 receipt `job_sha256`、`job_identity.sha256` 与重算值一致。
4. coordinator 必须传入期望 assembly identity，不能用第一个 slice 自行定义期望身份。
5. 回归覆盖：仅改 seed/profile/fit+data start/threads/kernels/backend 任一字段时拒绝；合法不同
   retrain dates 可组装；篡改 identity 后重新 seal 仍拒绝；必须执行生产 assembly 共用 helper。

## 4. IR02（P1）：任意 JSON 可授权 `GATE_PASS`

### 证据

`strategy_release.validate_release()` 对 `GATE_PASS` 只校验路径存在和 SHA256，不解析 gate receipt，
不调用 canonical promotion gate，也不验 evidence bundle。当前测试明确用 `{}` 创建 gate/bundle 并断言通过：

```text
PROBE_RR03_EMPTY_GATE_AND_BUNDLE=ACCEPTED
```

### 修复与验证

1. `GATE_PASS` 必须解析 research contract/promotion evidence/底层 receipts，要求 `promotion_eligible=true`、
   failed gates 为空，candidate/contract/release 身份一致。
2. 复用 `research_governance.promotion_gate()` / `research_promotion_gate.check_bundle()` 的共享核心验证器，
   并调用 IR04 修复后的 bundle verifier。
3. 保持 CSI300/500=`RISK_ACCEPTED_OVERRIDE`、CSI1000=`INCUMBENT_RETAINED`，不得为测试改成 PASS。
4. 回归覆盖 `{}`、缺 schema/status、failed gate、identity 错配、底层文件损坏；仅语义完整且
   真正过 gate 的 fixture 可通过。

## 5. IR03（P1）：策略合同闭包不完整

### 证据

`REQUIRED_CONTRACT_FIELDS` 未纳入完整 label expression/PIT 语义、board/date-aware 涨跌停、上市日豁免、整手、
流动性和其他实际执行参数。改变这些字段时 SHA 不变：

```text
PROBE_RR04_OMITTED_CONTRACT_FIELDS_SAME_SHA=True
```

`profile_manifest()` 还对 CSI300/500 设置 `contract_version=csi1000_stage_b_forward_v1`：

```text
PROBE_RR04_CSI300_CONTRACT_VERSION=csi1000_stage_b_forward_v1
```

### 修复与验证

1. 从实际生产执行/账户 canonical manifest 生成 strategy contract，纳入所有可改变信号、排名、下单、
   成本或 NAV 的字段，不手工复制子集。
2. 校验类型/范围，拒绝负 topk/dropn、非法费率、未知 timing 等重新 seal 后的无效值。
3. 分离通用 production contract version 和 CSI1000 Stage-B lineage；CSI300/500 不得有任何 CSI1000 Stage-B 标识。
4. 逐一改 label/PIT/universe/limits/lot/fees/timing/TopK/DropN 时 SHA 必须改变；无关文档/资源不得改变。

## 6. IR04（P1）：bundle 只校验字节完整性

### 证据

`research_bundle.py` 要求 10 个 role 存在并匹配 SHA，但不解析 role schema 或互相身份。
`rebuild_summary()` 只复制已有 summary，未由 report/ledger 重算：

```text
PROBE_RR05_ARBITRARY_CONTENT_BUNDLE=ACCEPTED REBUILT {}
```

`rg` 确认 builder/verifier/rebuild 在 `quant/code` 中无实际研究调用点，只被新单元测试调用。

### 修复与验证

1. 为每个 role 定义版本化 schema，互相绑定 source/provider/calendar/environment/model/strategy/signal/
   report/ledger/summary/gate 的 identity 和窗口。
2. `rebuild_summary()` 必须由验证后的 report/ledger 调用 canonical 计算路径重算，再与包内 summary 比较。
3. G1/G2 新运行和未来 `GATE_PASS` 必须在成功路径创建并验证 bundle；缺包则失败关闭。
4. legacy 只保持 SHA + uncertified/withdrawn，不补签；bundle 路径解析后必须拒绝 symlink 越界。
5. 回归覆盖任意 `{}`、role 身份错配、summary 数字错误、成功路径缺 bundle 和 symlink 逃逸。

## 7. IR05（P1）：bear 续接不验真实日历与 block account

### 证据

`bear_market_driver()` 用实际 block `dates` 拼出 `expected`。中间缺一个交易日时，“期望”也同时缺少：

```text
PROBE_RR02_BEAR_GAP=ACCEPTED
```

`combine_bear_blocks()` 还忽略 block 的 `final_account/account_return_max_error`，而是从已圆整 daily net
新造 account 再自我验证：

```text
PROBE_RR02_TAMPERED_BLOCK_ACCOUNT=ACCEPTED input_final=1.0 output_final=101899999.99999999
```

### 修复与验证

1. driver 从已验 provider calendar 和请求 start/end 独立导出期望 traded-session 列表，不从 block 输出反推。
2. 每块验证 start/end/dates、gross-cost-net、`final_account`、`account_return_max_error`、初始本金和 schema。
3. 对 JSON 圆整使用明确且严格的误差合同；拼接后再验全窗口日历、净收益和 account 路径。
4. 回归覆盖缺日/重复/乱序/超窗口、block 边界错配，以及只篡改 account/error/cash/daily net 的情况。

## 8. IR06–IR08（P2）：证据与完整性缺口

### IR06：correction inventory 未真正可重现

当前测试只读 committed inventory，断言 SHA、13 个单元和 `ranking_changed=false`；仓库内没有从原
candidate result/account 重生 inventory 的 generator。需增加纯函数/CLI，在临时目录重建并与已登记
inventory 逐字节/逐字段比较，保留原 artifact 原字节。

### IR07：environment identity 未完整绑定实际 Qlib

`_qlib_source_digest()` 在 repo 存在 `qlib/` 时只扫 `*.py`，不核对实际 `import qlib` 的 package root，
也不纳入 `*.pyx`/实际加载扩展产物：

```text
PROBE_RR09_PYX_CHANGE_SAME_SHA=True
```

需绑定实际导入的 package/distribution，校验其与声明 checkout/image 一致，并对影响运行的 Python、
Cython 源和已加载扩展产物生成不含绝对路径的清单及哈希。修改 `.py/.pyx/实际扩展产物`
任一项均应改变身份，意外导入路径应失败关闭。

### IR08：Gate0 不校验期望交易日

`reconstruct_paper_series()` 只验已存日期有序/唯一，不接收 expected calendar/window。删除 2026-10-20：

```text
PROBE_RR07_MISSING_SESSION=ACCEPTED PASS 2026-10-19 2026-10-21
```

调用方需传入已验 calendar 和期望窗口，校验每个应有 report 的 session。若合同允许缺日，必须返回
`INCOMPLETE` 并记录缺口，不得 PASS。不要仅用相邻 open/close 数值相等判定连续性，隔夜重估可合法改变
pre-open value。

## 9. 已确认有效的修复

- RR06：`fit_start` 已进入 handler fit window、train segment、job identity 和 receipt。
- RR08：jobs/results 数量不等已失败，不再被 `zip()` 静默截断。
- RR07 核心门禁：NaN/Inf/bool/非正 NAV、重复或乱序日期的现有负控已生效。
- 13 个 inventory 的当前数值和原文件 SHA 已核对，`ranking_changed=false`；缺的是可重跑生成链。
- legacy inventory 的原件 SHA 均匹配，G1/G2/bear 未被错标为已认证。
- 三市场状态正确：CSI300/500 是 `RISK_ACCEPTED_OVERRIDE`，CSI1000 是 `INCUMBENT_RETAINED`。
- 本轮没有改变两个生产 App、Provider 2/8 候选、Website incremental 4/12 候选或三市场账户合同。

## 10. 本地验证记录

```text
定向套件：146 passed, 1 warning in 2.81s
.venv/bin/python -m pytest -q quant/tests
1213 passed, 2 skipped, 24 warnings, 50 subtests passed in 41.35s
```

- 本轮变更/新增 Python 文件逐文件 Black 120 检查通过。
- aggregate Black 受限于 sandbox multiprocessing；这是环境失败，不冒充成功或代码失败。
- `git diff --check` 通过。

测试全绿只证明现有断言通过；IR01–IR05 的独立负控仍被接受，所以整体验收失败。

## 11. 下轮顺序与外部边界

1. 先把本文 8 个探针转成当前会失败的回归测试，保留修复前失败证据。
2. 先修 IR01/IR03/IR07 identity，再修 IR04 bundle，最后让 IR02 PASS 依赖完整闭包；独立修 IR05/06/08。
3. 运行定向套件和全量 `quant/tests`，更新实施报告，然后交给 Codex 再验。
4. 本地负控关闭前不运行付费 Modal。如后续获授权，只做一次集中、有界、前台 attached 验证，
   严禁 `modal run --detach`。
5. 仍为 `NOT CLOUD-REPLAYED / NOT DEPLOYED / NOT BROKER-CERTIFIED`；当前没有新的三市场 `GATE_PASS`，
   也没有重新证明当前策略优于其他候选。
