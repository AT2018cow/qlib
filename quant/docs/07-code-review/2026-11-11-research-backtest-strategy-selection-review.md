# 研究回测与策略择优代码审查（2026-11-11）

> **状态：REVIEW COMPLETE / REMEDIATION OPEN / LOCAL EVIDENCE ONLY / NOT DEPLOYED**
> 审查基线：`4f4163bb1`（`main`，审查开始时工作区干净，领先 `origin/main` 3 个提交）
> 审查边界：只审 `quant/` 扩展；不审上游 `qlib/` 实现；未运行、部署或修改任何 Modal App；未改变生产 profile、账户、provider 或网站。
> DeepSeek 实施要求：[`2026-11-11-research-backtest-strategy-selection-remediation-handoff.md`](2026-11-11-research-backtest-strategy-selection-remediation-handoff.md)。

## 0. 结论

这次审查确认 **5 个 P1、4 个 P2**。没有发现新的标签前视、PIT universe 或当前生产账户会计计算错误；`portfolio_performance.py`、`prelive_revalidation.py`、Stage/L1-L4 的 provider/source gate 仍然是有效保护，必须保留。

但当前研究证据还不能被描述为一个完整、可重复、自动准入的策略发布链：

1. regime spine 的断点缓存会在任务、源码、provider 或时间窗变化后复用旧结果；既有 G1 manifest 实际包含 3 个代码 SHA。
2. 早期 current-window 与 block-chained 熊市脚本把 Qlib 毛收益当成净收益，和“成本已计入”的文档口径冲突。
3. 研究 gate 与生产 profile 没有 fail-closed 的发布桥；CSI300/CSI500 在研究报告判定“不晋级”后仍被人工风险接受并直接切换，代码没有绑定例外批准收据。
4. 两个 current-window 生产 profile 继续携带无关的 CSI1000 Stage-B lineage，属于错误来源声明。
5. 已提交的 G1/G2 汇总没有形成可独立重放的 source/provider/signal/report 证据闭包；研究 chenditc 与生产 overlay 也尚未完成同身份迁移复核。

这些问题**不足以单独推出当前生产策略必须立即回滚**：后续 2026-11-06 prelive replay 已对冻结信号、完整 provider 与生产 paper 引擎做了更严格复验，而且 RR02 的净收益修正不改变 13 个短窗口候选的相对排序。正确处置是：暂停新的策略晋升，把当前 CSI300/CSI500 标为“人工风险接受、非研究 gate PASS”，先修证据链并做一次冻结输入复验；不要把本报告误读为实盘成交认证或收益承诺。

## 1. 严重度与证据口径

| 等级 | 含义 |
|---|---|
| P1 | 可改变研究结果身份、净收益口径或生产晋升结论；下一次策略选择前必须关闭 |
| P2 | 不一定改变当前结论，但会造成配置失真、诊断误判或恢复路径假成功 |

| 证据状态 | 含义 |
|---|---|
| `REPRODUCED` | 已用本地最小反例复现 |
| `CODE_CONFIRMED` | 控制流/字段闭包直接证明，且有现有 artifact/doc 佐证 |
| `EVIDENCE_GAP` | 未证明结果错误，但缺少足以独立认证的证据 |

## 2. Findings 总表

| ID | 等级 | 状态 | 结论 |
|---|---|---|---|
| RR01 | P1 | REPRODUCED | spine slice / assembly 缓存不绑定真实任务身份，可返回旧 profile、horizon、seed、源码或窗口结果 |
| RR02 | P1 | REPRODUCED | current-window 与早期 bear block 使用毛收益，却在 artifact/doc 中声明净收益或成本已计入 |
| RR03 | P1 | CODE_CONFIRMED | 研究晋升 gate 没有接入生产 profile；人工 override 未形成机器可验证的 release receipt |
| RR04 | P1 | CODE_CONFIRMED | CSI300/CSI500 新 profile 被错误标成 CSI1000 Stage-B lineage，且只校验 model hash，不绑定完整策略合同 |
| RR05 | P1 | EVIDENCE_GAP | G1/G2 汇总缺 provider/source/report 闭包，且尚无 chenditc 研究到 overlay 生产的同身份迁移复核 |
| RR06 | P2 | CODE_CONFIRMED | `--fit-start` 只改变 handler fit 起点，模型 train segment 仍硬编码为 2016-01-01 |
| RR07 | P2 | REPRODUCED | Gate-0 NAV 诊断接受 NaN 并返回 `PASS`；研究 JSON 默认也允许写出非标准 NaN |
| RR08 | P2 | REPRODUCED | `failed_jobs()` 对 jobs/results 长度不等静默截断，尾部任务可被当作成功 |
| RR09 | P2 | EVIDENCE_GAP | 当前 research source identity 不绑定 Qlib 源码与关键运行依赖，环境变化可保留同一 engine identity |

