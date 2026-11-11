# 研究回测与策略择优整改实施交接

> 日期：2026-11-11
> 状态：`LOCALLY IMPLEMENTED / INDEPENDENT REVIEW FAILED / REMEDIATION REQUIRED / NOT COMMITTED / NOT DEPLOYED`
>
> 实施报告：[`2026-11-11-research-backtest-strategy-selection-implementation-report.md`](2026-11-11-research-backtest-strategy-selection-implementation-report.md)
> 独立验收整改：[`2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md`](2026-11-11-research-backtest-strategy-selection-independent-review-remediation.md)
> 问题来源：[`2026-11-11-research-backtest-strategy-selection-review.md`](2026-11-11-research-backtest-strategy-selection-review.md)
> 实施者：DeepSeek
> 独立验收：Codex
> 性质：代码与证据链整改交接，不是策略晋升、Modal 执行或生产部署批准。

## 1. 目标与验收结论边界

本轮只关闭审查确认的 RR01–RR09：

- 修正 regime spine 缓存身份、current-window 净收益、研究发布状态和 profile lineage；
- 建立 provider/source/environment/signal/report/summary 的可验证证据闭包；
- 修正 `fit_start`、非有限数和 partial-map 恢复的 fail-closed 行为；
- 先提交能复现旧缺陷的失败测试，再修改实现；
- DeepSeek 完成本地实现和实施报告后，由 Codex 独立阅读 diff、复现反例并运行测试。

本轮不根据短窗口结果重新选模型，不改变 CSI300、CSI500、CSI1000 的当前模型、TopK/DropN、账户本金、费用、重训周期或 T-close→T+1-open 合同。审查没有要求立即回滚当前生产；在整改验收前，暂停**新的**策略晋升即可。

## 2. 接手前必须保护的工作区

本文起草时：

- Git HEAD：`4f4163bb1`；分支 `main` 领先 `origin/main` 3 个提交。
- HEAD 已依次包含 `95b55855e`（PC01–PC08）、`001cada22`（PR01–PR06）和 `4f4163bb1`（CR01–CR05）本地整改；这些修复及更早 R01–R10、S01–S05、复权边界、Provider 2/8、Website incremental 4/12 候选均须保留，不得为研究整改回退。
- 本轮审查已有未提交文档改动：

```text
M  quant/docs/README.md
?? quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-review.md
?? quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-remediation-handoff.md
```

DeepSeek 开始前必须执行并保存 `git status --short --branch`、`git diff --check` 和 `git log -3 --oneline` 的结果，先理解并保护上述文档以及随后出现的任何用户改动。禁止 `reset --hard`、`restore`、`clean`、覆盖式 checkout，禁止只在纯 HEAD 上重做。

实现和验收只允许修改 `quant/`。不得把自建实现移入上游 `qlib/`，也不得顺手格式化或审查上游代码。验收前不要提交、推送或部署，除非用户另行明确授权。

## 3. 不变合同与禁止事项

必须保留：

- 唯一自建 overlay provider；研究默认 chenditc 和生产 overlay 保持独立 lineage，不拼接行情、信号或账户。
- `qlib-prod-daily`、`qlib-prod-website` 两个生产 App，不合并容器。
- CSI300 Top10/Drop1、CSI500 Top20/Drop2、CSI1000 Top10/Drop1，三个独立 1M 账户。
- 20-session 重训、标签成熟度和 purge、PIT universe、板块/日期涨跌停、整手、5bp/15bp 及最低 5 元费用。
- `portfolio_performance()`、prelive provenance/ledger、Stage/L1-L4 clean-worktree/provider/source gate 的现有严格校验。
- 生产输入只读、输出隔离、内容寻址 artifact、不可变 receipt 和失败关闭。

禁止：

- 不得把当前 CSI300/CSI500 伪造为研究 gate PASS；其准确状态是 `RISK_ACCEPTED_OVERRIDE`。
- 不得把 CSI1000 incumbent 伪造为本轮新晋级；其准确状态是 `INCUMBENT_RETAINED`。
- 不得修改旧 JSON 中的数字或补字段来冒充一次重新计算；旧 artifact 必须保留原字节与 SHA。
- 不得因兼容旧缓存而省略 identity 字段、接受 NaN、吞掉任务或放宽 gate。
- 不得运行 `modal run`、不得使用 `--detach`、不得部署 App、不得写生产 Volume、不得发布 GitHub/网站结果。
- 不得把 paper simulation 描述为券商成交认证，也不得把 overlay replay 描述为重新证明训练来源。

