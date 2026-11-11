# 收盘后统一生产、网站身份与公开榜单整改实施交接

> 日期：2026-11-11
>
> 状态：`APPROVED PLAN / IMPLEMENTATION NOT STARTED / NOT DEPLOYED`
>
> 实施者：DeepSeek
>
> 独立验收：Codex
>
> 性质：实施交接，不是部署批准、生产 PASS 或收益认证。

## 1. 结论与用户决策

本轮按以下方向实施：

1. 把 provider、信号发布、20-session 重训和网站收益更新统一移到交易日收盘后。重计算不再依赖次日盘前窗口；当晚先做只读延迟检查和恢复，次日 07:00 再做最终只读核验。
2. 拆分“完整发布源码身份”和“网站计算语义身份”。资源、schedule、Modal 装饰器或文档类变化仍改变发布身份，但不再无条件使网站增量祖先失效。
3. 删除程序内按日/月 USD 金额阻断网站运行的预算控制。保留只读费用观测，但费用 API 不得决定业务是否执行。
4. 公开榜单改成账户实际使用的原始 TopK，不再因 T 日收盘涨跌停过滤并补位。涨跌停只作为信息提示，T+1 是否成交仍由开盘执行规则决定。
5. 网站收益继续明确标注为因果纸面模拟；本轮不补做券商成交、逐日 ST、开盘流动性或官方参考价认证。
6. 已有 Modal 邮件通知视为当前外部告警通道；本轮不新增 Slack、webhook 或邮件实现，只在部署验收时核对失败邮件实际可达。
7. 不采用多容器抓取。Sina 仍在单个 Provider 容器内有界并发，并补连接复用、429/`Retry-After`、抖动退避和限流指标。
8. 其余 P2 按前次审查建议处理：网站生产输入只读挂载、公开方法页与索引纠正、代码身份 fail-closed、归档保留具备可审核的执行机制。

## 2. 接手前必须保护的工作区

本文最初起草时的工作区状态：

- Git HEAD：`c05c59144d98eb85f3c4d7966a9a201b04517c4f`。
- 当时有 6 个 tracked 文件未提交修改：

```text
quant/code/ops/website_simulation_modal.py
quant/code/production/modal_qlib_cn_a10g.py
quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md
quant/tests/test_modal_structure.py
quant/tests/test_post_rollout_regressions.py
quant/tests/test_website_simulation.py
```

- 上述资源调整代码与测试已经整理为独立检查点提交 `9b5aa359c`：Provider `2 CPU / 8 GiB` 和独立 Website incremental `4 CPU / 12 GiB`；full/smoke/retrain 仍为 `8 CPU / 16 GiB`，Signal 仍为 `8 CPU / 16 GiB`。
- 生产 v20 仍是 Provider `4/8`、统一 Website `8/16`；不要把本地候选写成已部署事实。

交接文档、实施报告状态和总索引在资源代码检查点之后作为独立文档提交。DeepSeek 开始前应看到干净工作区，并重新运行 `git status --short`、`git log -2 --oneline` 和 `git diff --check`；若实际状态不干净，应先理解并保护新增 diff。禁止 `reset --hard`、`restore`、`clean`、覆盖式 checkout，禁止只在旧纯 HEAD 上重做。不要把 `9b5aa359c` 的实现拆掉后再复制一份相同代码。

## 3. 不变合同与明确非目标

必须保留：

- 唯一自建 overlay provider；日常路径不恢复 chenditc-first、自动主备或双源账户 lineage。
- `qlib-prod-daily` 与 `qlib-prod-website` 两个生产 App；不合并容器，不做镜像瘦身。
- CSI300、CSI500、CSI1000 三市场及各自独立 1M 账户。
- T 日收盘评分、T+1 开盘执行；CSI300/CSI1000 Top10/Drop1、CSI500 Top20/Drop2。
- 20-session 重训、标签成熟度和 purge、PIT 成分、板块/日期涨跌停、数量规则、5bp/15bp 与最低 5 元费用。
- publication journal 单写者、不可变 payload、内容寻址 provider archive、网站 series/ledger 哈希、账户连续性和失败恢复。
- Provider、信号、网站的缺价/复权/日历/身份 gate；不得为缩短运行或迁移缓存而放宽。
- ChiNext 继续暂停，STAR 状态不变。