## 3. 详细问题与整改验收

### RR01 — spine 缓存身份不完整（P1 / REPRODUCED）

**证据**

- `spine_and_replay_modal.py:106-107` 的路径只有 `run_id/market/spine/retrain_asof`。
- `fit_slice()` 在 `:167-177` 只核对 `status` 与 prediction 文件 SHA；不核对当前 job 的 `code_sha/profile/label_horizon/signal_end/seed/data_start/fit_start`，也没有 provider/model-YAML/source identity。
- `completed_jobs()` 在 `:282-303` 使用相同的弱条件。
- `assemble_spine()` 在 `:307-330` 只核对 slice 数量与 signal SHA；不核对调用方的 `start/end`、calendar identity 或有序 slice receipt 集合。
- 已提交的 `g1_spine_manifest_20261102.json:10-18` 显示同一 run 混入 `fa3f1327`、`1dca5c08`、`2efd232e` 三个代码 SHA。三个提交之间主要是并发、恢复与内存观测变化，不能据此断言数值已错；但 artifact 已经证明“一个 run = 一个源码身份”不成立。

**本地反例**

1. 构造旧 receipt（`old-code/old-profile/h20/seed0`）与当前 job（`new-code/changed-profile/h10/seed99`），prediction 文件 SHA 正确；`completed_jobs.local()` 仍返回该 job 已完成。
2. 构造 2021 窗口的旧 `spine_receipt.json`，以 2024 窗口调用 `assemble_spine.local()`；函数直接返回旧 `start=2021-01-01/end=2021-12-31`。

**影响**

同一个 run-id 的恢复、参数试验或代码修复可能形成混合 spine；缺失 slice 也可能在 mutable provider 更新后补入不同数据。覆盖天数和 prediction 文件 hash 都无法证明这些切片来自同一实验。

**整改**

- 建立 canonical `job_identity` / `job_sha256`，至少绑定：source manifest、clean commit、provider fingerprint/receipt、model YAML、完整 profile（模型+label+TopK/DropN）、market、horizon、dates、data/fit start、seed、threads/runtime。
- artifact 路径包含 identity digest；receipt 保存完整 identity，复用时做完全相等校验。
- assembly receipt 绑定请求窗口、calendar SHA、按日期排序的所有 slice receipt SHA；发现额外/缺失/冲突 slice 必须失败。
- 已有 run 不原地“补签”；保留为 legacy evidence，新的认证 run 使用新 run-id/schema。

**验收测试**

- 任一 identity 字段变化均 cache miss；prediction 文件未变也不能复用。
- 旧/新窗口、额外 stale slice、calendar 改写、provider 改写均失败。
- 断点恢复在相同 identity 下 0 fit，且 assembled signal 字节与首次运行一致。

### RR02 — 毛收益被声明为净收益（P1 / REPRODUCED）

**证据**

- Qlib report 的 `return` 是成本前收益，canonical helper 明确用 `return - cost` 并与 `account` 交叉校验。
- `current_window_backtest()` 在 `current_window_modal.py:193-215` 读取了 `cost`，但 `cumulative_return` 使用 `(1 + return).prod()`；`final_account` 则是成本后账户。
- `bear_block()` 在 `:390-405` 只返回 `report["return"]`，完全丢弃 cost；driver 在 `:469-503` 直接拼接这些毛收益。
- `01-csi1000-csi300-csi500-current-window-candidate-backtest-20261030.md` 把该字段写成“净收益”；`2021-02-18_2024-02-05_bear_market_summary.json` 则声明 `costs included`。

**本地复算**

对已记录的 13 个短窗口结果，用 `final_account / 100_000_000 - 1` 作为真实账户净收益：

| 例子 | 已报告 | account 净收益 | 高估 |
|---|---:|---:|---:|
| CSI1000 `c98856b4` Top10/Drop1 | -13.1405% | -13.3579% | 0.2174pp |
| CSI300 `59b93929` Top20/Drop2 | -8.5531% | -8.7815% | 0.2284pp |
| CSI500 `raw10d_c98856b4` Top10/Drop1 | -12.4369% | -12.6409% | 0.2040pp |

