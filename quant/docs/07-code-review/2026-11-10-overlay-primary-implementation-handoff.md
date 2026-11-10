# 唯一自建行情管道：完整实施方案与新会话交接

> 日期：2026-11-10  
> 状态：**用户已完整批准本方案；待实施**。本文是实施合同和交接，不是已完成报告。  
> 交接代码基线：`83f710a76`。写本文时 `main` 与 `origin/main` 同步，原工作树干净。  
> 本文不修改生产代码、调度、数据或部署。新会话必须重新确认 Git、部署版本、活动任务和最新发布状态。

## 1. 用户决定与目标

用户最终决定：

> 暂不付费，把现有 overlay 当正式自建数据管道优化，而不是假设它是廉价备用。

用户已同意本文方案，并计划在新对话中由 DeepSeek 实施。

目标是：**单一自建日更数据管道 + 冻结历史底座 + 日常增量信号/收益计算**。

重点优化：

1. 采集重复请求和网络等待。
2. 失败后从已完成部分恢复。
3. 共享 Volume 上大量小文件读写。
4. 不必要的历史特征计算和缓存失效。
5. 调度重叠和重复执行。

要求：稳定优先、适度提速、降低 Modal 成本、避免过度设计。保留现有两个生产 App 和市场计算隔离；不做容器合并，不在本轮做镜像瘦身，不为每只股票拆远程任务，不引入数据库、分布式任务队列或 Volume 伪锁。

### 1.1 历史决策已被本方案取代

此前曾做过：

- 网站增量回放、资源从 20 CPU / 32 GiB 降到 8 CPU / 16 GiB。
- chenditc 主源 + overlay 备用源，共享 chenditc 下载缓存。
- 06:00 主跑、07:30 串行重试。
- 方案 A：chenditc/overlay 双向切换时网站全量重建，同源才增量。

**现在不再以日常主备切换为目标。** 生产每天从冻结的自建快照追加行情；chenditc 自动检查、下载、选择与切源逻辑退出日常关键路径。历史锚点可以保留其 chenditc 来源，不需要重新爬取全部历史。历史研究和恢复工具不应盲目删除。

### 1.2 不变的业务合同

- CSI300、CSI500、CSI1000 三个独立 1M 账户。
- 当前 canonical 模型、TopK/DropN、费用、板块规则、标签成熟度、purge、20-session 重训规则保持。
- CSI300 / CSI1000：Top10/Drop1；CSI500：Top20/Drop2。
- T 收盘特征形成信号，T+1 开盘执行。早间发布时使用上一交易日收盘数据，不能引入当日未来数据。
- 网站历史模拟账户与实时 paper 账户是不同的证据路径，不能为了省计算强行合并模型、预测或账户。
- ChiNext 仍暂停生产；STAR 的研究状态不在本轮改变。
- publication journal、历史信号、paper 状态与既有审计证据不可随意覆盖。

## 2. 基线、部署与入口

### 2.1 最后确认的状态

| 项目 | 交接时已知状态 |
|---|---|
| Git HEAD | `83f710a76b3c9356f0176f46bed78af06dc5428c` |
| daily App | `qlib-prod-daily`，最后确认 v15 @ `83f710a76` |
| website App | `qlib-prod-website`，最后确认 v15 @ `83f710a76` |
| 现有 cron | 06:00 `daily_cron`；07:30 `daily_preopen_retry`，北京时间工作日 |
| 旧晚间入口 | `daily_evening` / `daily_evening_retry` 已无 schedule，仍可手动调用 |
| 当前数据选择 | chenditc 缓存优先，失败/陈旧时 overlay；这是本轮要替换的行为 |
| 已完成生产周期 | 11/10 晚间在 20:06 完成，三市场 website 使用增量 |
| 最近公开数据提交 | `d2df363b2` 发布 11/11 信号；`b351a41c7` 更新至 11/10 NAV |
| chenditc 缓存 | 11/10 release 曾成功缓存，calendar_end=2026-11-10；不能误认成自建数据锚点 |
| 预算 | 最后确认 website 策略为 daily_cap=10、monthly_credit=40、reserve=2 美元；日 cap 是临时值，实施前复核 |