本轮不做：

- 实盘下单、券商 fill 认证、逐日历史 ST 或开盘盘口采购。
- 策略重选、调参、TopK/DropN 变化或历史收益重写。
- 多容器抓取、代理轮换或用出口 IP 规避源站限制。
- 自动删除生产数据；首次归档清理必须经过 Codex 验收和操作者对明确删除清单的单独批准。
- DeepSeek 自行部署 Modal、写生产 Volume、更新生产 GitHub 信号或触发付费全量回放。

## 4. 目标时序：T 收盘后完成，T+1 盘前只读

### 4.1 日期合同

对于交易日 `T` 和下一交易日 `U`：

```text
T 18:00
  provider 数据截至 T，确认 T 已完成
  → 结算原 pending 在 T 开盘的执行并以 T 收盘估值
  → 网站三账户 NAV 更新到 T
  → 到期模型用截至 T 且标签已成熟的数据重训
  → 使用 T 收盘特征生成 U 榜单
  → 创建 U 开盘 pending
  → 三市场 publication journal + website receipt + postclose marker

T 23:00
  无 secret、生产 Volume 只读的当晚延迟检查
  → 完整 marker 已通过则返回 OK
  → 未完成/不一致则显式失败，由 Modal 邮件发送当晚预警

T 23:15
  同一 daily_cron 单写协调器的幂等恢复
  → 主跑仍在执行则排队，不产生第二写入者
  → 完整 marker 已通过则只读校验后退出

U 07:00
  无 secret、生产 Volume 只读
  → 核验 data_asof=T、usage_date=U、provider/journal/website/marker
  → 失败由已配置的 Modal 邮件再次通知操作者，距开盘仍有 2.5 小时
  → 不启动第三条重计算路径
```

`signal_date` 在代码和 receipt 中应逐步改名或明确别名为 `usage_date`，`data_asof` 明确为 `session_date`。不得继续用“today”隐式推断两个不同日期。

### 4.2 调度合同

目标 cron（Asia/Shanghai）：

| 时间 | 入口 | 资源/权限 | 合同 |
|---|---|---|---|
| `0 18 * * 1-5` | `daily_cron` | 单写、带发布 secret | `T → U` 收盘后主跑；保持函数名以减少外部 lookup 迁移 |
| `0 23 * * 1-5` | `daily_postclose_check` | 无 secret、Volume 只读 | 当晚首次检查；未完成即失败并触发已配置的 Modal 邮件 |
| `15 23 * * 1-5` | `daily_evening_retry` | 无发布 secret、Volume 只读 | 只转发同一 `daily_cron`；不新增独立 heavy path；`max_containers=1` 排队恢复 |
| `0 7 * * 1-5` | `daily_readonly_check` | 无 secret、Volume 只读 | 次晨最终核验当日 `U` 对应上一交易日 `T` 的完整 receipts |

删除 06:00 主算和 07:30 重算 schedule。现有 `daily_evening_manual`、`daily_evening_retry_manual`、`daily_cron_manual` 只能作为无 schedule 的显式恢复入口，并继续转发唯一协调器。

### 4.3 为什么不在 20:00 重试、09:00 才检查

- 普通日历史关键路径约 45–65 分钟，20:00 通常已经完成；但三个市场同日重训预计约 3.2–3.8 小时，叠加首次 website full 可能达到约 4–4.5 小时，20:00 不能作为重训日完成门。
- `daily_cron(max_containers=1)` 能保证 20:00 调用只排队、不双写，但这意味着它很可能只是等待 18:00 主跑，并没有在两小时处获得真实恢复机会；同步等待还可能占用 retry 容器和费用。
- 23:00 给最慢的已知正常路径约 5 小时，同时仍在当晚提供首次邮件预警；23:15 recovery 即使完整重跑约 4.5 小时，通常也能在次日清晨前结束。
- 次日 07:00 final check 距 09:30 开盘 2.5 小时。它仍不是第三条 heavy path，但比 09:00 才发现问题更适合人工核对或执行受控的部分恢复。
- 23:00 检查可能在极端长尾主跑仍执行时发出延迟邮件；这是有意的早期告警，不把“仍在运行”误写成已完成。23:15 recovery 继续由平台单执行排队，绝不并发写入。

