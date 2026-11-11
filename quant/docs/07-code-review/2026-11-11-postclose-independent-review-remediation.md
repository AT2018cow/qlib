# 收盘后统一生产独立验收整改清单

> 日期：2026-11-11
>
> 验收基线：`95b55855e3f337366bbd370cbe0e084494629753`
>
> 基线状态：`INDEPENDENT REVIEW FAILED / LOCAL ONLY / NOT DEPLOYED / NOT PUSHED`
>
> 实施者：DeepSeek
>
> 独立复验：Codex
>
> 性质：针对 PC01–PC08 本地实现的整改交接；不是部署批准、生产 PASS、收益认证或自然调度验收。

## 1. 验收结论

提交 `95b55855e` 当前不能部署或推送。独立审查确认 4 个阻断问题和 2 个验收缺口：

| ID | 级别 | 结论 | 当前状态 |
|---|---|---|---|
| PR01 | P0 | 未缓存的网站 full/incremental replay 在进入业务回放前即 `TypeError` | 已复现，待修复 |
| PR02 | P1 | release/semantic identity 漏哈希实际生产训练 YAML | 已复现，待修复 |
| PR03 | P1 | 不完整或被篡改的网站 publication receipt 可被当成已发布 | 已复现，待修复 |
| PR04 | P1 | 23:15 recovery 仅有 10 分钟，无法等待数小时的同步 `.remote()` 恢复 | 已确认，待修复 |
| PR05 | P2 | 生产自然生成的 retention dry-run 没有绑定 anchor/reachable 引用 | 已确认，待修复 |
| PR06 | P2 | 调度测试依赖实时 Sina；实施报告的前端命令只执行一个脚本 | 已复现，待修复 |

在 PR01–PR06 关闭前，状态只能写：

```text
LOCAL IMPLEMENTED / INDEPENDENT REVIEW FAILED / NOT DEPLOYED
```

## 2. 必须保护的现状与边界

验收结束时：

- 本地 HEAD：`95b55855e`；`origin/main`：`d90ff2bcb`；本地 `main` 领先 1 个提交。
- 工作区干净；Codex 本轮只审查，没有修改实现、部署、push 或写生产数据。
- `95b55855e` 已包含此前 Provider 2 CPU / 8 GiB、Website incremental 4 CPU / 12 GiB 的资源候选，以及 PC01–PC08 实现。
- 生产仍为 v20：Provider 4/8、统一 Website 8/16、06:00/07:30/09:00 调度。

DeepSeek 接手前先运行：

```bash
git status --short --branch
git log -3 --oneline
git diff --check
```

禁止 `reset --hard`、`restore`、`clean`、覆盖式 checkout，禁止拆掉 `9b5aa359c` 或 `95b55855e` 后在旧纯 HEAD
重做。只修改 `quant/` 扩展层及本整改要求涉及的索引/状态文档；根目录 `AGENTS.md` 如无新事实不要再改。

必须继续保留：

- R01–R10、S01–S05、第三轮复权边界和旧 anchor 续接修复；
- 唯一自建 overlay provider、两个生产 App、三市场和三个独立 1M 账户；
- T-close 评分、T+1-open 执行、TopK/DropN、费用、板块/日期限制、20-session 重训和 PIT 合同；
- 网站 `/data` 只读、取消 USD 阻断但保留 fresh-fit/timeout/数据/三市场原子 gate；
- 单容器 Sina 8 workers；不改成多容器抓取；
- retention 仍只生成 dry-run 计划，不实现或执行删除；
- 未获单独授权不得部署、运行付费 Modal、写生产 Volume 或发布 GitHub 信号。

## 3. PR01（P0）：真实 replay core 调用参数错误

### 3.1 证据

`quant/code/production/website_replay_core.py` 中：

- `_incremental_prior()` 的第一个参数是 `cache_sha`；`semantic_sha`、`account_sha` 是必需的 keyword-only 参数。
- `_build_market_impl()` 的未缓存 `mode="full"` 路径却调用：

```python
_incremental_prior(source_sha, asset, market, end, calendar, simulation_root=simulation_root)
```

直接按该调用复现：