以上不是新会话的实时查询结果。之前注册过次日早间只读日志采集任务，不应假设它仍在运行或会自动完成新版本验收。

### 2.2 关键提交

- `79855c0bc`：网站单日增量回放、8 CPU / 16 GiB。
- `86d5f6ced`：overlay base 日期关系修复；此前生产 v10。
- `4c317a246`：chenditc 缓存和 06:00 主跑。
- `09694110b`：preopen 完成 marker / 07:30 跳过。
- `d88f17b5f`：修复 manifest 的 PASS 覆盖函数 READY 返回状态。
- `fc3d78a86`：preopen marker 只保留可序列化阶段状态。
- `83f710a76`：website payload 记录 provider_source；跨源拒绝增量。

### 2.3 阅读入口

先读仓库 `AGENTS.md`，以及：

- `quant/docs/00-strategy-handbook/STRATEGY_ATLAS.md`
- `quant/docs/05-round2-layered-search/60-csi1000-two-round-strategy-research-final-report-20261015.md`
- `quant/docs/04-mvp-and-website/` 中相关账户、网站与发布合同。

重点代码：

| 文件 | 内容 |
|---|---|
| `quant/code/production/modal_qlib_cn_a10g.py` | 镜像、App、ensure_overlay、信号、cron、发布路径 |
| `quant/code/data_ingest/sina.py` | Sina raw/hfq 获取 |
| `quant/code/data_ingest/baostock_source.py` | 串行 BaoStock 会话及回退 |
| `quant/code/data_ingest/csindex.py` | 官方成分/权重及超时重试 |
| `quant/code/data_ingest/pipeline.py` | 采集、调整、构建、gate 组织 |
| `quant/code/data_ingest/overlay.py` | 完整 provider 复制、bin 追加、成分区间 |
| `quant/code/data_ingest/adjust.py` | 复权与行情转换合同 |
| `quant/code/data_ingest/gate.py` | 日历、覆盖、行情、公司行为、交叉校验、qlib smoke |
| `quant/code/ops/website_simulation_modal.py` | website smoke/full/incremental、模型缓存与发布 |
| `quant/code/research/regime_reselection/prelive_revalidation.py` | prepare_data、replay、prior state 恢复、ledger |
| `quant/code/production/website_simulation.py` | 连续账户 series、receipt 和校验 |
| `quant/code/production/paper_portfolio.py` | 实际账户状态机 |
| `quant/code/production/publication_journal.py` | 幂等发布与账户提交 |
| `quant/code/production/daily_pipeline.py` | 两阶段恢复与 timeout 合同 |
| `quant/code/execution/qlib_live_retrain.py` | 重训节律、模型缓存、provider 历史指纹 |
| `quant/code/execution/research_identity.py` | 全量 extension source manifest |
| `quant/code/production/provider_cache.py` | 当前 chenditc 缓存，准备退出日常路径 |
| `quant/code/execution/st_history.py` | ST 证据与日期语义；同时检查生产入口的抓取时间窗口 |
| `quant/code/ops/website_budget.py` | website 门禁，不是全工作区硬消费上限 |

## 3. 历史证据：哪些可信，哪些不能沿用

### 3.1 可作为参考的测量

- 网站 8/16 全量曾成功：CSI300 约 170 秒、CSI500 452 秒、CSI1000 881 秒；另一轮 CSI1000 约 1,267 秒。
- 网站 CSI1000 隔离增量约 211 秒，其中交易 replay 接近瞬间，主要耗时在特征加载和输入读取。
- 11/10 实际生产增量：CSI300 约 124 秒、CSI500 160 秒、CSI1000 222 秒。
- overlay 探针主进程峰值 RSS 约 986 MiB。
- CSI1000 warm 信号探针约 139 秒，主进程峰值 RSS 约 8,730 MiB。
- 既有记录还包括信号重训日约 9.1 GiB RSS；网站历史全量研究曾记录更高内存，不能把推理内存当全量上限。
- 最近全量 Python 测试报告：945 passed、1 skipped、50 subtests；新会话以实际运行结果为准。

