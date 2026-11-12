# 唯一自建行情管道：第二轮复审与修复交接

> 历史交接：下文未提交工作区和失败验收均对应 2026-11-11 的审查基线；S01–S05 已后续修复并进入提交历史。当前部署与待验证边界见[生产状态记录](2026-11-12-production-deployment-status.md)。

> 日期：2026-11-11
>
> 接手模型：用户计划在新对话中使用 GPT 5.6 Sol。
>
> 结论：**第二轮复审仍未通过。剩余 4 个 P1、1 个 P2，禁止以现有测试全部通过替代验收。**
>
> 本文给出剩余问题的根因、建议修复方案和必要上下文。只新增文档，不修改生产代码、数据或部署。

## 1. 最重要的接手说明：修复在未提交工作区

写本文时：

- Git HEAD：`41b1c96bd`，`main` 与 `origin/main` 同步。
- **上一轮整改尚未提交**，包含 19 个 tracked 文件修改，以及新增 Python 模块/测试和第一轮整改文档。
- 本轮复审对象是 **HEAD 加当前工作区修改**，不是只审查 HEAD。
- 不要执行 `git reset --hard`、批量 restore/clean，不能把当前修改当成无用文件覆盖。
- 不要仅 checkout HEAD 到新 worktree 后就开始修复，否则会丢失上一轮已经有效的整改。
- 先重新检查状态，保留当前完整 diff 和新增文件。必要时按用户授权保存一个明确标记为“未验收”的本地检查点，但不得把它当作可部署版本。

### 1.1 交接时未提交修改范围

生产代码：

```text
quant/code/data_ingest/adjust.py
quant/code/data_ingest/cache.py
quant/code/data_ingest/gate.py
quant/code/data_ingest/overlay.py
quant/code/data_ingest/pipeline.py
quant/code/data_ingest/sina.py
quant/code/data_ingest/store.py
quant/code/ops/overlay_primary_probe.py
quant/code/ops/resource_sampler.py
quant/code/ops/website_simulation_modal.py
quant/code/production/modal_qlib_cn_a10g.py
quant/code/production/provider_build.py
```

另有相应测试修改，及新增：

```text
quant/code/ops/image_filters.py
quant/tests/test_image_filters.py
quant/tests/test_provider_build_recovery.py
quant/docs/07-code-review/2026-11-11-overlay-primary-review-remediation.md
```

以上是交接时状态，不是未来会话的实时事实。新会话要重新核对，保护其他会话新增的工作。

## 2. 用户已批准的方向与不变合同

目标：**现有 overlay 成为唯一自建日更数据管道**，使用冻结历史底座，正常日只采新增数据、增量计算账户。

```text
冻结历史 provider
  → 自建每日采集/校验
  → 内容寻址 archive + PASS receipt
  → 容器本地 materialize
  → 三市场信号 + 网站增量账户
```

- 保留 `qlib-prod-daily` / `qlib-prod-website` 两个生产 App。
- 06:00 主跑，07:30 通过同一主协调器串行恢复，09:00 只读检查。
- 保留市场隔离、模型/执行合同、费用、标签成熟度、purge、20-session 重训。
- 保留 journal、历史信号、实时 paper 状态和不可变快照。
- 不恢复 chenditc-first 或自动主备切源，不引入双源账户 lineage，不做容器合并或镜像瘦身。
- 网站模拟账户与实时 paper 不强行共用模型或状态。
- 不为通过验收放宽 PIT、复权、停牌和价格完整性 gate。

先阅读：

1. `AGENTS.md`。
2. `quant/docs/07-code-review/2026-11-10-overlay-primary-implementation-handoff.md`：完整方案。
3. `quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md`：原实施报告。
4. `quant/docs/07-code-review/2026-11-11-overlay-primary-review-remediation.md`：第一轮 R01–R10。
5. 本文：第二轮未关闭问题与新增缺陷。

## 3. 已验证的事实与证据范围

第二轮本地验证：