### 4.4 必须覆盖的日期边界

- 普通连续交易日：周二 18:00 生成周三榜单并更新周二 NAV。
- 周五：周五收盘生成下一实际交易日榜单，不把周末当 session。
- 长假前最后交易日：`usage_date` 是假期后的第一交易日。
- 工作日但非交易日：18:00/23:00/23:15 零重算跳过；不能使用“上一自然日”。
- T 行情未完整、provider calendar 不是 T、当前时间早于完整收盘门：fail-closed。
- T 23:00：检查 marker key 为 U，provider/website 最新估值日必须是 T；不得因主跑仍运行而伪造完成。
- U 07:00：再次检查同一 U/T 身份；只读函数不得启动重计算。
- 手动 override 必须同时提供并校验 `usage_date`、`session_date`；两者必须是相邻真实交易日。

## 5. 整改项总表

| ID | 级别 | 目标 | 本轮状态 |
|---|---|---|---|
| PC01 | P1 | 收盘后统一计算与次日只读检查 | 待 DeepSeek 实施 |
| PC02 | P1 | 网站发布身份与计算语义身份拆分 | 待 DeepSeek 实施 |
| PC03 | P1 | 删除程序内 USD 预算阻断，保留观测 | 待 DeepSeek 实施 |
| PC04 | P1 | 公开榜单与账户排名统一 | 待 DeepSeek 实施 |
| PC05 | P2 | 网站生产 Provider Volume 只读 | 待 DeepSeek 实施 |
| PC06 | P2 | 单容器 Sina 网络健壮性 | 待 DeepSeek 实施 |
| PC07 | P2 | 代码身份 fail-closed 与可审核归档保留 | 待 DeepSeek 实施 |
| PC08 | P2 | 方法页、索引、handoff 与状态报告一致 | 待 DeepSeek 实施 |
| D01 | 接受限制 | 网站收益保留因果纸面模拟 | 本轮不认证，但不得弱化披露 |
| D02 | 外部依赖 | Modal 邮件通知 | 不加新通知；部署时实测核对 |

## 6. PC01：收盘后统一生产

### 6.1 实现要求

1. 把 `_evening_overrides()` 提升为唯一的 `T → U` 日期解析合同，输入时钟可注入测试；不要在主跑、重试和检查器各写一套日期算法。
2. `_run_preopen_cycle`、`preopen_status` 等命名可兼容读取旧 marker，但新写入使用 `postclose_status` 或 schema v2，并明确：
   - `session_date=T`
   - `usage_date=U`
   - `provider_calendar_end=T`
   - `website_latest_date=T`
   - `journal_signal_date=U`
3. 旧 marker 只作为历史证据；不得覆盖。若保留兼容索引，新旧 schema 必须可区分且校验完整。
4. `require_reference_preopen()` 保留“收盘后至 U 开盘前”的合法窗口，删除调度对同日早晨重算的依赖，但不要删除历史回放所需的兼容读取。
5. Signal、website 两阶段仍由 `run_daily_pipeline` 独立记录成功/失败；最终 marker 只有在两者 receipts 均通过时写 `COMPLETED`。
6. 23:00 当晚 checker 和 U 07:00 final checker 都不带发布 secret、不写 Volume，也不执行 provider、训练、信号或网站 worker。
7. 23:15 retry 使用只读 Volume、不得携带发布 secret，并通过 `.remote()` 排队到同一 `daily_cron(max_containers=1)`；禁止 `.local()` 绕开平台单执行合同。

### 6.2 先写失败测试

建议落点：

- `quant/tests/test_daily_pipeline.py`
- `quant/tests/test_cron_gate.py`
- `quant/tests/test_modal_structure.py`
- `quant/tests/test_csi1000_mvp_contract.py`
- `quant/tests/test_post_rollout_regressions.py`