### 3.2 必须修正的旧推论

1. **不能认为补一天就只下载一天。** 本地 AkShare `stock_zh_a_daily` 先下载历史行情、JS 解码和合并，再按 start/end 截取。raw/hfq 分别调用会重复下载和解码。还存在流通股本请求。云端版本必须核实、固定。
2. **不能把 47–64 分钟当稳定 SLA。** 旧测量混有超时、重启、手动探针与生产并发访问同一输出目录。阶段归因不完整。
3. **不能说 qlib smoke 本身用了 35 分钟。** 当前 smoke 仅检查目标日日历和少量股票 close；长耗时可能在小文件复制、读写或恢复。
4. **不能凭 1GB RSS 就承诺 2 CPU / 4GiB 足够。** CPU/cgroup 采样失败，缺少完整子进程统计。null/0 是无效指标，不是零消耗。
5. **不能声称 06:00 必定赶上开盘。** 免费接口有尾延迟，多日补数与重训仍需实测和失败边界。
6. **不能把前一天预测相等当作完整历史一致。** 行情、复权、成分和持仓估值也影响账户。
7. **不能把重新全量等同于历史 NAV 不变。** 数据修订下全量会改历史路径，必须版本化、解释原因。
8. 此前 chenditc 14.16% 是 release 标签/发布时间代理统计，不是下载验证的历史实际不可用率。不要作为确定的供应商 SLA。
9. 此前 15:33 测试在 Sina 完成后卡住，并不能仅凭日志断言必然是“运行太早导致大规模回退”；没有完整采样不能下定论。

### 3.3 特别需要避免的操作事故

- 曾漏设部署 selector，误部署到 legacy app。新部署必须先检查预期 app/volume 名称。
- 临时脚本曾只加入本机路径，云端 import 失败并反复启动。先验证容器 bootstrap，不能反复付费重试猜路径。
- 临时探针曾 `.local()` 调用生产逻辑、修改 membership 获取函数并写生产 Volume，且与 cron 重叠。**以后禁止以此方式测资源。** 切换前须审核候选锚点的来源和证据，不因 manifest 写了 PASS 就忽略这段历史。
- 两个不同函数的 `max_containers=1` 不构成互斥；另一个 App 的 `.local()` 更不受该并发限制。

## 4. 目标数据架构

```text
冻结历史 provider（保留来源和哈希）
    ↓
每日采集：Sina + 必要 BaoStock 校验 + 指数成分信息
    ↓
本地构建、校验、文件清单
    ↓
不可变当日 PASS provider（唯一生产版本）
    ├─ 三市场信号 → paper/journal → 选股发布
    └─ 三市场网站账户增量 → 收益发布
```

一个自建 provider 管道仍依赖多个上游接口，这是行情和验证职责的组合，不是日常切换两套完整 provider。

保留旧 `overlay` 路径名称也可以；第一轮不为命名做目录迁移。

### 4.1 数据覆盖与正确性

每天获取的股票集合至少包括：

```text
当前三市场成分
∪ paper 持仓和待执行订单涉及股票
∪ website 账户持仓
∪ 必要基准
```

退指股票尚未卖出时仍需报价。这是本仓库历史审计已发现的问题，不能只采当前成分。

新成分缺少历史特征时，需要受控补齐足够历史；不得伪造特征或静默排除。复权坐标、成交量/金额单位、VWAP、日期敏感涨跌停和上市日规则必须保持一致。

## 5. Modal App 与任务划分

### 5.1 保留两个生产 App

| App | 职责 |
|---|---|
| `qlib-prod-daily` | 唯一调度入口、数据构建、三市场信号、paper/journal 发布 |
| `qlib-prod-website` | website 模拟账户计算与收益发布，由 daily 调用，无独立 cron |

研究和资源测试使用独立临时 App、隔离输出路径，不复制完整生产发布能力到测试脚本。