```text
TypeError: _incremental_prior() missing 2 required keyword-only arguments:
'semantic_sha' and 'account_sha'
```

现有 `test_website_simulation.py` 主要直接测试 `_incremental_prior()`，或把 `_build_market_impl` 整体 monkeypatch 掉；因此
`1135 passed` 没有执行到这条真实内部调用。首轮 smoke 可以完成，但三市场 full 或 4/12 incremental worker 在新日期会失败，网站阶段和最终 marker 都不能完成。

### 3.2 修复要求

1. 调用必须使用 `cache_sha`，并显式传入已验证的 `semantic_sha`、`account_sha`。
2. 不得改回 release SHA 作为 cache 根；release-only 变化必须继续复用同语义直接祖先。
3. 不得为绕过异常而捕获 `TypeError` 后静默 full；4/12 worker 仍必须 fail-closed，不能执行 full。
4. 推荐提取一个很小的 replay-plan/helper，使测试能执行生产同一调用点，而不是继续用 AST 或整体替换 core。

### 3.3 必须先失败再通过的回归

- 未缓存 full：调用参数为 `cache_sha + semantic_sha + account_sha`，能进入 FULL 分支。
- 有直接祖先：能进入 INCREMENTAL 分支。
- 4/12 worker 无合法祖先：明确失败，不退化成 FULL。
- lost-response 的 immutable FULL：仍须先复核 input/series/ledger/model hashes，再允许返回。
- release-only 变化：semantic/account cache key 不变；业务或账户合同变化：旧祖先拒绝。

## 4. PR02（P1）：identity 哈希了错误的训练 YAML

### 4.1 证据

生产 `MODEL_CFG["lgb158"]` 实际读取：

```text
examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml
```

但以下身份都只加入了另一个文件：

```text
examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
```

受影响位置：

- `WEBSITE_SEMANTIC_DEPENDENCIES`
- `release_manifest()`
- `source_manifest("training")`

独立探针结果：

```text
actual_yaml_in_release= False
actual_yaml_in_semantic= False
hashed_yamls= ['examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml']
```

因此修改真实生产模型 YAML 不会改变 release 或 website semantic identity，旧模型/result cache 可能在业务语义已经变化后继续被接受。

### 4.2 修复要求

1. 从 `qlib_runtime.MODEL_CFG` 的真实生产配置建立单一、可审计的 repo-relative 配置依赖，不再复制错误文件名。
2. 实际使用的 `workflow_config_lightgbm_Alpha158_csi500.yaml` 必须同时进入完整 release identity 和 website semantic identity。
3. 若 `workflow_config_lightgbm_Alpha158.yaml` 仍被其他 production 路径真实读取，可同时保留；不得用它替代实际文件。
4. `release_manifest()` 当前没有被生产 `_code_source_sha()` 使用。要么删除重复抽象并修正统一的
   `source_manifest("training")`，要么让两个生产 App 一致使用同一个 release manifest；不能让测试只验证未使用函数。
5. 不要无理由改变研究 receipt 的 `SOURCE_IDENTITY_VERSION`；若确需迁移，必须单独说明兼容边界。

### 4.3 必须测试

在临时 repo fixture 中分别改动：

- 实际 `_Alpha158_csi500.yaml`：release 和 semantic SHA 都必须改变；
- Modal CPU/memory/schedule：只改变 release，不改变 semantic/account；
- website replay、费用、board/execution 或 TopK/DropN：semantic/account 至少一个改变；
- 无关文档：三类业务 identity 均不改变。

测试还必须证明 production `_code_source_sha()` 与 website `daily()` 使用的确实是被上述负控覆盖的同一实现，而不是只调用测试专用 helper。

## 5. PR03（P1）：publication receipt 与 marker 校验不完整

### 5.1 证据

`quant/code/ops/website_budget.py::already_published()` 当前只检查：

- `status`
- `source_sha256`
- `asset_digest`

一个仅含这三个字段、没有 semantic/account identity、latest date、三市场文件/series 哈希的 JSON 会被返回为已发布。网站
`daily()` 会在任何 replay 前 idempotent short-circuit；website `status()` 和
`modal_qlib_cn_a10g._postclose_marker_valid()` 也会接受它。