## 4. 整改矩阵和依赖顺序

| ID | 级别 | 实施目标 | 依赖 | 交付状态 |
|---|---|---|---|---|
| RR01 | P1 | spine job/slice/assembly 使用完整内容身份，旧缓存不能误命中 | RR09 | 待实现 |
| RR02 | P1 | current-window 和 bear-block 全部使用成本后净收益 | 无 | 待实现 |
| RR03 | P1 | 研究 gate、人工 override 与生产 release 状态可机器验证 | 与 RR04 联动；PASS 依赖 RR05 | 待实现 |
| RR04 | P1 | 每个 market/profile 使用准确 lineage 和完整策略合同 | 与 RR03 联动 | 待实现 |
| RR05 | P1 | 研究结果形成可验证 evidence bundle；legacy 缺口不伪造 | RR09 | 待实现 |
| RR06 | P2 | `fit_start` 同时控制 handler fit 和 model train 起点 | 无 | 待实现 |
| RR07 | P2 | Gate-0/候选/JSON 全链拒绝 NaN、Inf、bool 和非法 NAV | 无 | 待实现 |
| RR08 | P2 | jobs/results 长度不等必须失败，不能静默丢尾部 | 无 | 待实现 |
| RR09 | P2 | source identity 绑定实际 Qlib 源码和关键依赖环境 | 无 | 待实现 |

建议顺序：

1. 先为 RR01、RR02、RR06–RR09 写最小失败测试并保存旧实现失败证据。
2. 先实现 RR09 的 environment/source identity，再由 RR01、RR05 使用同一个版本化结构。
3. 修复 RR02、RR06–RR08，形成完全本地可验收的计算/健壮性闭环。
4. 实现 RR03/RR04 的 release manifest 与生产配置校验，再实现 RR05 bundle。
5. 运行全部本地测试、更新实施报告；停止并交给 Codex，不自行做云端 replay 或部署。

## 5. RR01：spine 缓存必须绑定实际实验身份

### 5.1 目标代码

- `quant/code/research/regime_reselection/spine_jobs.py`
- `quant/code/research/regime_reselection/spine_and_replay_modal.py`
- `quant/code/execution/research_identity.py`
- `quant/tests/test_regime_reselection_spine_jobs.py`
- 建议新增 `quant/tests/test_regime_reselection_cache_identity.py`

身份、receipt 和验证逻辑应尽量放在 Modal-free 模块，测试不得依赖远程容器。

### 5.2 实现要求

1. 定义版本化 canonical `job_identity` 和 `job_sha256`，至少包含：
   - source/environment manifest SHA；
   - provider 类型、immutable receipt/fingerprint、calendar SHA；
   - model YAML SHA、market、profile 和完整 strategy contract SHA；
   - label horizon、data start、fit start、retrain as-of、signal end；
   - run purpose、spine、seed、LightGBM threads 和影响确定性的运行参数。
2. coordinator 创建 identity，worker 从实际加载的源码、环境、provider 和配置重算并要求完全相等。只信任调用方传入 SHA 不算关闭。
3. 新 slice 路径包含 `job_sha256`；receipt 保存完整 identity、prediction SHA 和 receipt 自身 SHA。复用要求 schema、identity、文件摘要全部匹配。
4. `completed_jobs()` 对 legacy/缺字段/不同 identity 一律 cache miss，不删除旧数据，也不原地补签。
5. assembly receipt 绑定请求的 `start/end`、calendar SHA、期望交易日列表 SHA、按日期排序的 slice receipt SHA 列表和最终 signal SHA。
6. assembly 必须拒绝缺失、重复、额外、跨 identity、跨 provider、跨窗口或日历改写的 slice。不得因最终 parquet 存在就直接返回。
7. 新 schema 使用新 run-id 或 identity 目录；既有混合 SHA 的 G1 manifest 只保留为 `LEGACY_UNCERTIFIED` 证据。

### 5.3 必须先失败、修后通过的测试