### 5.2 函数与并发

| 任务 | 运行方式 | 并发 |
|---|---|---|
| 主协调器 | 顺序调用阶段并核验 receipt | 同一函数 max_containers=1 |
| 采集＋provider 构建 | 一个 worker，内部有界线程池，本地构建 | 1 个容器 |
| 信号计算 | 保留三市场顺序调用现有 worker | 1 个重容器 |
| website 计算 | 保留三市场并行 | 最多 3 个重容器 |
| 07:30 重试入口 | 转发到主协调器，不直接执行重活 | 主入口串行排队 |
| 09:00 检查 | 只验证状态、日期和 receipt，通知异常 | 轻量短任务 |

正常数据阶段、信号阶段约为协调器＋一个 worker；网站阶段可为主协调器＋website 协调器＋三个 worker。容器数不是唯一成本指标，需看资源和实际计费时长。

采集与构建先同容器，共享 scratch 数据。只有实测证明拆分收益明确，才考虑后续分离。

### 5.3 唯一写入入口

- 所有自动和手动生产恢复经同一个主协调器串行运行。
- 已失去 schedule 的晚间/manual 入口也应审查，避免仍可绕过主入口并发写入。
- 不引入基于 Volume 文件的 lease/CAS 伪锁。
- 测试、资源探针只读生产输入，写隔离输出，不调用生产发布或改生产 journal。
- GitHub 发布凭据只在发布职责容器使用；资源测试不需要发布权限。

## 6. 资源配置与测量

以下为**候选实施起点**，不是实测通过结论：

| 任务 | 初始配置 | 后续候选 | 抢占策略 |
|---|---|---|---|
| 主协调器 | 1 CPU / 2 GiB | 按实际内存确认 | 保持非抢占 |
| 采集＋构建 | 4 CPU / 8 GiB | 验证后考虑 2 CPU / 4 GiB | 普通容器＋断点恢复 |
| 信号 worker | 8 CPU / 16 GiB | 第一轮保持 | 保持非抢占 |
| website 协调器 | 1 CPU / 2 GiB | 第一轮保持 | 保持现状 |
| website market | 8 CPU / 16 GiB | 增量、全量、重训都测后再评估 | 保持现状 |

不要同时改资源、抢占和模型线程数。尤其 canonical LightGBM 线程配置可能参与模型身份，不能当纯资源参数随意修改。

### 6.1 必须采集的指标

- 阶段 wall time：启动、下载、解析、复制、追加、gate、训练、预测、replay、上传、发布。
- CPU seconds、平均使用核数；可用时记录 cgroup 限额与 throttling。
- 主进程及子进程内存；注明 RSS 汇总可能重复计算共享页。可用时采用 cgroup/PSS。
- 请求数量、响应字节、失败类型、重试、缓存命中、成功目标日覆盖。
- 文件读写/复制/打包/解包时间和数据量。
- 任务起止 UTC、source SHA、输入摘要、App/call ID、退出码、重启次数、对应账单。

不可读的指标标为 unavailable。失败路径也必须保存指标；现有临时 sampler 在异常时可能没有返回 summary，需修复。

收费依据采用当期 Modal 官方规则和实际账单。区分 wall time、CPU 使用时间、资源请求、实际峰值以及排队/启动/恢复，不从平均 CPU 利用率直接推算账单。

### 6.2 成本验收

普通日、重训日、恢复日分别统计；生产 cron 与实验/预热分开。保留 App 账单和函数阶段计时，账单只有 App 粒度时不伪称精确函数费用。

第一轮不承诺绝对日成本。用冻结的相同数据输入验证结果，然后用实测的资源和账单验证节省。降低重抓与全历史计算优先于移除信号非抢占溢价。

## 7. 采集优化

### 7.1 raw/hfq 共用获取与解析

在 `sina.py` 内实现小范围适配器：

```text
每只股票：一次获取历史行情＋一次获取复权因子
    → 同时生成所需 raw/hfq 结果
    → 截取目标区间
```