13 个单元高估范围为 **0.1958–0.2284pp**；相对排序不变。早期 bear-block 没有提交完整 cost/report，无法仅用当前仓库精确修回三条历史净值；后续连续账户 G1/G2 已使用正确的 `portfolio_performance()`，应作为正式口径。

**整改**

- current-window/bear 全部调用 canonical `portfolio_performance()`，或至少使用 `return - cost`；有 `account` 时强制 account/return-cost 一致性。
- 短窗口 artifact/doc 更新为成本后数字，并保留 withdrawn 原件；bear-block 结果在可取回冻结报告时重算，否则明确标记 `GROSS_ONLY_WITHDRAWN`，不得继续写“成本已计入”。
- 禁止新的研究模块直接从 `report["return"]` 生成命名为 net 的指标。

**验收测试**

- 人工注入非零 cost：净收益必须低于毛收益且等于 account 路径；漏扣 cost 的旧实现先失败。
- 13 单元更正后排序比较明确记录是否变化。
- bear 每个 block 输出 return/cost/account，拼接前逐块校验。

### RR03 — 研究 gate 未接入生产晋升（P1 / CODE_CONFIRMED）

**证据**

- `research_governance.py:49-87` 和 `ops/research_promotion_gate.py:19-35` 有独立 gate，但生产/研究入口没有调用；除测试外，仓库内唯一调用点是审核 CLI，生产 profile 入口不调用它。
- `15-csi300-csi500-g2-results-20261102.md:10-14,51-70` 明确判定 CSI300/CSI500 不晋级、三市场不切换、CSI300/500 不启动 D6。
- `16-production-cutover-20261102.md:7-15,61-78` 随后直接激活 CSI300/CSI500，且明确“无 shadow”、硬门槛缺口由收益优先取舍接受。
- `csi1000_production_config.py:32-49` 只硬编码 profile/TopK/DropN/lineage，没有 research decision receipt、gate result、override reason/authority 或输入 identity。

**判定**

人工改变风险偏好本身不是代码错误，且文档已披露；缺陷是代码无法区分“研究 gate PASS”与“人工风险接受”，也不能阻止未登记 profile 直接进入 canonical mapping。

**整改**

- 增加版本化 `strategy_release_manifest`：绑定 candidate/profile、完整参数、研究 contract/receipt SHA、provider/source/signal/report identity、每个 gate、shadow 状态，以及显式 override 的原因、范围和批准记录。
- 生产配置只引用 release id；启动/测试时解析并验证 manifest。`GATE_PASS` 与 `RISK_ACCEPTED_OVERRIDE` 使用不同状态，页面和报告不得互换。
- 没有完整 release receipt 的新 profile fail closed；不要让研究 CLI 自动部署生产。

**验收测试**

- 未晋级、缺 shadow 或证据 hash 错误的候选不能以 `GATE_PASS` 激活。
- 显式 override 必须完整、不可变且在 artifact/网站方法页披露；删除任一字段即失败。
- 当前 CSI300/CSI500 迁移为 `RISK_ACCEPTED_OVERRIDE`，CSI1000 为保留 incumbent；不伪造历史 gate PASS。

### RR04 — 新 profile 的 lineage 声明错误（P1 / CODE_CONFIRMED）

**证据**

- `csi1000_production_config.py:15-18` 的全局 lineage 固定为 CSI1000 Stage-B commit/token/path/contract。
- CSI300/CSI500 current-window profile 定义在 `:140-173`；`profile_manifest()` 在 `:189-203` 无条件给所有 profile 加上上述 CSI1000 Stage-B lineage。
- 本地调用确认：`59b93929_top10_drop1_10d` 与 `raw10d_c98856b4_top20_drop2_20d` 都返回 `csi1000_stage_b_forward_v1` 和 `quant/data/round1_stage_ab/csi1000_stage_b/...`。
- `apply_csi1000_model_profile()` 在 `:220-227` 只校验 model object hash，然后单独写 label；TopK/DropN、label horizon、训练相位及 current-window decision 并未进入一个统一 contract hash。

**影响**

信号/网站 metadata 可以声称一个并未批准这些 market/profile 组合的来源；审计者也无法从 manifest 找到真正的 G2/current-window 选择证据。