- 仅修改 profile、horizon、seed、fit/data start、source SHA、YAML、provider、calendar 或 runtime 中任一字段：cache miss。
- prediction 文件 SHA 相同但 job identity 不同：cache miss。
- 2021 receipt 请求 2024 window：失败，不返回旧 receipt。
- 缺一天、多一天、重复一天、乱序 receipt、receipt 内容被改写：失败。
- 完全相同 identity 的中断恢复：0 次新 fit，assembly 字节和 receipt SHA 与首次一致。
- legacy receipt 仍可审计读取，但不能授权新 fit/assembly 命中。

## 6. RR02：统一净收益与历史 artifact 状态

### 6.1 目标代码和数据

- `quant/code/research/current_window/current_window_modal.py`
- `quant/code/execution/portfolio_performance.py`（优先复用，非必要不改变合同）
- 建议新增 `quant/tests/test_current_window_net_performance.py`
- `quant/data/current_window_backtest/` 中现有结果只做 inventory/状态标注，不覆盖原件。
- `quant/docs/08-current-window-backtest/` 中受影响的口径说明。

### 6.2 实现要求

1. `current_window_backtest()` 使用 canonical `portfolio_performance()` 或等价共享 helper；净收益必须来自验证后的 account/NAV 或 `return-cost`，不能再由 `report["return"]` 得出。
2. 输出升级为显式 schema，至少区分：
   - `daily_gross_return`、`daily_cost`、`daily_net_return`；
   - `strategy_total_return_net`、benchmark return、total cost、final account；
   - `account_return_max_error` 和 metric schema version。
3. 若保留兼容字段 `cumulative_return`，其值必须明确等于净收益，并通过 schema version 与旧 artifact 区分。
4. `bear_block()` 每块输出 gross/cost/net/account 校验证据；driver 只拼接 verified net return。日期重复、缺口或 block 边界不一致必须失败，不能静默去重。
5. 13 个短窗口历史单元：保留旧文件，以 final account 独立生成 correction inventory，记录旧值、净值、差异、原文件 SHA 和“排序未变”。不得直接编辑旧数字。
6. 旧 bear artifact 因缺完整 cost/report，标为 `GROSS_ONLY_WITHDRAWN`；只有取回冻结原始 report 或授权重算后才能生成新的净收益结果。
7. 文档明确后续 G1/G2 continuous-account 使用 canonical 净收益，本缺陷不应被扩大成当前生产账户错误。

### 6.3 必须测试

- 构造非零 cost：gross、net、account 三者关系精确，旧算法会失败。
- account 与 `return-cost` 不一致、NaN、负 cost、缺 session：fail-closed。
- anchor bar 只按合同处理一次，不漏扣第一笔费用，不多算一天。
- 两个 bear blocks 拼接后净值与逐日 net 复算一致；重复/断裂日期失败。
- 13 单元 correction inventory 可重复生成，原 artifact SHA 不变，排序变化结论由代码计算而非手写。

## 7. RR03/RR04：研究 release、override 与准确 lineage

### 7.1 建议落点

- 新增 Modal-free release validator，例如 `quant/code/execution/strategy_release.py`。
- 新增版本化 release 数据目录，例如 `quant/data/strategy_releases/`。
- 修改 `quant/code/production/csi1000_production_config.py` 的 canonical market/profile 解析。
- 在信号/网站 metadata 入口传播 release id/status/contract SHA；不得改变账户计算。
- 新增 `quant/tests/test_strategy_release_manifest.py`，扩展 `test_csi1000_production_config.py` 和 lineage tests。

### 7.2 release manifest 合同

每个 release 至少绑定：

- schema/release id、market、profile、candidate id；
- model config SHA、label 表达式/horizon、PIT universe；
- TopK/DropN、20-session retrain、T-close→T+1-open；
- board/date limit、100 股、fees/min fee、initial cash/account scope；
- research contract、decision artifact、signal/report/bundle SHA；
- release status 和 status-specific evidence；
- manifest 自身内容 SHA，JSON 使用 `allow_nan=False`。

允许的状态至少为：

- `GATE_PASS`：必须通过真实 promotion gate 并绑定完整 evidence bundle；
- `RISK_ACCEPTED_OVERRIDE`：必须记录原因、范围、已知缺口、决策文档及其 SHA，不得伪装成 gate PASS；
- `INCUMBENT_RETAINED`：现有 incumbent 延续，不表示本轮新晋级。

当前迁移必须如实记录：