检查并固定 AkShare/解析依赖版本。字段依赖确认后才移除流通股本等非必要请求。不能未经验证改掉价格四舍五入、单位、缺失日期、复权或停牌语义。

不直接修改 upstream `qlib/`，不以修改 site-packages 作为交付。

### 7.2 温和增加并发

- 先 6→8，效果好再测 10–12。
- 每请求连接/读取超时、有限重试和退避。
- 限流或错误率升高时降低压力。
- 每批有总体耗时边界。
- 以整批成功耗时和完整率衡量，而非只看瞬时吞吐。

### 7.3 原始结果批次缓存

按目标交易日和采集合同版本保存行情、因子、成分证据、receipt、失败清单。使用少量批次文件即可，不做每股票一个远程任务。

重试时：

- 已完整且日期/身份一致的结果复用。
- 缺失、失败、未收盘完整的数据重拉。
- 不把“空成功响应”永久缓存为完整数据。
- 不因一次失败丢弃全部已完成采集。
- 批次持久化频率要兼顾恢复粒度与 Volume commit 成本。

### 7.4 BaoStock 有界串行

- 保留一个串行会话，避免未经证实的多线程会话安全问题。
- 单查询和整批都有 deadline。
- 底层阻塞无法可靠取消时，用一个可终止子进程隔离，不增加远程 App 层级。
- 持仓缺失、公司行为、必需交叉校验优先。
- 大面积缺数判为 provider 不就绪，停止当前批次；不要直接串行回退全市场。
- 不能为节省时间降低必要的停牌/公司行为验证。

### 7.5 成分获取与证据

- 同一次运行已下载文件复用；日期滞后后不要再下载整套文件。
- 保留官方文件真实日期、哈希和来源。
- 上一份成分延用应有明确有效期/政策，调整生效窗口尤其严格。
- 网络异常不等于可以把旧 membership 标成当日官方事实。
- 多日补数无法取得历史成分证据时停止自动推进，不能把当前成分应用于所有缺失日期。

## 8. provider 存储、构建和恢复

### 8.1 本地构建＋不可变发布

1. 从明确绑定的上一 PASS 快照读取。
2. 在容器本地 scratch 解包、追加。
3. 本地完成 gate 和 qlib smoke。
4. 生成内容清单和哈希。
5. 上传到唯一 staging 路径。
6. 完整持久化后才发布 PASS receipt / 最新指针。

不要原地修改已经发布的 provider；不要依赖目录存在或 calendar 尾部就认为快照完整。Volume 的 rename/commit 可见性和异常恢复要按真实平台语义测试，不能假设跨文件事务。

### 8.2 归档传输试验

在隔离环境比较：

- 当前远程目录小文件复制。
- 归档读取到本地后解包，按 digest 复用本地缓存。

若归档方案明显更快，再采用。压缩算法和压缩等级按 CPU/传输实测选择，不先堆叠存储层。输出 manifest 需清楚区分 archive digest 与 provider 内容 digest。

禁止用可写硬链接把未来追加连接到历史文件，以免污染历史快照。

### 8.3 保留策略

- 永久保留冻结历史锚点。
- 保留每日原始增量、manifest、关键证据和账户账本。
- 完整快照滚动保留，加周期性检查点。
- 先验证检查点＋增量能够重建，再清理旧快照。
- 任何正在运行或尚未完成发布的输入不得被清理。

第一轮优先降低计算与等待成本，不急于清理既有 chenditc、overlay 和历史审计文件。

## 9. 信号与收益计算

### 9.1 网站增量保留并补强

正常日继续：上一日 final_state＋当前行情/预测→一个新 session。

需要补强：