至少包括：

- 18:00 主跑、23:00 当晚检查、23:15 retry 和 07:00 final check 的 AST/decorator 守护；06:00、07:30 不再注册 heavy path。
- 普通日、周五、长假、非交易工作日的 `T/U` 解析。
- 17:59 拒绝，18:00 可运行；provider 未结束于 T 拒绝。
- U 的 journal/榜单使用 T 数据，标签成熟和训练截止不出现 U 数据。
- T 网站 NAV 使用此前冻结的 pending，不读取刚生成的 U pending 作为 T 成交。
- 23:00 未完成时明确失败并形成邮件告警证据；不得把 `RUNNING` 当 `COMPLETED`。
- 23:15 与主跑重叠时只排队同一入口；已完成时不重复训练、发布或结算。
- 07:00 无 secret/只读挂载，receipt 任一不匹配即失败。

### 6.3 验收

固定小日历逐项证明：价格、factor、score、训练快照、榜单、pending、成交、费用、持仓、cash、NAV 与原 T-close→T+1-open 合同一致；变化只能是任务发生时钟和新榜单合同 PC04，不能出现同日开盘前视。

## 7. PC02：拆分网站语义 identity

### 7.1 根因

当前 `source_manifest("training")` 哈希整个 `quant/code/**/*.py`。同时，网站 replay core 仍从大型 Modal launcher 导入 `_load_and_patch_cfg`、`MODEL_CFG` 和交易日历函数，资源装饰器与业务语义处在同一文件。因此 CPU、memory、schedule、日志或无关工具改动都会改变缓存 lineage，强制 smoke + full replay。

### 7.2 目标身份

新增并同时记录三类身份：

| 身份 | 内容 | 用途 |
|---|---|---|
| `release_source_sha256` | 完整 `quant/code` 可执行源码 + 配置 | 审计、两个 App 部署一致性、receipt 追踪；任何代码变化都应改变 |
| `website_semantic_sha256` | 真实影响模型、预测、账户 replay、费用、交易规则和 series 的依赖闭包 | website model/result cache 与 incremental ancestor key |
| `account_contract_sha256` | market/profile/horizon/TopK/DropN/费用/本金/起点/重训周期/执行规则 | 防止语义 hash 漏绑业务合同 |

Provider digest、calendar identity、provider source 和父 archive lineage 继续独立校验，不能被 semantic hash 代替。

### 7.3 代码拆分

优先把业务实现移出 Modal launcher，而不是对源文件做正则删 decorator：

- `website_simulation_modal.py` 只保留 App、Volume、资源装饰器、路由、publish 和 worker 薄入口。
- 新增 Modal-free replay core（文件名由实施者按现有目录风格确定），承载 `_build_market_impl` 及其纯业务辅助函数。
- 网站需要的模型配置、calendar 和 dataset 构造从 `modal_qlib_cn_a10g.py` 移到单一共享的 Modal-free production 模块；信号与网站共同导入，禁止复制。
- `research_identity.py` 提供 versioned semantic manifest；显式依赖表必须包含被 replay core 导入的 extension 文件和训练 YAML。
- worker import 完成后校验实际加载的仓库内业务模块属于声明闭包。出现未登记的新业务依赖必须 fail-closed，而不是静默漏哈希。

### 7.4 Cache 与 receipt

- 新 website 路径以 `website_semantic_sha256 + account_contract_sha256 + provider digest` 定位。
- `release_source_sha256` 每次发布都写入 result/receipt，但只改变它不得拒绝同语义祖先。
- `_incremental_prior` 必须继续校验：相邻 session、父 digest、provider source、calendar、series、ledger、final state、model cache、market/account contract。
- semantic 或 account contract 任何变化必须拒绝增量并进入受控 full；不得提供 `allow_mismatch`。
- 两个生产 App 仍须核对同一 release identity，避免不同部署代码互相调用。

### 7.5 一次性迁移

不实现复杂的旧 SHA alias 或人工 identity bridge。首次部署新 identity schema 时允许一次预期内、受限的 8/16 smoke + full replay：