- 完整测试：**1045 passed、2 skipped、50 subtests passed**。
- 22 个变更/新增 Python 文件 Black 120 列检查通过。
- 新增本地合成场景复现了本文 5 项问题，无网络、无云端、无生产写入。
- 审查未修改任何用户代码或部署。
- 原实施报告仍未补充本轮整改结果，不应沿用其“只剩首次调度验收”的结论。

同一环境中可能仍有以下临时文件：

```text
/tmp/opencode/overlay_primary_re_review_repro.py
/tmp/opencode/overlay_primary_re_review_repro.log
/tmp/opencode/overlay_re_review_pytest.log
```

复现脚本 SHA256：`b1c5ead464abd06acbe57a1f1ab61aea07dbd97740db472a813ba47accf8609b`。

复现日志 SHA256：`1f86e82aed9d3b199a3ca81e72ecc8789f42817c0d1893dd160ec898026d7ff8`。

这些临时文件不是交付依赖，下文包含独立重建场景。应把验证转成正式回归测试，不提交机器私人路径或凭据。

### 3.1 第一轮整改状态

| 第一轮编号 | 第二轮结论 |
|---|---|
| R01：CSIndex 滞后清理不存在目录 | 已看到有效修复和 builder 层测试 |
| R02：archive 被增量入口拒绝 | 已看到修复和真实 asset 形状的候选测试；完整增量消费边界见 S04 |
| R03：无 base 行误当新股票 | 保护历史覆盖有进展，但锚点续接计算仍错，见 S02 |
| R04：hfq 对齐丢因子 | 已看到 union/ffill 修复及离线边界测试 |
| R05：坏 hfq 缓存误命中 | 缓存命中已修；重新获取仍失败时不阻断，见 S03 |
| R06：多日 PIT 未校验 | 已看到无逐日证据拒绝、逐日区间构建及测试 |
| R07：探针敏感文件过滤 | 已共享过滤器并增加测试，但接入引入入口路径错误，见 S01 |
| R08：materialize 缓存不命中 | 路径已修，但损坏日历仍命中，见 S05 |
| R09：子进程退出导致负 CPU | 已看到累计/下限标记和测试；不应把下限当精确利用率缩容 |
| R10：采样线程未停止 | 已看到统一 finally 清理和成功/失败测试 |

“已看到有效修复”仅指本地代码层检查，不代表已经云端部署或生产验收。

## 4. S01 / P1：新增 Modal 入口路径越界

**位置**：`quant/code/production/modal_qlib_cn_a10g.py:27–36`（当前工作区行号）。

### 根因

新增代码无条件计算：

```python
_LOCAL_QUANT_CODE = Path(__file__).resolve().parents[2] / "code"
```

本地 checkout 路径足够深，所以测试通过。Modal 序列化入口可能是：

```text
/root/modal_qlib_cn_a10g.py
```

此时父目录只有 `/root` 和 `/`，`parents[2]` 抛 `IndexError: 2`。错误发生在遍历候选路径之前，即使 `/root/qlib/quant/code` 正确存在，也无法进入选择分支。

### 复现

从源码 AST 提取 `_LOCAL_QUANT_CODE` 赋值，以 `__file__=/root/modal_qlib_cn_a10g.py` 执行：

```text
SERIALIZED_ENTRYPOINT: IndexError 2
```

### 建议修复

- 使用安全的祖先遍历或有界路径推导，避免无条件索引固定深度。
- 优先验证已知容器路径；本地回退只在需要时安全求值。
- 以是否存在 `quant_bootstrap.py` 判断候选有效，而不是只看目录名。
- 保持生产和 probe 可导入共享 `image_filters`，不通过重新复制私人绝对路径解决。
- 不只把 `parents[2]` 改成另一个固定下标而不测试容器/本地两种布局。

### 必须验收

- 本地标准 checkout 路径。
- 容器 `/root/<entry>.py` + 仓库 `/root/qlib/quant/code`。
- 更浅入口路径不会抛索引异常；缺少所有有效代码根时给出明确错误。
- 使用模拟文件布局/AST 或隔离 import 测试，不必为此启动生产任务。

**此项应先修，未修前不应构建/部署该生产入口。**

## 5. S02 / P1：旧锚点续接仍错误改变复权坐标

**位置**：