1. 父快照绑定实际 digest，而非只有 `base.path/calendar_end`。
2. 验证历史价格、复权、成分和账户边界，不仅比较某一天预测。
3. 增量前置条件缺失或已确认历史修订走受控全量重建；未知异常保留原错误，不能无差别吞掉后全量。
4. `_incremental_prior` 返回 None 与增量执行中 AssertionError 是不同路径，后者目前不自动 full，需明确处理。
5. prelive `PaperPortfolio.from_dict` 的可变对象引用、完整状态校验，以及 ledger/report 反序列化后的字段/NaN 行为应纳入恢复测试。
6. prepare_data 增量读取需要覆盖已有持仓，即便股票已经不在当日评分集合中。

历史修订触发的全量重建必须版本化；不能宣称保持原历史 NAV。数据修订、代码不兼容与单纯缺少缓存要有不同原因记录。

### 9.2 信号缓存命中时减少 dataset 历史范围

当前 model cache hit 仍会创建较长历史 handler。复用短窗口预测思路，但必须：

- 审查 infer/shared processors 的训练期状态依赖。
- 保存/恢复需要的处理器状态。
- 保留足够的特征回看窗口。
- 对分数、排名和订单做精确/合同规定的数值验证。
- 重训日走原完整训练流程。

不要把 website 与实时 paper 的模型直接互换。两者训练起点和 fit receipt 可能不同。

### 9.3 缓存身份治理

当前 `research_identity.source_manifest` 把所有 `quant/code/*.py` 纳入训练身份，调度小改动也可能导致 website 全部模型/结果缓存失效。

后续可拆：

- 完整部署身份：仍用于代码审计与部署一致性。
- 计算兼容身份：覆盖模型、特征、处理器、执行、账户、依赖和实际加载的配置。

必须完整枚举间接依赖，并测试相关改动一定失效；不能直接删掉校验或随意排除模块。当前 source manifest 与实际加载 YAML 的覆盖关系也需审查。

第一轮先合并改动、一次验收部署，避免每修一行就部署并重新预热。兼容身份治理可以作为后续独立阶段。

## 10. cron 与失败边界

### 10.1 推荐时间

| 时间（北京时间工作日） | 行为 |
|---|---|
| 06:00 | 使用上一交易日数据，运行整条管道 |
| 07:30 | 已成功则核验 receipt 返回，否则进入同一个主入口排队恢复 |
| 09:00 | 只检查日期、状态、错误并告警，不启动第三轮完整计算 |

保留早间执行，让免费数据源有充分落地时间。交易日按外部可验证交易日历判断，不只按工作日；周末、节假日及时短路。

### 10.2 不重复执行

- provider 已 PASS：不重复采集。
- 信号 journal 已提交：不重算信号。
- website receipt 已发布：不重算收益。
- 只有部分采集完成：从批次缓存恢复。
- `preopen_status` 是便利索引，不是唯一真相；必须绑定日期、provider digest、相关版本和真实阶段 receipt。旧 marker 不可遮蔽失败或不同输入。

### 10.3 时间预算

初始给数据阶段约 90 分钟执行预算，并预留持久化收尾时间。worker、coordinator、排队重试的 timeout 必须联动核对，不能只增加一个 decorator 常量。

性能目标（不是保证）：普通日争取 08:00 前完成，异常恢复争取 09:00 前完成；重训日单独验收。

长历史全量初始化或严重修订应在调度前单独完成。开盘前未完成时明确显示数据日期与失败/未更新状态，不用旧信号冒充当日信号。迟到恢复涉及 paper 订单是否允许模拟执行，应遵守现有执行合同并明确记录。

### 10.4 ST 与时间语义

检查当前 ST 抓取窗口、publication gate 和 paper execution context 是否仍假设晚间 T-close 运行。早上 T+1 采到的状态不能未经区分当作 T 收盘状态；信号日、执行日、采集时间分别记录。不能因换 cron 而放宽证据要求。

## 11. 多日中断恢复

| 场景 | 处理 |
|---|---|
| 单批失败 | 仅重试缺失批次 |
| worker 被中断/抢占 | 从已持久化原始结果恢复 |
| provider 成功、信号失败 | 复用同一 provider 重试信号 |
| 信号成功、website 失败 | 保留已提交 journal，只恢复 website |
| 中断多个交易日 | 按日期补数据和必要证据，不能使用未来成分回填历史 |
| 历史成分/公司行为证据不足 | 停止自动补数，给出明确恢复要求 |
| paper 多日未运行 | 验证旧 pending 的实际执行日与后续结算，不能用网站全量替代 paper 补账 |