**整改与验收**

- profile lineage 按市场/profile 独立定义；旧 Stage-B 字段只属于真实 Stage-B profile。
- current-window profile 指向新的 release manifest（RR03），并用一个 full strategy contract hash 绑定模型、label、universe、TopK/DropN、训练/retrain 规则及执行合同。
- 测试要求每个 canonical profile 的 decision artifact 存在、hash 匹配、market 一致；交叉指向 CSI1000 artifact 必须失败。

### RR05 — 研究结果缺完整证据闭包与 overlay 迁移复核（P1 / EVIDENCE_GAP）

**证据**

- `run_g1_baseline.py:131-156` 的 trial identity 绑定 signal 与四个 execution 文件，但不绑定 provider fingerprint、calendar/instrument/quote hashes、Qlib/runtime identity或策略 module hash。
- `g2_csi300_summary.json`、`g2_csi500_summary.json` 没有 signal/provider/source/report SHA；`g2_csi1000_summary.json` 只有 signal SHA；`g2_1m_summary.json:1-7` 只有自然语言 source。
- 数据 lineage 文档已明确：研究默认 chenditc，生产使用 overlay；两者不能互称，复现生产必须显式物化 overlay 并记录 receipt。当前仓库没有一份把 current profile、冻结信号、overlay archive、production replay report 和 selection decision 全部串起来的认证 bundle。
- 2026-11-06 prelive revalidation 已补强冻结信号、provider 与 replay source identity，是重要正面证据；它仍是历史参考模拟，不认证竞价流动性、券商成交，也没有重新证明训练 spine 的源码/provider 来源。

**整改**

- 统一 research result schema：`data_source`、provider/release/receipt、calendar、source、environment、signal、report、ledger、gate 与 parent receipt 全部内容寻址。
- 结果汇总只能由已验证 receipt 生成，禁止手工复制数字后丢掉 lineage。
- 在不触碰生产输入的隔离输出中，用一个冻结 overlay archive 对当前三 profile 做 bounded replay；与 chenditc 结果逐日对比并解释差异。它是迁移验证，不是新一次选型。

**验收**

- 修改任一 provider quote、signal、源码、report 或 summary 数字都使 bundle 验证失败。
- 同一 bundle 可从空目录重建汇总；产物不得依赖个人 `/tmp/opencode/...` 路径。
- overlay/chenditc 指标并列时明确 lineage，不拼接账户，不把 paper 模拟说成成交认证。

### RR06 — `fit_start` 参数没有控制训练段（P2 / CODE_CONFIRMED）

`spine_and_replay_modal.py:197-205` 把 job 的 `fit_start` 写入 handler，却把 `segments.train[0]` 硬编码为 `2016-01-01`。非默认 `--fit-start` 会形成 processor fit 窗口与 model train 窗口不一致，CLI 输出却声称参数已生效。

**整改/验收**：用同一个已校验的 `fit_start` 设置 handler 与 train segment；要求日期在 calendar、早于 train_end 且训练样本充分。测试传入非默认值并检查最终 cfg，而不是只检查 job 字段。

### RR07 — Gate-0 接受 NaN 并返回 PASS（P2 / REPRODUCED）

`gate0_diagnostics.py:61-99` 只检查 Python 数值类型和 `<= 0`，没有 `math.isfinite()`。本地最小反例把 `portfolio_value_close=NaN`，函数返回：

```json
{"status":"PASS","end_nav":NaN,"return":NaN,"max_drawdown":0.0}
```

这会把损坏 artifact 伪装为通过，并且 `json.dumps()` 默认允许非标准 NaN。`select_per_market.py` 的候选输入也缺统一 finite schema，排序/输出可被 NaN 污染。

**整改/验收**：共享 finite-number validator；拒绝 bool、NaN、Inf、非正 NAV；强制 `allow_nan=False`；测试 NaN/±Inf/断裂日期/相邻 opening 与 prior close 不一致均失败。

### RR08 — 恢复分类静默丢尾部任务（P2 / REPRODUCED）

`spine_jobs.py:92-100` 用 `zip(jobs, results)`。本地输入 3 个 jobs、1 个 PASS result，返回 `[]` 而不是剩余 2 个失败任务；coordinator 在 `spine_and_replay_modal.py:508-529` 可能打印全部完成。

