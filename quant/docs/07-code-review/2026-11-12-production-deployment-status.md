# 2026-11-12 生产部署与文档状态

> **19:13 最新结论（晚于本文原核对窗口）：** ST 选择性简化已以
> `31b65fa8a` 提交并推送；Website / Daily 两个生产 App 已部署 v28。18:00 的 v27
> 自然调度因中证指数 OSS 读取超时失败，18:10 的旧版恢复父调用又在 Website 前被取消；
> v28 前台 attached 补跑于 19:00:40 成功结束，生产 App 的独立只读检查随后返回
> `status=OK`。三市场 2026-11-13 信号、纸面账户、Website FULL 产物和 postclose marker
> 已关联至同一 2026-11-12 Provider archive `af7d92b8…`。Git 远端最终因生产产物提交前进到
> `caf7f8190`。本文原 v26 表格保留为 10:06–10:16 的日期化历史快照。

> 核对时间：2026-11-12 10:06–10:16，Asia/Shanghai。
>
> 原始核对窗口性质：只读部署配置、Git 与补跑日志核对；不代表 v26 自然调度、策略晋升或券商成交验收。

## 1. 提交与部署

Git `main` 和远端 `main` 在核对时均指向 `1b5ef71ac502cf106574518628864221139abdd5`。该提交收紧 website publication receipt 的 release map 和 series 身份校验，并补齐跨 release 恢复与 Volume reload 顺序测试。此前的补跑修复包括 `81678cc4a`（逐市场 smoke）、`7021c9f7a`（按市场绑定 release）、`ee6e6ce4e`（发布前 reload）。

| App | 已部署版本 | 平台记录的提交 | 部署时间（Asia/Shanghai） |
|---|---|---|---|
| `qlib-prod-website` | v26 | `1b5ef71` | 2026-11-12 09:12:21 |
| `qlib-prod-daily` | v26 | `1b5ef71` | 2026-11-12 09:13:09 |

以上来自 Modal `app history` / `app info` 的只读回读。Website 先部署、Daily 后部署；两个 App 使用同一提交和镜像。Git 后续如仅增加文档提交，不意味着生产代码已更新或必须重新部署；应分别核对部署提交和代码身份。

## 2. 已回读的生产配置

时区均为 Asia/Shanghai，周一至周五触发；代码使用交易日历判断实际交易日及 T/U。

| 时间 | 已部署入口 | 作用 |
|---|---|---|
| T 18:00 | `daily_cron` | 唯一协调器：T provider、U 信号/纸面账户、T 网站 NAV |
| T 23:00 | `daily_postclose_check` | 只读核对当晚结果，失败时由平台通知配置告警 |
| T 23:15 | `daily_evening_retry` | 必要时通过同一 `daily_cron` 排队恢复 |
| U 07:00 | `daily_readonly_check` | 下一交易日只读最终核验，不启动重算 |

Website App 无 cron。Daily `daily_cron` 的 `max_containers=1`。Modal `function info` 回读：

| 函数用途 | CPU | 内存 | 单次 timeout |
|---|---:|---:|---:|
| Provider `ensure_overlay` | 2 | 8 GiB | 90 分钟 |
| Signal `daily_standalone` | 8 | 16 GiB | 90 分钟 |
| Website full/smoke/retrain `build_market` | 8 | 16 GiB | 90 分钟 |
| Website incremental `build_market_incremental` | 4 | 12 GiB | 90 分钟 |

生产日常路径继续使用唯一自建 overlay archive，缺失时不回退到 chenditc；网站 `/data` 为只读挂载。三市场、独立账户及执行规则未因本次部署改变。研究默认数据来源与生产不同，详见[数据 lineage](../09-data-source/02-production-vs-research-data-lineage.md)。

## 3. 11 月 11 日补跑证据