不要虚构缺失日期曾发布过的选股。网站历史模拟补算与当时实际发布记录必须区分。

## 12. 分阶段实施与代码落点

### 阶段 1：稳定性与可观测性

主要落点：生产入口、daily_pipeline、data_ingest、隔离 ops 测试工具。

- 审查所有生产写入口和手动入口。
- 阶段计时、进程树资源指标、失败时仍保存日志。
- 请求/任务 timeout、退出状态和持久化边界。
- preopen marker 必须核验真实 receipt。
- 明确隔离测试数据路径。

验收：失败时能定位阶段、已完成工作和有效资源测量，无测试写生产冲突。

### 阶段 2：采集与 I/O 提速

主要落点：`sina.py`、`baostock_source.py`、`csindex.py`、`pipeline.py`、`overlay.py`、`gate.py`。

- raw/hfq 请求复用、并发 6→8。
- 批次缓存、缺失重试、BaoStock deadline。
- CSIndex 单次获取复用与真实日期证据。
- 退指持仓、新成分历史覆盖。
- 本地构建和归档传输试验。

验收：固定原始输入下有效行情、复权和账户结果一致；注入中断后不重抓完整股票池；全市场缺数时及时停止。

### 阶段 3：计算与资源优化

主要落点：daily_standalone、website worker、prelive replay、模型缓存相关模块。

- 信号短窗口推理及处理器状态。
- website 父快照/历史前缀与状态校验。
- 测普通增量日、重训日、全量恢复日。
- 根据有效测量决定 overlay 4/8 或 2/4，信号/website 首轮保持 8/16。

验收：分数/排名/订单/费用/持仓/NAV 满足原合同，实际账单下降，不靠移除证据校验实现提速。

### 阶段 4：一次性生产切换

- 审核并冻结历史锚点；特别检查此前手动探针写入的快照来源。
- 日常入口改为自建 provider-only。
- chenditc 自动选择退出；保留必要的历史恢复工具和证据。
- 两个 App 使用同一最终提交部署；必要的首次初始化在 cron 前完成。
- 核对 06:00/07:30 共用单主入口，09:00 只读检查。
- 更新 AGENTS 中生产描述、运行手册、网站数据来源说明和故障恢复文档。

回滚：回滚已知有效代码与明确快照选择，不删除 journal、不回滚已发生的账户交易、不暗中恢复 chenditc-first 作为不同语义的补救。回滚版本要明确是否仍会选 chenditc，不能把 v15 当作 overlay-only 回滚点。

## 13. 测试与验收清单

### 13.1 必要场景

- 普通单日、重训日、周一/长假后。
- 股票退指但仍持有、新成分历史不足。
- 停牌、除权、复权异常、板块日期敏感规则。
- 成分文件滞后/网络失败/调整生效日。
- Sina 部分超时、全市场目标日未就绪、限流。
- BaoStock 登录/查询阻塞。
- 下载/采集/上传中断，worker 重启。
- provider 已写部分文件但未 PASS。
- 已发布信号而 website 失败。
- 主跑未完成时重试到达。
- 相同日期反复运行不重发、不改变已冻结账户状态。
- 多日缺口、历史修订、增量前缀损坏、缺失 receipt。

### 13.2 已有测试入口

- `quant/tests/test_cron_gate.py`
- `quant/tests/test_modal_structure.py`
- `quant/tests/test_daily_pipeline.py`
- `quant/tests/test_website_simulation.py`
- `quant/tests/test_prelive_revalidation.py`
- data_ingest、paper、execution 相关测试。

从仓库根目录运行 `pytest -q quant/tests`；按本地可用 Python 环境调用，若有库加载问题按环境实际配置解决，不写入开发者专属绝对路径。遵守 AGENTS 的构建前提和 lint 入口。格式为 Black 120 列。修改网站文案/契约时运行相关 Node 测试。