1. 使用同一 frozen provider，逐日比较旧/新 score、排名、成交、费用、持仓、cash、NAV。
2. 除新增身份字段外要求精确一致；PC04 引起的公开 CSV 差异单独解释，账户路径应不变。
3. 新 full 结果成为后续增量祖先。
4. 下一新交易日必须实际走 4/12 `INCREMENTAL`，证明资源/schedule 文件变化不再强制 full。

### 7.6 必须测试

- 只改 Modal `cpu/memory/schedule/scaledown_window`：release SHA 改变，semantic/account SHA 不变，增量可复用。
- 只改文档、日志文本或非语义 launcher：同上。
- 改模型参数、特征、标签成熟度、paper accounting、board rules、费用或 replay：semantic/account 至少一个改变，旧祖先被拒绝。
- 新业务 import 未登记：构建 manifest 或 worker 启动立即失败。
- release 不同但 semantic/account 相同的直接父结果可以增量；非直接父、跨源、坏 ledger 或坏 model cache 仍失败。
- lost-response FULL cache 恢复继续复核 series/ledger/input identity，不在 4/12 worker 内执行 full。

## 8. PC03：删除程序内预算阻断

### 8.1 实现要求

- 删除 `daily()` 和本地 entrypoint 中 `_budget_guard()` 对执行的阻断。
- 删除生产结果状态 `SKIPPED_BUDGET`，并同步清理 daily pipeline、status/checker 和测试中的成功/失败分支。
- `website_budget.py` 中只为 USD 阻断服务的 policy/decision 代码和相应测试应删除，不保留不可达死代码。
- 可以保留一个只读 `billing_status`/usage snapshot 用于 receipt 或人工查询；API 错误只记录 `unavailable`，不能阻断、放行或改变 replay 模式。
- 不修改 Modal workspace 自身的外部额度、账单或邮件配置。
- 不删除以下计算安全门：`new_fits > 3` 有界恢复、worker timeout、重试上限、三市场原子发布、provider/ledger/account gate。

### 8.2 测试

- 构造当日/月度费用远超旧阈值，website 仍按输入执行。
- billing API 抛异常，website 仍执行且 telemetry 标为 unavailable。
- 源码和状态机中不再存在 `SKIPPED_BUDGET`。
- 超过 3 个 fresh fit 仍失败，证明“取消美元预算”没有变成“取消工作量边界”。

## 9. PC04：用一套榜单同时服务公开页面和账户

### 9.1 选择的简洁方案

从新合同版本起：

```text
published_selection = ranking_full[:topk]
paper_planner_input = ranking_full
```

不再用 T 日收盘涨跌停集合删除公开榜单股票，也不从第 `topk+1` 名开始补位。原因是榜单表达模型的 T-close 原始选择，账户也是按这套排名规划；T 日收盘状态不能替代 U 日开盘是否可成交的判断。

用户只看到一套“模型原始 TopK”榜单。页面在表格附近显示固定说明：

> 榜单与模拟账户使用同一原始排名。T 日收盘涨跌停仅作状态提示，不从榜单补位；实际买卖在下一交易日开盘按停牌、涨跌停和账户现金规则判定，无法成交时保留原持仓或现金。

无需同时展示“原始榜单”和“过滤榜单”两个表，也不把榜单误称为当日实际持仓。

### 9.2 数据合同

- CSV 继续保持 `rank,instrument,score` 三列，降低前端和历史消费者迁移风险。
- `mvp_metadata` 增加 `ranking_contract_version=2`、`published_selection_instruments` 和该列表/score 的哈希。
- 可在 metadata 中记录 `t_close_limit_annotations`，但必须标记 `informational_only=true`；缺少注释不得改变排序或补位。
- v2 validator 强制 CSV 的 instrument/score 与 `ranking_full[:topk]` 完全相等、顺序一致；paper planner 继续读取完整 ranking。
- 历史 v1 文件、journal 和账户状态不可重写。v1 继续按旧字段读取；首次 v2 发布日期在部署时冻结到 release record。
- 前端继续把“榜单”“当前持仓”“成交记录”分开展示，不把入榜等同买入。

### 9.3 测试

