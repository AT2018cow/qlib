# 2026-11-12 生产部署与文档状态

> 核对时间：2026-11-12 10:06–10:16，Asia/Shanghai。
>
> 性质：只读部署配置、Git 与补跑日志核对；不代表 v26 自然调度、策略晋升或券商成交验收。

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

- v26 首次自然 18:00 主跑、23:00/23:15 检查与恢复、下一交易日 07:00 检查，以及失败邮件实际送达，尚未在本记录时间点验收。23:00 若主跑尚在运行，应区分运行中与失败；23:15 的恢复调用会在同一协调器后排队。
- `website_budget.py` 属于 website semantic 闭包；`1b5ef71ac` 因而改变 semantic identity。下一次新日期运行预计进入新 cache root 的逐市场 smoke/full 路径，后续新交易日再验证 4/12 incremental；具体耗时、费用和结果以实际 receipt/日志为准。
- 2/8 Provider 的冷缓存完整新日期构建、全量 replay 与前版的逐日 score/ranking/成交/费用/持仓/cash/NAV 对照，以及公网静态站点更新，尚无本次 v26 完整验收证据。
- 研究 AR01–AR09 整改已随 `80014b09f` 提交并推送，其源码包含在后续生产镜像中；这不表示真实三市场研究 bounded replay、策略 `GATE_PASS` 或独立生产认证已完成。CSI300/CSI500 仍为风险接受 override，CSI1000 为 incumbent retained。
- 网站收益是因果纸面模拟参考值，不是券商成交、完整历史 ST、开盘竞价流动性或官方参考价认证。归档清理仍仅允许 dry-run，不能因本文推断已授权删除。