- CSI300 `59b93929_top10_drop1_10d`：`RISK_ACCEPTED_OVERRIDE`；
- CSI500 `raw10d_c98856b4_top20_drop2_20d`：`RISK_ACCEPTED_OVERRIDE`；
- CSI1000 `c98856b4_top10_drop1`：`INCUMBENT_RETAINED`。

批准人字段不得写个人姓名、账户或私有 workspace；使用公开仓库中的操作者角色和已有决策文档 SHA 即可。禁止补造 shadow、forward 或流动性证据。

### 7.3 profile/lineage 修复

1. 把 model profile 和 production release lineage 分开：共享模型参数可以复用，但生产 lineage 必须按 `(market, profile)` 解析。
2. CSI1000 Stage-B commit/token/path/contract 只能附在真正由该 Stage-B 证据支持的 profile；CSI300/CSI500 必须指向各自 current-window/G2 decision 与 release manifest。
3. 完整 strategy contract SHA 同时绑定模型、label、universe、TopK/DropN、重训及执行合同；不能只校验 LGB model object hash。
4. canonical production mapping 启动时验证 release 文件、SHA、market/profile/status。缺失、错 market、未知状态、hash drift 一律失败。
5. release 状态进入 signal/website 方法 metadata。页面不得把 override 展示成“已通过研究门槛”，但本轮不要求重做页面布局。

### 7.4 必须测试

- CSI300/CSI500 不再返回 CSI1000 Stage-B path/token/contract。
- 交换两个市场 release、修改 TopK/DropN/horizon/fees/model hash：失败。
- `GATE_PASS` 缺任一 gate receipt：失败；把 override 字符串改成 PASS 也失败。
- override 缺 reason/scope/decision SHA：失败；完整 override 可加载但保持独立状态。
- 未登记 profile 不能进入 canonical production mapping；研究用非 canonical profile 不被错误授予 release。
- 现有三市场账户参数在迁移前后完全一致。

## 8. RR05：建立研究 evidence bundle，不给 legacy 结果补签

### 8.1 实现目标

新增一个 Modal-free、版本化的 bundle builder/verifier（可放在 `quant/code/execution/`），让 summary 只能从已经验证的父 receipt 生成。bundle 至少包含并哈希：

- research contract 和 release/candidate identity；
- source/environment manifest（RR09）；
- provider 类型、immutable receipt/fingerprint、calendar/instrument/quote identity；
- model YAML/profile/strategy/execution contract；
- frozen signal、逐日 report、ledger、summary；
- gate 结果、parent receipt 列表和生成器版本。

所有路径必须是仓库相对或 bundle 相对路径；禁止提交个人绝对路径。verifier 从 bundle 根目录重新读取实际文件并比对 SHA，不能只比较 JSON 中的两个字符串。

### 8.2 legacy 迁移政策

- `g1_spine_manifest_20261102.json` 保持原件，标记/inventory 为 `LEGACY_MIXED_SOURCE_UNCERTIFIED`。
- G2 summary 缺 signal/provider/source/report 的部分标记为 `LEGACY_INCOMPLETE_EVIDENCE`。
- prelive 结果可作为独立 replay 证据引用，但不能补证训练 spine 来源，不能认证券商成交。
- 只迁移仓库中真实存在且可重算 SHA 的字段；未知字段写 `MISSING` 并使 certification 为 false。
- 不因为 legacy incomplete 回滚生产；但它不能授权新的 `GATE_PASS`。

### 8.3 必须测试

- 从临时目录构造完整 bundle，验证后可从空输出目录重建 summary。
- 修改 provider quote、calendar、signal、源码、report、ledger 或 summary 任一字节：验证失败。
- 缺父 receipt、相对路径越界、绝对路径、重复 artifact role、NaN：失败。
- chenditc 与 overlay 两套结果可以并列但 lineage 不同；试图把两者拼成同一账户/bundle：失败。
- legacy inventory 不会被 verifier 误判为 certified。

DeepSeek 本轮只实现 schema、verifier、合成测试和 legacy inventory；不得自行启动三市场 overlay replay。真实 bounded replay 留待 Codex 本地验收通过且用户另行授权后集中执行一次。

## 9. RR06：`fit_start` 必须真正控制训练段

在 `spine_and_replay_modal.py` 中抽取 Modal-free cfg 构造/修改 helper，使同一个已验证 `fit_start` 同时设置：

- handler `fit_start_time`；
- `segments.train[0]`；
- job identity/receipt 中的训练起点。