- 原 TopK 中包含 T-close 涨停、跌停股票：CSV 不删除、不补位，metadata 仅加提示。
- CSV 前 K 名与 `ranking_full` 前 K 名逐项相等，三市场全部覆盖。
- paper planned buy/sell、T+1 blocked order、cash/NAV 与改榜单展示前完全一致。
- chart 股票集合与新 CSV 一致，无因旧过滤残留而缺图。
- v1 历史 artifact 仍可读取；v2 缺 identity、顺序不一致或伪造注释时 fail-closed。
- 前端安全、空值、同分 instrument 升序和 Top10/Top20 行数测试继续通过。

如果实施中发现必须并存两套榜单、重写历史账户或改变 TopK/DropN 才能完成，应停止 PC04 并回报，不得自行扩大产品语义。

## 10. PC05：网站生产输入只读

在 website App 中创建 `data_volume_readonly = data_volume.with_mount_options(read_only=True)`，以下函数的 `/data` 一律使用只读对象：

- full/smoke/retrain worker；
- 4/12 incremental worker；
- website daily coordinator。

`/simulation` 保持可写；provider archive 物化到本地 scratch，不写回 `/data`。测试检查所有 website Function decorator，不只检查其中一个 worker。

## 11. PC06：单容器 Sina 网络健壮性

保持 `ensure_overlay(max_containers=1)` 和 `SinaSource(workers=8)` 上限，不增加远程 shard。

实现：

- 每线程复用 `requests.Session`，配置与 workers 匹配的连接池；
- 对 429、502、503、504 和连接超时做有界指数退避 + full jitter；
- 429/503 若带合法 `Retry-After`，在全局上限内遵守；异常或超大值要 clamp；
- 增加单 host token bucket，初始上限不高于 8 request/s，并允许通过受审配置下调，不能通过环境变量无限提高；
- 统计每 host 的请求数、状态码、429、重试、sleep、p50/p95 延迟和成功数，写入 provider metrics；
- 外层 symbol retry 不得在 factor 请求失败后无条件制造同步历史请求风暴；可复用本轮已成功的 history 响应，但缓存必须绑定 symbol/url/payload hash 且只在单次 build 内有效；
- 不修改 raw/hfq 算法、anchor、BaoStock 证据或 gate。

Mock 测试覆盖 `Retry-After` 秒数/HTTP 日期、jitter 边界、session 复用、history 成功 factor 失败、deadline 取消和 metrics。真实网络性能只在后续授权的隔离 probe 中验证；本地 mock 不能宣称源站限流已消失。

## 12. PC07：代码身份与归档保留

### 12.1 代码身份 fail-closed

生产 provider publication、postclose marker 和 website receipt 不得写 `code_source_sha256="unavailable"`。构造 release/semantic identity 失败时，在任何 archive、GitHub 或账户状态写入前失败。

测试：manifest 文件缺失、不可读、非有限/非法哈希、两个 App identity 不一致均拒绝发布；本地探针也必须显式报告失败，不伪造 identity。

### 12.2 归档保留

先实现 reachability-aware retention plan：

- 永远保留冻结 anchor、latest PASS、最近 30 个 PASS、月末 checkpoint；
- 保留任何未完成 journal/marker、当前 website predecessor、显式 recovery receipt 引用的 archive；
- 删除候选必须是完整 date directory，receipt/manifest/archive 一起处理，不能留下半快照；
- plan 记录 keep/remove 原因、路径、digest、size 和生成时刻；计划本身不可修改历史 receipt；
- dry-run 可由每日任务生成，但自动生产任务暂不执行删除。

首次 apply 是破坏性生产操作，必须在 Codex 验收实现和固定 fixture 后，由操作者审阅明确删除清单并单独批准。DeepSeek 不得执行。

## 13. PC08：文档与公开表述

DeepSeek 实施时同步更新：

- `quant/website/methodology.html`
- `quant/docs/README.md`
- `quant/docs/04-mvp-and-website/HANDOVER-website.md`
- 本文的实施状态附记或单独的 postclose implementation report
- 将 `2026-11-09-evening-publication-schedule.md` 标为历史版本，并链接新实现报告