后者只比较 website status 返回的 release 和 provider digest，没有比较：

- `website_semantic_sha256`
- 三市场 `account_contract_sha256`
- `latest_date == session_date`
- receipt 中三市场 immutable series/result 的实际哈希

marker 虽然写了 semantic/account 字段，但 read-only checker 只是把它们原样返回，没有验证。这会让损坏或伪造的本地 receipt
把 23:00/07:00 检查错误变成 PASS。

### 5.2 修复要求

1. 建立唯一 `validate_publication_receipt()`，由 website `daily()` short-circuit、`status()` 和 postclose checker 共用。
2. 至少验证 schema/status、release、semantic、三市场 account map、provider digest、latest date、文件数量和合法 SHA256。
3. receipt 应绑定三个市场实际发布的 series/result 内容哈希；若复用 immutable result，还须重新验证 ledger/final-state/model-cache 哈希。
4. `status()` 接收或自行计算 expected semantic/account，并返回这些已验证字段和 `latest_date`；不能只回显调用参数。
5. `_postclose_marker_valid()` 必须比较 website receipt、当前代码、marker 和 T/U 身份，任一缺失或不一致即失败。
6. 不能因为 read-only checker 无 GitHub token 就放宽本地 receipt/hash 校验。GitHub 在线复核可继续只在带 secret 的主协调器执行。

### 5.3 必须测试

以下 receipt 均必须被 `daily()`、`status()` 和 marker gate 拒绝：

- 只有 status/source/asset 的最小伪造 receipt；
- semantic SHA 被改；
- 任一市场 account SHA 被改或缺少；
- latest date 不是 T；
- series/result/ledger/final-state 任一哈希损坏；
- 非三市场完整集合；
- release 相同但 provider digest 不同。

合法 lost-response receipt 仍应恢复且不重复 push。

## 6. PR04（P1）：23:15 同步恢复入口 timeout 不足

### 6.1 证据

`daily_evening_retry` 装饰器使用：

```python
timeout=10 * 60
```

函数最后同步 `return daily_cron.remote(...)`。主协调器可能先等待仍在运行的 18:00 调用，再执行一次最长数小时的恢复；交接预计重训/首轮
full 约 4–4.5 小时，现有 `MAIN_TIMEOUT_SECONDS` 也远大于 10 分钟。即使远端子调用在父入口超时后仍可能继续，23:15 scheduled
entry 也无法返回真实恢复结果，并会形成误导性的 timeout/邮件状态。

### 6.2 修复要求

1. 保持 `.remote()` 转发同一 `daily_cron(max_containers=1)`，不得 `.local()` 绕过平台单写合同。
2. 使用现有 `BACKUP_TIMEOUT_SECONDS` 或重新推导一个覆盖“排队等待 + 一次完整恢复 + receipt 返回”的有界 timeout。
3. 不要改成 `--detach`、nohup、shell 后台或无跟踪 fire-and-forget。
4. 23:15 caller 仍无发布 secret、Volume 只读；写权限和 secret 只属于被调用的唯一协调器。

### 6.3 必须测试

- AST/decorator 测试确认 retry timeout 不小于 `MAIN_TIMEOUT_SECONDS` 加可信排队余量。
- 未完成 marker 时只调用一次 `daily_cron.remote()`，参数包含固定 T/U；返回协调器最终 receipt。
- 已完成 marker 时不调用 coordinator。
- 与主跑重叠时不出现第二写入者；`max_containers=1` 不得改变。

## 7. PR05（P2）：retention dry-run 未绑定真实 reachability

### 7.1 证据

`retention_plan()` 支持手工传入 `frozen_anchor_dates`、`referenced_dates`、`incomplete_dates`，对应 fixture 也只证明手工参数有效。
生产 `provider_build.py` 的自然调用却是：

```python
prune_archives(root, keep=30, dry_run=True)
```

没有从生产事实收集：

- 冻结 anchor；
- latest PASS 的父 lineage；
- 当前 website predecessor；
- 未完成 journal/postclose marker；
- recovery receipt 显式引用。