不启动常驻生产 shadow。采用固定历史输入的有界隔离验证，以及最终上线后的真实运行验收。

### 13.3 完成定义

不能用“部署成功”或“945 个测试通过”代替目标完成。必须证明：

1. 正常日只补新行情，账户只推进新 session。
2. 网络慢时有明确失败边界，已采集成果可恢复。
3. 重试不产生第二套并行生产写入。
4. 普通/重训/恢复日均有有效资源记录。
5. 三市场与 website 绑定明确一致的当日 provider。
6. 费用可分辨生产和实验，并能解释主要阶段。
7. 缺数据时明确显示未更新，不冒充成功。

## 14. Modal 操作纪律

- 使用已授权的本地 Modal profile；本文不记录私人 workspace/profile 名称或凭据。
- 当前生产模块通过 `QLIB_DEPLOY_PROFILE` 环境变量选择 App/Volume。部署前检查代码 selector，显式选中生产配置，确认解析出的名称为上述 `qlib-prod-*`，避免默认 legacy 分支。selector 不是授权 profile，两者不要混淆。
- `modal run` 测试必须保持前台 attached；**禁止 `modal run --detach`**。
- 启动前预算镜像构建、排队、所有 worker、收尾和结果取回时间；工具 timeout 必须覆盖最坏合理耗时。不能覆盖就停止并请求合适的前台执行方式或有界批次。
- 使用 `set -o pipefail` 和 `tee` 保存真实输出，确认实际退出码；不要用 `tail` 掩盖失败。
- 超时/连接中断不等于任务失败或成功，先查 App 和不可变 artifacts，再决定恢复；不能盲目重复启动。
- 不用 `nohup`、shell `&` 或无跟踪后台任务保护研究实验。
- 不自动删除旧 secret、生产快照、journal 或模型缓存。此前用户明确要求保留 legacy github-push secret。
- 文档、日志、fixtures 不提交访问凭据、私人工作区/profile 名称、个人文件系统路径。
- 切换应避开活动生产任务；先验证所有改动，再集中部署，避免不断失效 source cache。

## 15. 新对话可直接使用的启动指令

```text
请阅读 AGENTS.md 和以下已由用户完整批准的实施方案：
quant/docs/07-code-review/2026-11-10-overlay-primary-implementation-handoff.md

请按文档执行“唯一自建行情管道”优化。重点是可靠采集、断点恢复、
本地构建/减少 Volume 小文件 I/O、保留增量账户引擎、有效资源采样，
以及 06:00 主跑/07:30 串行恢复/09:00 只读检查。

先核对当前 Git、部署和活动任务，保护新增用户工作与生产 journal。
交接基线为 83f710a76，最后确认生产 v15；这只是历史状态，请重新确认。
现有 chenditc-first 逻辑是待替换行为，不是继续完善的方向。
不要做容器合并或镜像瘦身，不要新增双源账户 lineage，不要跳过 gate。

按阶段实施并提交，每阶段记录真实验证结果。需要云端实测时使用隔离输出、
有界前台执行和足够的工具 timeout；严禁 modal run --detach，不能让探针
绕过单写入口修改生产目录。先完成本地/隔离验收，再集中部署，
不要只凭部署 acknowledgement 或单元测试数量宣布生产稳定。

请对文档中标为“候选配置”“待测量”的内容进行验证，不要当成已证明结论。
发现阻碍或与当前代码不一致时明确说明并据证据调整，不悄悄改业务语义。
```

## 16. 交付物

- 代码与必要回归测试。
- 数据/账户合同兼容性证据。
- 普通、重训、恢复场景的资源与成本报告。
- 最终资源、timeout、cron 和并发配置表。
- 部署提交、App 版本、输入快照摘要和首次生产验收记录。
- 明确的故障恢复、缓存保留与回滚说明。

最终判断标准：**先让单管道请求更少、失败可恢复、文件读写更快、账户只算新增，再有证据地下调资源。**