- `quant/code/data_ingest/overlay.py`：`read_last_finite_features()`。
- `quant/code/data_ingest/pipeline.py:594–619`：选择 anchor 并传入 `build_rows()`。
- `quant/code/data_ingest/adjust.py:46–85`：anchors 合并及相邻日复权公式。
- `_corporate_actions()` 也使用了同样错误的旧前收假设。

### 当前改进与剩余问题

上一轮已避免“因 base 当日 NaN 就覆盖全历史”。但新的 anchor 带有真实 `date`，下游计算忽略该日期，仍把旧锚点当作 `base_end` 的前收。

`preclose` 是目标日相对于前一交易日的参考价，不能直接与几天前的 raw close 比较来判断除权。

### 最小复现（没有任何公司行为）

```text
11-03：冻结 raw=10，adjusted=1，factor=0.1
11-04：provider 行缺失，真实 raw=11
11-05：raw=12，preclose=11

正确：adjusted close=1.2，factor=0.1
当前：adjusted close=1.090909，factor=0.090909
gate：PASS
```

算法把 `preclose=11` 与旧 raw=10 的差异误判为除权，用 `1 × 12 / 11`，漏掉中间正常涨幅。

### 建议修复方案

把 anchor 作为**有日期的状态**传递，而不只是三个数值：

1. 记录每只股票真实 anchor 日期及 frozen close/factor/adjclose。
2. 若 anchor 早于 base_end，确认 anchor 到目标日间所需的 raw、复权因子/公司行为、停牌证据。
3. 在内存中按实际交易日逐步续接至目标日；仅向新 provider 追加新日期，不回写冻结缺失行。
4. 或在可靠证据下按 anchor 日与目标日的同一调整坐标求比率。无论采用哪种算法，都必须验证端点和途中公司行为的含义。
5. 无法证明安全续接时 fail-closed，不能把跨度数日的 preclose 当作相邻日证据。
6. gate 必须能独立发现坐标错误，不能只用相同的错误公式生成 expected_factor 再比对 actual。

可按缺锚股票的小范围补取区间，不需要重抓全市场或建设第二套 provider。

### 必须验收

- 上述 10→11→12 无除权例子，结果为 1.2/0.1。
- anchor 与 target 之间一次/多次公司行为。
- 连续停牌后复牌，以及退指后仍持仓重新需要报价。
- anchor 至 target 中途缺证据时明确失败。
- frozen 历史所有已有文件前缀不变。
- 新增测试应断言价格/factor 的正确数值，不能只断言“有限、PASS、历史没被覆盖”。

## 6. S03 / P1：补拉后仍缺因子、备用查询失败，却可 PASS

**位置**：

- `quant/code/data_ingest/pipeline.py:508–558`：suspect / BaoStock 获取。
- 同文件约 564–619 行：合并数据、生成 preclose、调用 build_rows。
- `quant/code/data_ingest/adjust.py:73–91`：无有效复权证据时退回普通收益公式。

### 根因

R05 已让坏缓存触发补拉，并把 hfq 无效股票加入 BaoStock suspect。

但如果补拉后 hfq 仍缺失，BaoStock 也失败，只会记录 error；目标 raw 仍保留，`build_rows()` 会延用旧 factor，gate 不要求这些股票有充分的调整证据。

### 复现

选一只不在交叉校验样本中的股票：

```text
旧 close=11
目标 raw close=5.5
Sina hfq 缺失
BaoStock 查询失败，目标日 preclose 不可得

当前：provider PASS，adjusted close=5.5
```

这不证明现实发生了除权，但证明程序在**不能区分正常跌幅和除权变化**时仍声称通过。股票未进入抽样不能成为放行理由。

### 建议修复方案

- 把“所有必要股票是否具备有效行情和调整证据”作为 row build 前或 gate 内的明确条件。
- 有有效 raw＋hfq 边界证据时按已有合同计算。
- hfq 无效但有效 BaoStock preclose/公司行为证据足够时，允许受控备用计算并记录来源。
- 两类证据都不足时明确拒绝该日期 provider，不默认普通日、旧 factor 不变。
- 完整性校验作用于**缓存与本次新获取数据合并后的最终输入**，不能只用于是否命中缓存。
- 保留合法停牌例外，但要求日期化证据，不能以“查询失败”推定停牌。
- 对 partial raw 的回退也检查按 symbol/date 合并的有效性，不仅检查 symbol 是否存在。