- Modal Provider 日志显示：11 月 12 日 04:32，`2026-11-11` archive 为 `PASS`，digest 前缀 `1f0fc68efb017277`。此前一次构建在历史前缀 gate 失败，修复后重试复用了已缓存的 Sina/BaoStock 批次；这不是从空缓存开始的一次完整构建耗时测量。
- Git 提交 `fd55cb3fd` 发布 11 月 12 日三市场信号文件；三份 metadata 记录 `signal_data_date=2026-11-11`、`usage_date=2026-11-12`、`lag_sessions=0`、`PASS`。
- Git 提交 `70dd0a250` 更新三市场网站模拟账户至 11 月 11 日。2026-11-12 05:31 的 Daily 日志记录 signal、website 阶段成功并写入 postclose marker。这次手动恢复发生在 v25；不应当成 v26 自然调度验收。
- `1b5ef71ac` 修复后的本地验证：`quant/tests` 为 `1265 passed, 2 skipped, 50 subtests passed`；website 前端 Node 测试通过。测试和旧版补跑不替代 v26 生产运行验收。

## 4. 仍需验证的边界

- v28 的前台手工恢复和独立只读检查已通过，但不能替代后续自然调度验收。
  23:00/23:15 自然检查与恢复、下一交易日 07:00 检查、失败邮件实际送达，以及下一次
  v28 自然 18:00 主跑仍是独立边界。23:00 若主跑尚在运行，应区分运行中与失败；
  23:15 的恢复调用会在同一协调器后排队。
- ST 选择性简化改变了 Website semantic identity，因此本次新 cache root 按设计执行三市场
  smoke + FULL，而不是复用旧增量祖先。下一个新交易日仍需验证 4 CPU / 12 GiB
  incremental 实际命中、耗时与费用。
- 2/8 Provider 的冷缓存完整新日期构建、全量 replay 与前版的逐日 score/ranking/成交/费用/持仓/cash/NAV 对照，以及公网静态站点更新，尚无本次 v28 完整验收证据。
- 研究 AR01–AR09 整改已随 `80014b09f` 提交并推送，其源码包含在后续生产镜像中；这不表示真实三市场研究 bounded replay、策略 `GATE_PASS` 或独立生产认证已完成。CSI300/CSI500 仍为风险接受 override，CSI1000 为 incumbent retained。
- 网站收益是因果纸面模拟参考值，不是券商成交、完整历史 ST、开盘竞价流动性或官方参考价认证。归档清理仍仅允许 dry-run，不能因本文推断已授权删除。

## 5. v28 选择性简化与 11 月 12 日补跑验收

### 5.1 实施范围

- 保留离线/历史研究的按日 ST 验证能力，不再把 Website 历史 ST 数据作为日常生产强依赖。
- 保留调度纸面账户当日/盘前 ST 执行风险校验；它与 Website 历史重放不是同一合同。
- Website 显式发布 `ST_HISTORY_UNVERIFIED_REFERENCE_ONLY` 和
  `CAUSAL_HISTORICAL_SIMULATION_REFERENCE_ONLY`，不宣称券商成交或完整历史 ST 认证。
- 源码提交 `31b65fa8ac722bdaa975eadde604f181621314af`；本地完整验证为
  `1268 passed, 2 skipped, 50 subtests passed`，Website Node 前端测试 5 个文件全部通过，
  Black check 和 `git diff --check` 通过。

### 5.2 部署与补跑证据

| App | 部署版本 | 部署时间（Asia/Shanghai） | 源码身份 |
|---|---:|---|---|
| `qlib-prod-website` | v28 | 2026-11-12 18:39:37 | `89d44e2d94a9…` |
| `qlib-prod-daily` | v28 | 2026-11-12 18:41:46 | `89d44e2d94a9…` |

- 补跑以前台 attached 方式执行，未使用 `modal run --detach`。Provider 复用已验证的
  `2026-11-12` archive，SHA-256 为
  `af7d92b8fc10b77e7ae31501871542724554390c89a0e09622328271fe98bf31`，calendar end 为 11 月 12 日。
- 三市场信号 metadata 均记录 `publication.status=PASS`、`lag_sessions=0`、
  `signal_data_date=2026-11-12`、`usage_date=2026-11-13`、PIT universe PASS 和 label maturity PASS。
  发布 journal Git commit 为 `f37a520ff83a009994f6bc965b1d075c66d55865`。
- Website 因 semantic identity 改变执行三市场 smoke + FULL；每个市场生成 26 个交易日点，
  `latest_date=2026-11-12`，源码身份 `89d44e2d94a9…`，semantic 身份
  `859e324df8fa…`，Provider 身份与上述 archive 一致。
