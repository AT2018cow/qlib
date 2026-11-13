# 生产 Modal 每日费用估算（2026-11-13）

> 状态：`v28 FULL MEASURED / v28 INCREMENTAL MEASURED / RETRAIN ESTIMATED`
>
> 核对时间：2026-11-13，Asia/Shanghai。
>
> 本文记录 v28 自然主跑、当前 v29 资源规格、Modal 实时单价、历史函数时长和账单证据。
> v29 未改变资源规格；部署/镜像构建费用与自然主跑分列。金额均为美元、免费额度抵扣前。

## 1. 结论

| 生产场景 | Website 阶段 | 整条每日主跑 |
|---|---:|---:|
| 普通日，Website 增量 | **$0.025–0.05** | **$0.18–0.30** |
| 增量祖先失效，触发 smoke + FULL | **$0.15–0.17** | **$0.30–0.42** |
| 三市场重训日，增量祖先有效 | **$0.05–0.10** | **$1.40–1.60** |
| 三市场重训日，同时触发 FULL | **$0.15–0.20** | **$1.50–1.70** |

按约 20 个交易日一个重训周期粗估，若 19 个普通增量日、1 个重训日且没有异常 FULL/重试，生产计算约
**$5–7.5/周期**。该估算不包含长期 Volume 存储、部署/镜像构建、人工 probe、失败重试或研究任务。

这不是费用上限或最终发票：Provider 受外部行情请求耗时影响，Modal 还会计入应用加载和容器缩容前的
存活时间。当前最主要的周期性成本是 non-preemptible 三市场 Signal 重训，不是普通 Website 增量或三个
只读检查入口。

## 2. 当前单价与资源规格

2026-11-13 通过 Modal billing API 只读回读的基础单价为：

- CPU：`$0.04730 / physical core-hour`；
- 内存：`$0.00800 / GiB-hour`；
- Network egress：`$0.04000 / GiB`，当前生产记录中的 egress 相比 CPU/内存可忽略；
- non-preemptible CPU Function 的 CPU/内存为基础价格的 **3 倍**。