### 必须验收

- 缺 hfq + BaoStock 失败 + 非抽样股票：必须 fail-closed。
- 缺 hfq + 足够有效 preclose：数值正确，receipt 标记来源。
- hfq 有效 + 非必要备用请求失败：仅在证据仍满足合同的情况下通过。
- 缓存坏→补拉仍坏、缓存坏→补拉恢复两个分支。
- 不能通过减少交叉校验要求或忽略 error 来修复。

## 7. S04 / P1：网站增量报价集合漏掉已有退指持仓

**位置**：

- `quant/code/research/regime_reselection/prelive_revalidation.py:89–130`，`prepare_data()`。
- `quant/code/ops/website_simulation_modal.py` 增量分支：`prepare_data()` 在 `_load_prior_ledger()` 之前调用。

### 根因

provider 侧已经将 paper/website 持仓并入采集集合，但 website 消费侧仍用：

```python
signal, provenance = load_signal(..., start, end)
names = sorted(set(signal.index.get_level_values("instrument")))
quotes = D.features(names + [benchmark], ...)
```

增量 start/end 通常仅为上一日和新增日。股票退指超过一天且仍持有时，可能不在这两天的评分中。

### 复现

```text
上一状态持仓：SH600001
当前两日评分：只有 SH600000
provider：两只股票行情都存在

D.features 实际请求：SH600000 + benchmark
未请求：SH600001
replay：产生该持仓 open/close gaps
发布：website simulation cannot publish missing held quotes
```

因此 R02 修复使增量真正启用后，会暴露此边界。不能只验证增量候选返回非 None。

### 建议修复方案

1. 增量分支先读取并验证 prior ledger/final_state。
2. `prepare_data()` 增加向后兼容的额外报价股票参数，或等价的显式执行 universe 参数。
3. 报价集合取“当前评分股票 ∪ 恢复持仓 ∪ 必要待执行对象 ∪ benchmark”。
4. 此并集仅用于报价/上市日期/输入证据，不把退指股票塞回评分 universe，不改变排名和买入候选。
5. 对新增报价股票同样验证文件完整性、记录哈希、获取 listing 信息。
6. 保留原缺失 held quote 的发布 gate；真正缺价时仍失败，不能放宽估值规则。

### 必须验收

- 退指超过一个 session、仍未卖出的持仓，有价时不产生伪缺价。
- 因涨跌停/停牌未能卖出，跨多个增量日继续正确估值和处理订单。
- 真正缺持仓报价时仍阻断发布。
- 增量/full 在固定输入下逐日 NAV、现金、持仓、成交、费用一致。
- 覆盖生产 TopK/DropN 的真实 blocked-sell 情形，不仅用无换手的最小 fixture。

## 8. S05 / P2：materialize 缓存仍接受损坏日历

**位置**：`quant/code/data_ingest/store.py:259–276`，缓存命中分支。

### 根因与复现

R08 已修复 relative_root，正常缓存不重复解包。但命中仅检查 calendar 文件存在、manifest 哈希匹配，未检查 calendar 内容身份。

```text
snapshot.calendar_end = 2026-11-10
首次 materialize 成功
将本地缓存 day.txt 截断为仅到 2026-11-09
第二次 materialize 返回坏缓存，末日仍为 2026-11-09
```

读取 manifest 不会发现 calendar 被单独改坏。可能导致错误 freshness 判断、重复失败或错误的本地 provider。

### 建议修复方案

- 缓存命中时至少验证实际 calendar SHA256 与 snapshot.calendar_sha256，及必要的日期/结构条件。
- 不只比较 ready marker 内的声明，必须读取被消费文件。
- 不完整/损坏缓存按合同丢弃该 digest 的本地 scratch 缓存并重新校验解包，或明确报错；不能返回它。
- 不需要为此每天重新读取远程 archive 或遍历全部行情文件。
- 首次解包路径也应检查完整 calendar 身份，而不是只看末日；统一验证函数减少两条路径漂移。