因此 30 日以后，非月末 anchor 或仍可达 predecessor 可能被错误列进 remove plan。当前 `dry_run=False` 已硬拒绝，所以尚未删除数据，但
PC07 不能写成完整实现。

### 7.2 修复要求

1. 增加只读 reference collector，从实际 receipt/journal/marker/website predecessor/recovery artifacts 解析 date + digest。
2. frozen anchor 应由冻结合同或 archive base lineage 解析，不要靠调用者临时记住日期字符串。
3. 生成计划时校验 date、digest、实际路径和 receipt identity；未知/损坏引用 fail-closed 并保留相关目录。
4. remove 仍只能是完整 `archives/<date>` 目录，plan 必须记录 keep/remove 原因、digest、size、生成时间。
5. 继续禁止实际 apply；本轮不得加入删除实现或执行删除。

### 7.3 必须测试

使用接近真实目录结构的固定 fixture，不允许直接把 expected dates 作为 `retention_plan()` 参数喂入：

- anchor 超过最近 30 个 PASS 仍保留；
- website 直接 predecessor 仍保留；
- FAILED/RUNNING marker 引用的 archive 保留；
- recovery receipt 引用保留；
- latest、recent、月末 checkpoint 保留；
- 不可达旧 PASS 才进入 remove；
- 损坏/未知引用不会被列为 remove；
- `dry_run=False` 仍明确失败且文件一个不少。

## 8. PR06（P2）：测试隔离和实施证据不准确

### 8.1 调度测试意外联网

新测试 monkeypatch 了 `resolve_postclose_dates()`，但生产函数在调用 resolver 前仍会先求值 `_trading_days()`；因此测试真实访问
Sina 日历。在受限网络中，交接要求的定向套件结果为：

```text
10 failed, 210 passed
```

10 项均因 `finance.sina.com.cn` DNS/连接失败。允许网络后完整套件才得到：

```text
1135 passed, 2 skipped, 21 warnings, 50 subtests passed
```

日期/调度单测必须固定输入，不能因公网可用性决定 PASS。

### 8.2 前端命令只执行一个文件

实施报告记录：

```bash
node quant/tests/test_*.cjs
```

shell 会把通配符展开成多个参数，但 Node 只执行第一个脚本，其余路径只是 `process.argv`。Codex 逐文件运行 5 个脚本后实际得到 33 项通过；
结果是绿的，但报告命令和“all frontend tests passed”证据不成立。

### 8.3 缺少固定输入 old/new 业务等价证据

原交接要求固定输入比较 score、ranking、paper trades、费用、持仓、cash、NAV。当前报告只说账户代码未改变，并把逐日 exact
comparison 留给首次部署，没有给出本地 old/new fixture 的摘要或哈希。模块拆分规模较大，不能只用“代码未改”替代业务等价证明。

### 8.4 修复要求与测试

1. 所有新增调度测试注入固定 calendar，或 monkeypatch `_trading_days()`；禁止公网请求。
2. 前端改为显式逐文件循环，任何一个失败即非零退出。
3. 增加受控 old/new fixture：除 PC04 公开 CSV 外，score、完整 ranking、planned/blocked trades、费用、持仓、cash、NAV 精确一致。
4. PC04 的允许差异仅为公开 CSV 从旧过滤/补位变为 `ranking_full[:topk]`；账户 planner/NAV 不得变化。
5. 更新实施报告为真实执行命令和结果，不回写成已部署或生产 PASS。

## 9. 独立验收中已经通过的部分

以下证据可以保留，但不能抵消 PR01–PR06：

| 检查 | 结果 |
|---|---|
| 完整 Python（允许访问 Sina） | `1135 passed, 2 skipped, 21 warnings, 50 subtests passed` |
| 5 个 `.cjs` 逐文件执行 | 33 passed |
| Sina 定向 mock | `16 passed, 1 skipped` |
| Black 单 worker | `238 files would be left unchanged` |
| `git diff --check` | PASS |
| 工作区 | clean；本地领先远端 1 个实现提交 |

静态与回归上已经符合方向、应避免回退的部分：