必须准确写：

- 18:00 主跑、23:00 当晚只读检查、23:15 同入口重试、次日 07:00 最终只读检查；
- T 数据生成 U 榜单，T NAV 在 T 收盘后更新；
- 公开榜单与账户 ranking 合同 v2；
- 收益是因果纸面模拟，非券商成交认证；
- Modal 邮件是外部配置，仓库不声称自行发送邮件；
- 本地验证、提交、部署、真实调度和生产验收是不同状态。

## 14. 实施顺序

1. 保护工作区，逐项登记现有 2/8 与 4/12 diff；不修改业务代码前保存 `git diff --stat` 和测试基线。
2. 为 PC01–PC08 先写最小失败测试，记录确实命中旧行为；不得只写实现后天然通过的断言。
3. 先完成 PC02 模块拆分和 identity v3，使后续 orchestration 修改不会继续扩大 cache identity。
4. 完成 PC03、PC05：删除预算阻断并把 website `/data` 只读化。
5. 完成 PC04 ranking contract v2；用固定输入证明账户路径不变、公开 CSV 变化可解释。
6. 完成 PC01 收盘后调度与 marker schema，复用唯一协调器。
7. 完成 PC06 网络健壮性、PC07 identity/retention；不执行真实删除或云任务。
8. 更新 PC08 文档和实施状态；运行格式、定向、完整和前端测试。
9. DeepSeek 只交付本地 diff、测试日志摘要和逐项状态，不部署、不 push，等待 Codex 独立验收。

## 15. 本地验收矩阵

最低要求：

| 类别 | 必须证明 |
|---|---|
| 调度 | 18:00/23:00/23:15/07:00；无 06:00/07:30 heavy path；周末/长假正确 |
| 时序 | T close score → U open；T NAV 不使用 U 信息；标签成熟/purge 不变 |
| 身份 | 资源/schedule 改动只改 release；业务改动改 semantic/account；漏依赖失败 |
| 网站 | full、incremental、lost-response recovery；ledger/series/provider identity 不放宽 |
| 预算 | 无 `SKIPPED_BUDGET`；高费用和 billing API 故障都不阻断；fresh-fit gate 保留 |
| 榜单 | CSV 与账户 ranking 前 K 一致；不补位；历史 v1 可读；账户/NAV 不变 |
| 权限 | 所有 website `/data` mount 只读，`/simulation` 可写 |
| 网络 | Session/Retry-After/jitter/token bucket/deadline/metrics 的确定性 mock |
| 存储 | retention plan 保留所有 reachable/checkpoint；未获批准绝不删除 |
| 来源 | production 仍仅 overlay；研究默认 chenditc lineage 未被误改 |

命令从仓库根目录执行，至少包括：

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
  quant/tests/test_pipeline_resilience.py