Modal 正常 map 应返回一一对应结果，因此这不是当前常态路径；但恢复代码既然显式处理异常和重试，就应对 partial result fail closed。

**整改/验收**：长度不等立即抛错，或把未配对 jobs 全部标失败；测试 0、短、长 results 以及 map 中断。

### RR09 — source identity 未绑定 Qlib/依赖环境（P2 / EVIDENCE_GAP）

`research_identity.py:197-221` 的 replay manifest 只哈希 `quant/code/**/*.py`，training 额外加 production YAML；不包括当前 checkout 的 `qlib/` 源码、Python/NumPy/Pandas/LightGBM/PyArrow 版本或环境锁。prelive Modal 会另写 environment JSON，但 cache/receipt matching 没有把它纳入 engine identity；本地 fallback 更没有等价环境 receipt。

这不表示现有结果一定错，但同一 `engine_sha256` 可以对应不同数据读取、Parquet 或模型运行环境。

**整改/验收**：在不审查上游 Qlib 逻辑的前提下，将实际安装的 Qlib source/package digest 与关键依赖版本纳入 environment manifest；launcher/worker 双端校验，结果 receipt 强绑定。环境任一关键项变化必须 cache miss 或进入明确的兼容性审核。

## 4. 已有保护与本轮不应回退的修复

以下检查抽样确认仍有效，整改时不得为了兼容旧 artifact 而放宽：

- `portfolio_performance()` 使用成本后 account/NAV，拒绝非有限值，并校验 `account == return-cost`。
- `qlib_audit_fixes.py` 的 label maturity/purge 使用交易日历而非自然日近似。
- `prelive_revalidation.py` 对冻结 signal overlap、完整报价文件、provider provenance、账本守恒、100 股/板块数量规则做 fail-closed 校验。
- Stage/L1-L4 的新 runner 使用 clean-worktree、provider fingerprint、source snapshot 和不可变 receipt；RR01 只针对 regime reselection 的旧 runner，不能把已加固 runner 降级为同样的弱 cache。
- 网站/生产与研究数据 lineage 分离是正确边界；整改目标是显式迁移复核，不是把两个 provider 混成一个账户。

## 5. 建议实施顺序

1. **先阻止新晋升**：新增 release status/manifest，标清当前 override；不回滚、不部署。
2. **先写失败测试再修 RR02、RR01**：净收益与 cache identity 是最高风险的可执行缺陷。
3. 修 RR04/RR05：建立 profile lineage 和研究 bundle，迁移现有 artifact，不伪造历史证据。
4. 修 RR06–RR09，并把共同 finite/environment/partial-map guard 下沉为共享工具。
5. 本地全量 `pytest -q quant/tests` 后，只做一次集中、attached、隔离输出的 overlay bounded replay；未另行授权不得运行 Modal，更不得部署生产 App。

## 6. 本地验证与未验证边界

### 已完成

- 相关回归：`85 passed`：spine jobs、Gate-0、Gate-v2、research identity、extension calculation。
- `pytest -q quant/tests`：`1165 passed, 2 skipped, 24 warnings, 50 subtests passed`（41.25s）。现有测试全绿，但没有覆盖本报告的反例，不能据此关闭 RR01–RR09。
- RR01 两个本地 Modal `.local()` 最小反例均复现。
- RR02 对已提交 13 个结果独立复算；净收益高估范围 0.1958–0.2284pp，排序未变。
- RR07、RR08 stdlib 最小反例复现。
- 审查期间未运行 `modal run`、未部署、未写 Volume、未修改生产数据。

### 尚未完成

- 本文没有修复 RR01–RR09，也没有重算/替换历史 artifact。
- 未对私有 chenditc/overlay provider 做新的逐字节比较。
- 未做云端训练、三市场 overlay replay、券商成交或竞价流动性认证。
- 文档改动尚未提交、推送、部署或生产验收。

## 7. DeepSeek 交付要求

DeepSeek 应逐项提交“失败测试 → 实现 → 本地通过证据”，不得直接改历史 JSON 数字来绕过重算。交付文档至少包含：

- RR01–RR09 状态矩阵与对应测试名；
- legacy artifact 的 retained/withdrawn/recomputed 清单；
- 当前 CSI300/CSI500 override 的 release manifest 与准确 lineage；
- 完整本地测试命令/结果；
- 明确 `NOT DEPLOYED / NOT CLOUD-REPLAYED / NOT BROKER-CERTIFIED` 的剩余边界。