- 18:00/23:00/23:15/07:00 四个 schedule 名称和时区；
- website `/data` 使用只读 mount；
- 程序内 `SKIPPED_BUDGET`/USD 阻断已移除，fresh-fit gate 仍在；
- 公开 CSV 采用 raw TopK，不再按 T-close 涨跌停删榜补位；
- Sina 单容器 Session/Retry-After/jitter/token bucket mock；
- `prune_archives(dry_run=False)` 当前明确拒绝。

## 10. DeepSeek 修复后的最低验收矩阵

先逐项运行新增失败回归，再运行：

```bash
.venv/bin/pytest -q \
  quant/tests/test_daily_pipeline.py \
  quant/tests/test_cron_gate.py \
  quant/tests/test_modal_structure.py \
  quant/tests/test_research_identity.py \
  quant/tests/test_website_simulation.py \
  quant/tests/test_website_budget.py \
  quant/tests/test_csi1000_mvp_contract.py \
  quant/tests/test_provider_store.py \
  quant/tests/test_sina_fast.py \
  quant/tests/test_pipeline_resilience.py

.venv/bin/pytest -q quant/tests

for test_file in quant/tests/test_*.cjs; do
  node "$test_file" || exit 1
done

.venv/bin/black quant/code quant/tests -l 120 --check --diff --exclude qlib/_version.py
git diff --check
git status --short --branch
```

额外必须保存的负控结果：

- actual production YAML 变化会改变 release + semantic；
- resource/schedule-only 变化只改变 release；
- 最小伪造 receipt、坏 semantic/account/latest/hash 全部拒绝；
- 未缓存 full 与合法 incremental 都真正执行生产 core 调用点；
- 23:15 timeout 覆盖排队和完整恢复；
- retention 自然收集 reachability，不靠测试手工传入日期；
- 日期/调度测试在禁网环境仍通过；
- old/new fixed fixture 账户路径精确一致。

验收通过仍只代表本地 release candidate。Modal 部署、首次 identity full、下一交易日 4/12 incremental、2/8 Provider 真实新日期、邮件到达和
18:00/23:00/23:15/07:00 自然时点均需用户另行授权后集中验证。

## 11. 可直接交给 DeepSeek 的任务文本

```text
请阅读当前 AGENTS.md，以及：
quant/docs/07-code-review/2026-11-11-postclose-independent-review-remediation.md
quant/docs/07-code-review/2026-11-11-postclose-production-remediation-handoff.md
quant/docs/07-code-review/2026-11-11-postclose-implementation-report.md

以 95b55855e 为验收基线，修复 PR01–PR06。先核对并保护当前干净工作区、本地领先远端的实现提交、
Provider 2/8、Website incremental 4/12、R01–R10、S01–S05 和复权修复。禁止 reset/restore/clean，
不要在旧纯 HEAD 上重做。

先把每个复现场景写成会在 95b55855e 上失败的回归测试，再修实现。重点：
1. 修复 website_replay_core 内部 _incremental_prior 调用，使用 cache_sha 并传 semantic/account；测试必须执行真实调用点。
2. release/semantic identity 必须哈希实际生产 _Alpha158_csi500.yaml；负控要证明生产函数使用了被测试的 identity。
3. publication receipt、status 和 postclose marker 必须校验 semantic/account/latest 及三市场 immutable hash；最小伪造 receipt 必须失败。
4. 23:15 同步 .remote() recovery 的 timeout 必须覆盖排队和一次完整运行，继续只转发 daily_cron(max_containers=1)。
5. retention dry-run 从真实 anchor/journal/marker/website/recovery artifacts 收集 reachability；仍禁止任何删除。
6. 调度测试完全离线；前端逐文件执行；补 fixed old/new score/ranking/trades/fees/holdings/cash/NAV 等价证据。

保留已经有效的 PC03–PC06、唯一 overlay provider、两个生产 App、三市场账户和全部 gate；不恢复 chenditc 主备，
不做多容器抓取、容器合并或镜像瘦身，不放宽 incremental、identity、数据或账户连续性校验。

本轮只做本地修复和验证。不要部署、push、运行付费 Modal、写生产 Volume、发布 GitHub 信号或执行归档删除。
完成后更新实施报告为 LOCAL REMEDIATED / NOT DEPLOYED，并列出测试先失败后通过的证据，交回 Codex 独立复验。
```