日期必须存在于 provider calendar，早于/equal train end，并满足最低训练样本约束。测试传入非默认日期并检查最终 cfg 和 identity；只检查 CLI/job 字段不算通过。默认 `2016-01-01` 行为保持不变。

## 10. RR07：非有限数必须全链失败

建立或复用一个共享 finite validator，覆盖 Gate-0 NAV、候选输入、metrics 和 artifact JSON：

- 拒绝 `bool`、NaN、正负 Inf、非正 NAV、非法 return/cost；
- 日期唯一、有序、覆盖连续的预期交易 sessions；
- opening/prior-close 等边界字段按原合同校验；
- 所有认证 JSON 使用 `allow_nan=False`。

扩展 `test_regime_reselection_gate0.py`：NaN/Inf/bool/零或负 NAV、断裂/重复日期均失败；有限合法样本保持 PASS。再增加一次序列化测试，证明 Python 默认 `json.dumps(NaN)` 的旧行为不再可达。

## 11. RR08：partial map 不能被当作完成

`failed_jobs(jobs, results)` 在分类前先要求长度严格相等：

- results 短于 jobs：明确抛出恢复错误，或把所有未配对 jobs 标失败；推荐抛出包含 counts 的异常，由 coordinator 记录失败并停止 assembly。
- results 长于 jobs：失败，不忽略多余结果。
- 对一一对应结果，保留现有 exception/非 PASS 分类。
- coordinator 只有在完整结果数、所有 PASS 和完整 receipt 验证后才能打印完成或进入 assembly。

测试 0、短、长、map 中断、异常对象、非 PASS、全部 PASS；必须直接覆盖 coordinator 的“不能宣布全部完成”，不只单测 helper。

## 12. RR09：绑定实际 Qlib 与关键运行环境

### 12.1 environment manifest

在 `research_identity.py` 增加稳定、版本化的 environment manifest，至少包含：

- Python 实现和 major/minor/micro；
- 实际导入 Qlib 的版本、源码/package digest；editable checkout 时哈希实际 `qlib/**/*.py`，而非只写版本字符串；
- NumPy、Pandas、LightGBM、PyArrow 版本；
- 影响确定性的 seeds、thread 数或由 job identity 单独绑定。

不得把个人路径、hostname、workspace、credential 或临时目录写入 committed artifact。路径只用于本地读取，manifest 中保存规范化 component name/version/digest。

### 12.2 source/cache 绑定

- launcher 和 worker 独立生成 manifest 并要求完全相等。
- RR01 slice、RR05 bundle 和新的研究 cache key 必须绑定 environment SHA。
- 关键版本或 Qlib 源码 digest 改变时 cache miss；不得提供默认 `allow_environment_mismatch`。
- 若明确评估兼容性，必须产生新的 review receipt，而不是复用旧 artifact。

### 12.3 测试

- 临时 fake Qlib tree 改一个源码字节：environment/source SHA 改变。
- 改任一关键依赖版本：cache miss。
- 只改绝对安装路径但源码和版本相同：规范化 SHA 不变。
- 缺 package/version/digest：fail-closed。
- manifest 和 JSON 中不出现 checkout 绝对路径或环境凭证。

## 13. 本地验证命令

DeepSeek 必须先运行新增/相关测试，再运行全量；实施报告记录命令、退出码和摘要，不得只写“通过”。建议至少：

```bash
.venv/bin/python -m pytest -q \
  quant/tests/test_regime_reselection_spine_jobs.py \
  quant/tests/test_regime_reselection_cache_identity.py \
  quant/tests/test_regime_reselection_gate0.py \
  quant/tests/test_current_window_net_performance.py \
  quant/tests/test_strategy_release_manifest.py \
  quant/tests/test_research_identity.py \
  quant/tests/test_extension_calculation_fixes.py \
  quant/tests/test_csi1000_production_config.py \
  quant/tests/test_lineage_market_binding.py

.venv/bin/python -m pytest -q quant/tests
git diff --check
git status --short --branch
```

若实施者选择扩展现有测试而不新增建议文件，必须在实施报告提供实际文件名和等价覆盖映射。不得为了让测试通过删除既有断言、降低精度、跳过测试或把异常改成 warning。

## 14. DeepSeek 必须提交的实施报告

新增：

`quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-implementation-report.md`

至少包含：