Modal 按资源请求量和实际使用量中的较高者计费，并计入 application load 与 scale-down 前仍存活的时间。
官方说明见 [Pricing](https://modal.com/pricing)、[Preemption](https://modal.com/docs/guide/preemption) 和
[Resources](https://modal.com/docs/guide/resources)。

| 阶段 | 当前规格 | 抢占语义 | 请求资源小时价 |
|---|---:|---|---:|
| Provider `ensure_overlay` | 2 CPU / 8 GiB | preemptible | $0.1586/h |
| Signal `daily_standalone` | 8 CPU / 16 GiB | **non-preemptible** | **$1.5192/h** |
| Website incremental | 4 CPU / 12 GiB | preemptible | $0.2852/h |
| Website smoke/FULL | 8 CPU / 16 GiB | preemptible | $0.5064/h |
| Website coordinator | 1 CPU / 2 GiB | preemptible | $0.0633/h |

Signal 和 Website 是不同模型/账户路径，不能为节省费用合并。Website 有完整有效祖先时使用 4/12 worker；
如果新 session 正好落在重训边界，该增量 worker 可以完成一个受限新 fit。无完整祖先、语义身份变化或受控
fallback 才进入 8/16 的逐市场 smoke + FULL 路径。

## 3. 分阶段证据与复算

### 3.1 Provider

随后的 v28 自然新日期主跑在 2/8 规格下用时 `1,373.2172 s`：当日
`cache_complete=0`、`refetched=1800`，最终 1,800 成员 + 52 个必需执行/持仓股票的
archive gate PASS。Modal 资源统计内存 p99 约 `0.71 GiB`，receipt 进程树 RSS 峰值约
`1.21 GiB`。因存在 `history_cache_hits=2`，这是新日期全市场自然证据，仍不是整个
`ingest_cache/` 物理清空实验。

当前 2/8 Provider 的最近一次成功调用耗时 `953.5268 s`，按请求资源复算约 **$0.0420**。它发生在前一
次 `473.9834 s` 失败调用之后并复用了已完成 raw batches；两次合计的 Provider 计算约 **$0.0629**，不能
解释为一次干净冷缓存构建。

历史上有一次明确的 1,800 股票全市场 PASS 构建耗时 `2,933.9964 s`，当时规格为 4/8。该任务主要受
外部行情和串行 fallback 约束；若仅按相同 wall time 重新套用当前 2/8 价格，约为 **$0.1293**。由于尚无
当前 2/8、空 `ingest_cache/` 的真实全市场新日期测量，日常总价对 Provider 使用 **$0.06–0.13** 的保守
区间。

### 3.2 普通信号日与重训日

最近一个模型缓存命中的三市场 Signal 调用分别耗时 `52.9839 / 27.2432 / 18.2448 s`，合计
`98.4719 s`。按 non-preemptible 8/16 复算纯执行约 **$0.0416**；加入加载和缩容余量后，普通 Signal
阶段按 **$0.04–0.07** 估计。

一组三市场顺序重训调用分别耗时 `2,721.8731 / 86.6311 / 74.6553 s`，合计 `2,883.1596 s`
（约 48.1 分钟）。按当前 non-preemptible 8/16 复算约 **$1.2166**；加入容器存活余量后，重训 Signal
阶段按 **$1.22–1.28** 估计。这一阶段支配重训日总费用。

### 3.3 Website 增量

2026-11-13 v28 自然主跑明确记录 `incremental prefix present for all markets; skipping smoke replay`。
三个 4/12 worker 分别用时 `40.0956 / 69.0637 / 89.9270 s`，全部成功并生成
`replay_mode=INCREMENTAL`。Website coordinator 用时 `133.4591 s`。

18:00–19:00 完整账单小时中 Website App 的 CPU `$0.02163376`、内存 `$0.00793249`、egress
`$0.00004526`，合计 **`$0.02961151`**。这是当前 4/12 自然生产证据，因此普通
Website 增量阶段的规划区间收窄为 **$0.025–0.05**；遇到 `new_fit=1`、加载抖动或更长数据窗口
仍可高于本次实测。

### 3.4 Website FULL

2026-11-12 v28 semantic 变化触发三市场 smoke + FULL：

- 三个 smoke worker 合计 `453.3189 s`；
- 三个 FULL worker 合计 `462.0134 s`；
- Website coordinator `726.2651 s`；
- 三市场 FULL 收据均为 `replay_mode=FULL`、`new_fits=1`。

按请求规格直接复算约 **$0.1415**；相邻账单小时中 Website App 的 CPU、内存和极小 egress 合计约
**$0.161**，还混有完成后的只读状态回查。因此日常估算用 **$0.15–0.17** 作为一次实际 smoke + FULL
的可操作区间，而不把整个 11 月 12 日账单当作单次 FULL 成本。

## 4. 整条主跑的组合口径

整条每日主跑不是一个固定规格容器，费用由 Provider、三市场 Signal、Website、两个 coordinator 以及
只读检查共同组成。表 1 的整条主跑范围按以下口径组合：

| 组成 | 普通增量日 | FULL 日 | 重训增量日 |
|---|---:|---:|---:|
| Provider | $0.06–0.13 | $0.06–0.13 | $0.06–0.13 |
| Signal | $0.04–0.07 | $0.04–0.07 | $1.22–1.28 |
| Website | $0.04–0.08 | $0.15–0.17 | $0.05–0.10 |
| coordinator、只读检查及余量 | $0.02–0.04 | $0.03–0.05 | $0.03–0.05 |

逐项区间机械相加得到的普通日敏感性外沿约为 `$0.16–0.32`；表 1 的 `$0.18–0.30` 是用于日常规划的
圆整工作区间，不是硬下限或上限。

11 月 12 日 18:00 后的账单小时同时包含 v27 自然失败、旧版恢复、v28 前台 FULL、状态核验及临时 App；
它是故障恢复总费用证据，不能直接当作普通日或单次 v28 主跑基线。

11 月 13 日 18:00–19:00 只有自然普通主跑：Daily App `$0.15755061`、Website App
`$0.02961151`，合计 **`$0.18716212`**。这个账单 API 实际值已包含应用加载和缩容窗口，
与表 1 普通日估算下沿一致。

## 5. 后续验收与更新规则

1. v28 自然 18:00 周期已用 App 小时账单和三市场 immutable publication receipt 关联 4/12
   incremental；后续自然日应继续观测区间稳定性，不得仅用 coordinator wall time 代替账单。
2. 当前 2/8 Provider 必须完成一次空 `ingest_cache/` 的全市场新日期构建，才能关闭 `$0.06–0.13`
   的宽区间。
3. 下一次三市场真实重训应分别记录 Signal 和 Website 的 `new_fit`、wall time、资源采样和账单；不得把
   研究批次成本并入生产日成本。
4. 费用下降不能通过放宽数据 gate、移除三市场/账户隔离、静默 FULL fallback、降低重训可靠性或把
   网站因果模拟与 scheduled paper 账户合并实现。
5. 每次更新必须标明 `MEASURED`、`REPRICED` 或 `ESTIMATED`；失败/重试/部署费用与自然成功周期分列。