### 必须验收

- 正常命中：远程 archive 读取次数、extractor 次数不增加。
- 截断日历、同末日但中间日期改变、空日历：不能命中坏缓存。
- marker 损坏、目录缺失、digest 改变：恢复策略正确。
- 验证异常不能破坏其他 digest 的正常本地缓存或生产 archive。

## 9. 测试与报告需要补充的内容

### 9.1 本轮测试盲点

- 本地入口路径很深，未模拟 `/root/<entry>.py`。
- 旧 anchor 测试只断言结果有限、历史不变，未检查 factor/close 数值。
- 因子缓存测试只测补拉成功，没测所有调整证据仍不足。
- 网站候选测试并不等于完整 `prepare_data → replay → build_website_series` 验证。
- materialize 缓存测试测了目录/marker 缺失，没测日历内容损坏。

### 9.2 建议正式测试落点

| 问题 | 测试位置建议 |
|---|---|
| S01 | cron/bootstrap/CLI 相关测试，模拟云端入口路径 |
| S02 | `test_pipeline_resilience.py` + 调整公式测试 |
| S03 | `test_pipeline_resilience.py` + `test_ingest_cache.py` |
| S04 | `test_prelive_revalidation.py` + `test_website_simulation.py` |
| S05 | `test_provider_store.py` |

先把失败场景写成测试，再修复。保持既有有效的 R01–R10 回归测试，不用修改预期来迎合错误结果。

### 9.3 报告状态

原实施报告未更新到本轮工作区状态。应补充：

- 第一轮每项整改状态与 S01–S05 的关闭证据。
- 当前修复是否提交、是否部署；当前测试版本不能冒充生产版本。
- 测量的范围和局限；process CPU 下限不是精确利用率。
- 新日期构建、archive 消费、完整增量账户和重试短路的集成证据。
- 未验证的生产门禁，不再写“只剩首次调度”而省略已知缺陷。

## 10. 实施顺序与验收要求

1. 检查工作区，保留上一轮修复；确认没有其他会话同时修改相同文件。
2. 优先修 S01，避免后续云端任务 import 阶段失败。
3. 修 S02/S03，先保证行情、复权和证据正确。
4. 修 S04，验证完整增量消费链路。
5. 修 S05，验证本地缓存恢复。
6. 重跑完整 `quant/tests` 和受改文件格式检查，更新整改/实施报告。
7. 本地收敛后，再按授权范围安排有界隔离实测；没有授权不得擅自发布/改写历史。
8. 修复集中验收后再考虑部署，避免每个小补丁都改变 source identity 并触发重复预热。

隔离验收至少包含：

```text
真实 archive 形状的父快照
→ 新日期构建（成分、因子、历史保护）
→ 新 archive/receipt
→ 信号消费
→ 网站增量（包含退指持仓）
→ 正确 series/ledger
→ 重试 receipt 校验后短路
```

必须保持真实缺价、证据不足时 fail-closed。不能为了通过链路而降低校验、伪造 PASS 或补造历史信号。

## 11. Modal 与仓库操作纪律

- 先读并遵守当前 AGENTS，而不是只依赖本文历史上下文。
- 生产 Volume 在探针中只读，只写隔离 probe Volume，无发布 secret。
- 不在临时 App 中 `.local()` 调用生产写逻辑并绕过单写入口。
- Modal 实验 attached 前台执行，禁止 `modal run --detach`，禁止 nohup/shell 后台替代。
- 工具 timeout 必须覆盖镜像构建、排队、worker、收尾和结果取回；不足则停止并请求有界批次或合适的前台执行方式。
- 保存真实退出码、source/input SHA、App/call ID、阶段日志与账单。超时后先查状态，不能盲目重跑。
- 不提交私人工作区/profile 名、个人文件系统路径、凭据或敏感日志。
- 不删除用户未提交代码、生产快照、journal、账户状态、旧 secret。
- 从仓库根目录测试；Black 120 列，按 AGENTS 使用合适检查入口。不改 upstream Qlib 和生成版本文件。

## 12. 可直接复制到 GPT 5.6 Sol 新对话