1. RR01–RR09 状态矩阵：`OPEN / LOCALLY_FIXED / BLOCKED_EXTERNAL`。
2. 每项“旧实现失败测试 → 新实现通过”的测试名和结果。
3. 实际修改文件、schema version、迁移/兼容策略。
4. legacy artifact 清单：`RETAINED`、`WITHDRAWN`、`CORRECTED_DERIVATIVE`、`LEGACY_UNCERTIFIED`，以及原 SHA。
5. 三个 canonical release 的 id/status/manifest SHA；不得把 override 写成 PASS。
6. targeted 和 `quant/tests` 全量结果、`git diff --check`、工作区状态。
7. 明确的外部边界：`NOT CLOUD-REPLAYED / NOT DEPLOYED / NOT BROKER-CERTIFIED`。
8. 若存在未完成项，保持 `OPEN` 并说明原因；不得用现有绿测替代新增反例。

同时更新本 handoff 和总索引的状态链接，但保留原审查报告作为发现时证据。不要删除或改写 RR01–RR09 的原始审查结论。

## 15. Codex 独立验收计划

DeepSeek 完成后，Codex 将：

1. 先核对 Git 范围，确认没有上游 Qlib、生产资源、策略参数或账户合同漂移。
2. 逐项阅读实现，不仅依赖实施报告或绿测。
3. 独立重放 RR01、RR02、RR07、RR08 的最小反例。
4. 篡改 release/bundle/provider/source/environment 字段，确认 RR03–RR05/RR09 fail-closed。
5. 检查三个 canonical profile 的 market、lineage、状态和完整合同。
6. 运行 targeted 与全量 `quant/tests`，核对 historical artifact 原 SHA。
7. 给出 `LOCAL PASS`、`REMEDIATION REQUIRED` 或 `BLOCKED_EXTERNAL`；本地 PASS 仍不等于部署或生产验收。

只有本地验收通过且用户另行授权，才考虑一次集中、前台 attached、隔离输出的 frozen overlay bounded replay。即便届时执行，也严禁 `modal run --detach`，且不得写生产输入或把 paper replay 升格为成交认证。

## 16. 本地验收矩阵

不能以测试总数代替下列语义证据：

| 类别 | 必须证明 | 对应项 |
|---|---|---|
| 缓存身份 | 任一任务、源码、环境、provider、日历、YAML 或窗口字段变化均 cache miss；同身份恢复不重复 fit | RR01、RR09 |
| assembly | 期望日期和有序 slice receipt 集合完全匹配；缺失、额外、重复、跨窗口和篡改均失败 | RR01 |
| 收益口径 | 非零成本下净收益等于账户路径和 `return-cost`；gross/net/cost 不混名 | RR02 |
| 历史状态 | 原 artifact 字节/SHA 不变；correction derivative 可重建；无 report 的 bear 结果保持 withdrawn | RR02、RR05 |
| 发布治理 | CSI300/500 为 override、CSI1000 为 incumbent；任何字段篡改或伪造 PASS 均失败 | RR03、RR04 |
| lineage | 每个 market/profile 指向自己的 decision/release；不再交叉引用 CSI1000 Stage-B | RR04 |
| evidence bundle | 从实际文件验证 provider/source/environment/signal/report/ledger/summary；任一字节变化即失败 | RR05、RR09 |
| 配置生效 | 非默认 `fit_start` 同时进入 handler、train segment、identity 和 receipt | RR06 |
| 数值安全 | NaN、Inf、bool、非正 NAV、断裂/重复日期和非标准 JSON 全部 fail-closed | RR07 |
| 恢复完整性 | jobs/results 短、长或中断不能宣布完成，也不能进入 assembly | RR08 |
| 合同无漂移 | 三市场 profile、TopK/DropN、费用、本金、重训、执行时序及生产资源配置不变 | 全部 |
| 状态边界 | 本地实现、Codex 验收、提交、云端 replay、部署和生产验收分别记录 | 全部 |

若修改 signal/website metadata 或网站方法说明，还必须运行受影响的 `.cjs` 前端测试，确认新增 release 状态不会被展示成收益保证或 gate PASS。若没有修改前端，应在实施报告明确说明未触及前端及原因。

## 17. 实施纪律与停止条件