.venv/bin/pytest -q quant/tests
black quant/code quant/tests -l 120 --check --diff --exclude qlib/_version.py
git diff --check
```

如果 `test_website_budget.py` 随预算模块删除，应以新的“无业务预算阻断/仅 telemetry”测试替代，并在报告中说明，不得简单减少覆盖。

前端相关 `.cjs` 测试也须执行，至少覆盖榜单 contract v2、历史 v1、方法说明和 CSV 安全解析。

## 16. Codex 独立验收关口

Codex 不以“测试全绿”作为唯一依据，将独立检查：

1. intended diff 是否保留此前 R01–R10、S01–S05、复权边界和资源候选修复；
2. fixed fixture 下 old/new score、ranking、paper trades、费用、持仓、cash、NAV；
3. PC04 只改变公开选择合同，不改变账户策略；
4. identity 负控是否真的区分 orchestration 与业务语义，是否存在漏哈希 import；
5. 取消 USD gate 后是否误删 fresh-fit、timeout、三市场原子性或数据 gate；
6. 23:00 和 07:00 checker 是否真正无 secret/只读/无 heavy call，23:15 是否只转发唯一协调器；
7. website `/data` 是否所有入口都只读；
8. retention 是否仅生成计划、没有隐藏自动删除；
9. 文档是否准确区分本地、提交、部署、自然调度和生产验收。

验收通过前状态只能写 `LOCAL IMPLEMENTED / NOT DEPLOYED`。

## 17. 后续云端验收（不属于 DeepSeek 本地实施授权）

只有 Codex 本地验收通过且用户另行授权后，才集中进行一次发布与验证：

1. 检查 intended diff、提交 SHA 和两个 App 使用同一 release identity。
2. 前台 attached 部署，禁止 `modal run --detach`、nohup、shell 后台。
3. 回读 function info：Provider 2/8、Website incremental 4/12、full 8/16、Signal 8/16；cron 18:00/23:00/23:15/07:00。
4. 新 identity schema 首轮执行预期为有界 full，保留调用、日志、费用和逐日 exact comparison。
5. 下一新交易日必须验证 4/12 `INCREMENTAL`，证明 release 改变但 semantic 未变时祖先可复用。
6. 用真实新日期验证 2-core Provider wall time、OOM 余量、Sina 429/重试指标。
7. 触发一个隔离的通知测试或核对受控失败邮件实际到达；不为通知测试写生产状态。
8. 自然 18:00、23:00、23:15、次日 07:00 四个时点完成后，才可写“生产调度验收通过”。

如果首轮 full、2/8 Provider 或邮件验证失败，保留不可变失败证据，恢复上一已知部署版本；不得 reset/重写账户、journal 或历史 artifacts。

## 18. DeepSeek 最终交付物

- PC01–PC08、D01–D02 状态表，逐项列出代码、测试和剩余外部门。
- 全部修改/新增文件清单；说明如何保护接手前 6 个未提交文件。
- 测试先失败、实现后通过的定向证据；完整 `quant/tests`、前端测试、Black 和 `git diff --check`。
- 固定输入下 score/ranking/成交/费用/持仓/cash/NAV 比较。
- identity 正负控矩阵和首次 full/后续 incremental 迁移说明。
- 明确写：未部署、未写生产 Volume、未执行归档删除、未完成自然调度验收。

## 19. 可直接交给 DeepSeek 的任务文本

```text
请先阅读当前 AGENTS.md，以及：
quant/docs/07-code-review/2026-11-11-postclose-production-remediation-handoff.md
quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md
quant/docs/07-code-review/2026-11-11-overlay-primary-review-remediation.md
quant/docs/07-code-review/2026-11-11-overlay-primary-second-review-handoff.md
quant/docs/09-data-source/02-production-vs-research-data-lineage.md

按 postclose handoff 实施 PC01–PC08。当前 HEAD 和工作区可能继续变化，先实时核对并保护已有
Provider 2/8、Website incremental 4/12、R01–R10、S01–S05 及复权修复；禁止 reset/restore/clean，
不要只在纯 HEAD 上开发。

先写失败回归测试，再实现。核心目标：
1. T 18:00 用 T 数据更新 T NAV、重训并发布下一交易日 U 榜单；23:00 当晚只读预警；23:15 同入口幂等恢复；U 07:00 最终只读检查。
2. 拆分 release/website-semantic/account identities；资源和 schedule 变化不再使网站失去增量祖先，业务语义变化仍 fail-closed。
3. 删除程序内 USD 预算阻断和 SKIPPED_BUDGET，保留费用观测及所有数据/工作量 gate。
4. v2 公开 CSV 等于 ranking_full 前 TopK，不因 T-close 涨跌停过滤补位；历史 v1 不重写，账户/NAV 不变。
5. website `/data` 全部只读；Sina 保持单容器 8 workers，补 Session、Retry-After、jitter、限速指标。
6. 生产代码身份不可 unavailable；只实现归档保留计划，不执行生产删除。

保持唯一 overlay provider、两个生产 App、三市场和账户/费用/执行合同，不恢复 chenditc 主备，不做
容器合并、镜像瘦身、多容器抓取，不放宽 gate。网站收益继续标为因果纸面模拟；不新增通知机制。

本轮只做本地实现和验证，不部署、不 push、不运行付费 Modal、不写生产 Volume。完成后提交 intended diff、
测试证据和状态报告，由 Codex 独立验收。
```