```text
请先阅读 AGENTS.md，以及：
quant/docs/07-code-review/2026-11-10-overlay-primary-implementation-handoff.md
quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md
quant/docs/07-code-review/2026-11-11-overlay-primary-review-remediation.md
quant/docs/07-code-review/2026-11-11-overlay-primary-second-review-handoff.md

请修复第二轮复审的 S01–S05，并保留上一轮已经有效的 R01–R10 修复。
非常重要：Git HEAD 仍为 41b1c96bd，但上一轮修复在未提交工作区，包含新模块
和测试。先核对并保护这些修改，不要 reset/restore/clean 或只在纯 HEAD 上开发。

先把复现场景变成回归测试，再修复。重点：
1. Modal /root/<entry>.py 导入不能 parents[2] 越界。
2. 旧 anchor 日期必须参与续接，不能把跨日涨幅误作除权。
3. hfq 和备用调整证据同时不足必须失败，校验涵盖新抓取结果而非只查缓存。
4. 网站增量报价集合包含恢复持仓和必要执行股票，但不修改评分 universe。
5. materialize 命中须校验实际 calendar 身份，坏缓存不可返回。

保持唯一自建 provider、两个生产 App、三市场和账户合同，不恢复 chenditc
主备、不做容器合并或镜像瘦身，不放宽 gate。更新整改状态和实施报告，
明确哪些已本地验证、哪些尚未提交/部署/生产验收。

云端实测仅按授权范围执行，生产输入只读、输出隔离、有界前台 attached，
严禁 modal run --detach。先本地收敛再集中验证，避免重复付费预热。
```

## 13. 最终交付物

- S01–S05 与第一轮 R01–R10 的闭环状态表。
- 修复提交、正式回归测试与完整测试输出。
- 固定输入下价格/factor、信号、成交、持仓、现金、费用、NAV 的证据。
- archive 新日期与网站增量的隔离集成结果。
- 更新后的实施报告及未完成项。
- 若部署，明确两个 App 的最终提交/版本、cron 与真实验收；若未部署，明确写未部署。

**验收标准是数据与账户路径正确、失败可解释、恢复不重复，不是测试总数或部署 acknowledgement。**

## 14. 实施后状态附记（当时的未提交工作区，2026-11-11）

本文第 1–13 节是第二轮复审当时的历史结论。后续已在同一个未提交工作区中把 S01–S05 复现转为正式测试并完成
本地修复，同时保留 R01–R10。完整 `quant/tests` 结果为 **1062 passed、2 skipped、50 subtests passed**；详细逐项
证据见 `2026-11-11-overlay-primary-implementation-report.md` 第 11 节和
`2026-11-11-overlay-primary-review-remediation.md` 第 17 节。

该阶段的状态边界没有改变：Git HEAD 当时仍为 `41b1c96bd`，全部整改尚未提交/推送；未执行 Modal、未部署两个 App、未写生产
Volume，也未完成真实全市场新日期与网站增量生产验收。因此原文“复审未通过”已被“本地代码/回归关闭”更新，
但不能改写为已部署或生产通过。

## 15. 第三轮复权边界关闭与本地验收（2026-11-11）

第 14 节之后的独立复审确认 S01–S05 原始场景已有修复，但又发现两处公司行为 P1：小 factor 下的绝对容差会漏算
真实调整；hfq 有限时会忽略已经取得的精确 preclose。现已改用尺度无关比较及 BaoStock preclose 优先，并保护多日混合
证据下的连续性，禁止已知冲突的 hfq 撤销精确调整。详细证据见实施报告第 12 节。

新增 35 个参数化场景后，完整本地测试为 **1097 passed、2 skipped、50 subtests passed**；网站 Node 测试 **30 passed**，
24 个变更/新增 Python 文件 Black 120 列和 `git diff --check` 通过。原独立复现的三种调整价收益乘数均恢复为 1.0。
用户已授权修复后把累积整改一起提交/推送，因此前文未提交状态为历史接手上下文，最终版本应查询 Git。
本轮未部署、未运行 Modal、未改生产数据，仍需有界云端和真实新日期验收。