1. 每个 finding 先建立能在旧代码上失败的正式测试；失败证据记录测试名和关键异常/错误值，不提交临时私人路径或大体积日志。
2. 优先抽取 Modal-free pure helper；测试不得通过 monkeypatch 掉被审查的核心校验，也不得把 production Volume 当 fixture。
3. 每完成一组依赖项就运行对应 targeted tests；全部实现后一次运行完整 `quant/tests`，避免只在最后堆叠无法定位的失败。
4. 对受改 Python 文件执行 Black 120 列检查；遵守当前 AGENTS 的 lint 入口。不得顺手格式化无关文件或 upstream Qlib。
5. 发现仓库没有原始 report、provider receipt 或批准证据时，按 `LEGACY_INCOMPLETE_EVIDENCE`/`BLOCKED_EXTERNAL` 记录并停止补证；不得生成貌似真实的 SHA、PASS receipt 或收益序列。
6. 发现修复会改变 current profile、排名、账户合同或生产结果时立即停止，在实施报告写明影响并等待用户决策；不得以“顺便优化”扩大范围。
7. 发现工作区出现非本任务的并行改动时，保护现有内容并停止冲突文件修改；禁止 reset/restore/clean。
8. 本地测试需要真实私有 provider、付费 Modal、生产 secret 或外部写权限时，先用合成 fixture 验证可验证部分，把外部门保持 OPEN；不得自行扩大授权。
9. DeepSeek 最终只交付 intended diff、失败→通过证据、完整测试摘要和实施报告，不提交、不 push、不部署，等待 Codex 验收。

## 18. 后续云端复核（不属于 DeepSeek 本地授权）

只有 Codex 本地验收通过且用户另行授权，才安排一次集中验证：

1. 冻结 intended commit、source/environment manifest、三市场 release 和一个 overlay archive receipt。
2. 生产输入只读，输出写独立 probe 位置，无发布 secret，不更新 canonical signal、网站、journal 或账户。
3. 前台 attached 执行并保存真实退出码、App/call id、输入/源码 SHA、日志和费用；禁止 `modal run --detach`、nohup 或 shell 后台。
4. 只做 bounded migration replay：逐日对比 chenditc 与 overlay 的 score、ranking、交易、费用、持仓、cash、NAV，并解释差异；不重新选型。
5. 云端通过只能关闭 `CLOUD_REPLAYED`，不能关闭 `BROKER_CERTIFIED`，也不能自动授权生产部署。

本节是未来验收边界，不是 DeepSeek 的执行任务。

## 19. 可直接发给 DeepSeek 的简短 prompt

```text
请先阅读当前 AGENTS.md，以及：
quant/docs/00-strategy-handbook/STRATEGY_ATLAS.md
quant/docs/05-round2-layered-search/60-csi1000-two-round-strategy-research-final-report-20261015.md
quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-review.md
quant/docs/07-code-review/2026-11-11-research-backtest-strategy-selection-remediation-handoff.md
quant/docs/07-code-review/2026-11-11-postclose-implementation-report.md
quant/docs/09-data-source/02-production-vs-research-data-lineage.md

按 remediation handoff 实施 RR01–RR09。开始前实时核对 Git HEAD/工作区并保护现有未提交文档和用户修改；
禁止 reset/restore/clean，不要只在纯 HEAD 上开发。只修改 quant/ 扩展，先把每个复现场景写成旧实现会失败的
正式回归测试，再修实现并记录失败→通过证据。

重点关闭：spine 完整 cache/assembly identity；current-window 与 bear 的成本后净收益；可验证的
GATE_PASS/RISK_ACCEPTED_OVERRIDE/INCUMBENT_RETAINED release；按市场准确 lineage；完整 research evidence
bundle；fit_start 真正进入训练段；NaN/Inf fail-closed；partial map 不得假成功；Qlib/关键依赖环境身份。
保留旧 artifact 原字节，缺证据只能标 legacy/blocked，不能补签或伪造 PASS。

保持当前三市场 profile、TopK/DropN、三个 1M 账户、费用、20-session 重训、T-close→T+1-open、唯一
overlay 生产源和两个生产 App，并保留 PC/PR/CR、R01–R10、S01–S05 及复权修复；不重选策略、
不放宽 gate、不恢复 chenditc 主备、不改生产资源配置。
本轮仅做本地代码、测试和实施报告，不提交、不 push、不运行 Modal、不部署、不写生产 Volume。
完成后更新 RR01–RR09 状态、legacy artifact 清单、全部测试结果和未验证边界，交给 Codex 独立验收。
```
