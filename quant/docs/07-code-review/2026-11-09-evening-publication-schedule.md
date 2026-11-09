# T 日收盘后发布与 T+1 执行调度（2026-11-09）

状态：`IMPLEMENTED / DEPLOYED / VERIFIED / MONITORING`

## 目标

不再等待 T+1 早上才发布信号和收益。新数据源在 T 日收盘后即可生成 T+1 信号，并计算 T 日收盘 NAV。

## 新的生产调度

| 时间（Asia/Shanghai） | 入口 | 作用 |
|---|---|---|
| T 18:00 | `daily_evening` | 构建 T overlay，生成 T+1 信号，发布 T 日 NAV，创建 T+1 paper pending |
| T 19:00 | `daily_evening_retry` | 只重试失败步骤；18:00 已成功时幂等退出 |
| T+1 07:00 | `daily_cron` | pre-open 检查/补发；已有 journal 直接返回，缺 overlay 才补建，缺失信号可补发 |
| T+1 18:00 | `daily_evening` | 结算 T+1 pending（官方开盘价 + 收盘估值），生成 T+2 信号，发布 T+1 NAV |

已移除：

- `daily_cron_second_chance` 的 08:00 定时触发；
- 旧 07:00 全量主跑 + 08:00 全量补跑模式。

## Paper pre-open 规则

`require_reference_preopen` 现在允许两个窗口：

1. usage_date 当天 00:00–09:30（旧 morning 路径）；
2. signal_data_date 收盘 15:00 后，到 usage_date 09:30 前（T evening 规划 T+1）。

仍拒绝：

- signal 收盘前规划；
- usage_date 09:30 后补建订单。

## Overlay 构建

新增 `ensure_overlay(trade_date)`：

- 优先复用 `/vol/cn_data_overlay/<trade_date>` 的 PASS manifest；
- 否则以上一个 PASS overlay 为 base，只抓取缺失交易日；
- 通过 `data_ingest` gate 后写入生产 Volume；
- 18:00 构建失败时，19:00 重试；07:00 仅在 overlay 缺失时补建，并可按 journal 补发缺失信号。

## 11/09 验收

- 11/09 03:52 手动调用 `daily_cron`，在 pre-open 窗口内发布 11/09 信号/paper；
- 18:00 首轮因容器 `data_ingest` 路径、CSIndex 超时/日期滞后、网站阶段漏调用失败；修复后 19:00 重试成功；
- `2026-11-10` 三市场 canonical 信号 12 个文件，journal `VOLUME_COMMITTED`，metadata `CANONICAL_T1`、`lag_sessions=0`；
- 网站 NAV 已更新到 2026-11-09，website commit `9992cec39`；公网 `available_dates` 已包含 11/09 与 11/10；
- 网站阶段在旧 60 分钟 worker 上限下 CSI1000 冷启动超时；worker 上限已提升到 90 分钟，首轮冷回放已完成；
- 后续凭证/CI/网站加固见 [`2026-11-10-security-hardening.md`](2026-11-10-security-hardening.md)。

## Paper 结算时间

T+1 18:00 的 evening 运行会用 T+1 官方开盘价结算前一日 pending，并用 T+1 收盘价计算 NAV。  
没有单独实现 09:35 部分数据结算，因为开盘时没有收盘价，无法生成最终当日 NAV；18:00 是会计上更准确的结算/估值时点。

## 成本

- 18:00 主发 + 19:00 幂等重试；
- 07:00 盘前检查/补发；已发布时做 journal/website 收据检查；
- 网站 App 有发布收据幂等短路和预算保护；
- 研究/回填/dry run 不在生产 workspace 运行。