- 生产 App 的独立只读 `daily_postclose_check` 返回 `status=OK`，T/U 分别为
  `2026-11-12` / `2026-11-13`，并回读到同一 provider digest、journal commit、release source、
  website semantic 和三市场 account contract。
- 生产产物自动提交为 `f37a520ff`（信号）、`f2b6ffefb`（名称映射）、
  `caf7f8190`（三市场收益与 Website FULL 产物）；已快进本地分支并通过文件结构/身份校验。

### 5.3 验收边界与运维注意

- 本次的生产输入为只读 Provider archive，输出隔离在发布产物、纸面账户和 Website
  simulation 路径；未进行真实成交。
- 一次验收性临时 App 因部署 selector 配置错误指向了 legacy Volume，其“marker 缺失”
  结果已作废；该临时 App 结束后无遗留容器。最终验收直接调用已部署生产 App
  的只读函数，不构建新镜像，结果为 `OK`。
- 不应在已同步生产产物的普通工作区反复使用 `modal run` 做简单只读验收；它会因
  mount/image 输入身份变化触发不必要的重建与上传。优先直接调用已部署的只读函数。

## 6. 生产费用基线

2026-11-13 已按当前 Modal 单价、v28 资源规格、历史函数时长和 Website FULL 实际账单建立独立费用基线。
普通增量日整条主跑估计 `$0.18–0.30`，Website FULL 日 `$0.30–0.42`，三市场重训日约
`$1.40–1.60`；证据分类、计算公式和未验收边界见
[生产 Modal 每日费用估算](2026-11-13-production-modal-cost-estimate.md)。

11 月 13 日 v28 自然主跑已命中 4/12 incremental，并形成完整账单小时；2/8 Provider 也完成了
新日期全市场构建，但仍复用 2 个 history cache 条目。以下第 7 节补充其实测结果；严格物理清空
`ingest_cache/` 的隔离实验和真实三市场重训账单仍未完成，因此规划区间不得解释为费用上限。

## 7. 11 月 13 日自然主跑与 v29 部署

- v28 `daily_cron` 于 18:00:00 自然入队，18:31:19 完成，平台状态为 `success`；
  T/U 为 `2026-11-13` / `2026-11-16`。Provider、三市场信号、Website 和 postclose marker
  全部成功，无人工补跑。
- 2/8 Provider 本次 `cache_complete=0`、`refetched=1800`，用时 `1373.2172 s`，
  PASS archive digest 为 `18dde7af1218…`。它是有效的新日期全市场自然证据，但因
  `history_cache_hits=2`，不等同于整个 `ingest_cache/` 物理清空的隔离实验。
- Website 明确检测到三市场有效增量祖先并跳过 smoke/FULL；三个 4/12 worker
  分别用时 `40.0956 / 69.0637 / 89.9270 s`，全部为 `INCREMENTAL`。因此 v28
  4/12 自然 incremental 门禁已通过。
- 18:00–19:00 Modal 完整账单小时为 Daily `$0.15755061` + Website `$0.02961151`
  = **`$0.18716212`**（免费额度/抵扣前）。
- 自然日志曾因协调器内部传递已解析日期而误写 `OPERATOR_OVERRIDE`。修复后显式区分
  `NATURAL_SCHEDULE`、`SCHEDULED_RECOVERY` 和 `OPERATOR_OVERRIDE`；直接人工调用未传来源时
  按 date/lag/refresh 参数推导为人工，显式伪装自然调度会在任何生产工作前失败。
- 本地完整验证为 `1282 passed, 2 skipped, 50 subtests passed`。修复提交
  `d0fdb6d47`已推送；Website v29 于 22:41:45、Daily v29 于 22:42:30 前台部署，
  共享镜像 `im-go7O0S3h6TwXQpWYt0jfhB`。四个 cron、两个生产 App、资源和 Volume 合同未变。
- v29 部署发生在当日 18:00 主跑之后；因此上述业务产物是 v28 自然证据。v29 的
  23:00/23:15 和次日 07:00 自然保护入口需按实际调度结果分开验收。
