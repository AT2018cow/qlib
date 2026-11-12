# 网站历史账户 ST 核实（2026-10-08–2026-11-11）

> 日期：2026-11-12
>
> 状态：`LOCAL VERIFIED / NO ACTIVE ST IN WINDOW / PRODUCTION UNCHANGED`
>
> 范围：网站三市场因果模拟账户的 25 个执行日、131 只 active 股票、1096 个 symbol-date。
>
> 性质：离线历史 ST 证据核实与重放对照；不是生产部署、账户重发或券商成交认证。

## 1. 结论

对网站 2026-10-08–2026-11-11 的三市场账户，按“持仓 + 计划买入/卖出 + 实际成交”定义 active universe：

- BaoStock 历史 `isST` 覆盖 **131 只股票、25 个执行日、1096 个 active symbol-date**；
- **0 缺失、0 查询错误**；
- **没有任何一只 active 股票在任一执行日处于 ST 状态**；
- 用这份逐日 ST 历史重新 replay 三市场，结果与当前已发布账户 **逐日完全一致**：
  - 最大账户差额：**0.00**
  - 最大现金差额：**0.00**
  - 最终收益不变。

因此，**ST 不是当前网站 10/08–11/11 收益偏差的来源**。现有 `ST_HISTORY_UNVERIFIED_REFERENCE_ONLY` 标签仍是保守披露，但本窗口的 ST 问题已经被日期化证据关闭。

## 2. 数据源与方法

| 项目 | 内容 |
|---|---|
| ST 历史源 | BaoStock `query_history_k_data_plus`，字段 `date,code,close,tradestatus,isST`，`adjustflag=3` |
| 查询区间 | 2026-09-30–2026-11-11（覆盖首个信号日与全部执行日） |
| Active universe | 每个执行日的 `opening_positions` + `planned_buy` + `planned_sell` + `executed_buy` + `executed_sell` |
| 去重股票 | 131 只（SH/SZ） |
| 执行日 | 25 个（2026-10-08–2026-11-11） |
| 覆盖检查 | 每个 active symbol-date 必须有非空 `isST`；缺失即失败 |
| 重放输入 | 生产 11/11 archive `1f0fc68e…` + 已发布 `signal.parquet` |
| 对照基线 | 当前已发布 `report.parquet` / `website_performance.json` |

字段语义抽查：

```text
SH600079（ST人福）  2026-11-10/11  isST=1
SH688622（*ST禾信） 2026-11-10/11  isST=1
SH600000（浦发银行）2026-11-10/11  isST=0
```

## 3. 覆盖结果

```text
active_symbols          131
active_symbol_dates     1096
missing_count           0
query_errors            0
ST active symbol-dates  0
dates with any ST       0
```

逐日 `st_history.json` 的 25 个日期均为空列表，表示“BaoStock 确认当日 active universe 无 ST”，不是“未查询”。

## 4. 重放对比

| 市场 | 基线期末 NAV | 带 ST 历史期末 NAV | 最大账户差额 | 最大现金差额 | 成交笔数 | blocked buy | blocked sell |
|---|---:|---:|---:|---:|---:|---:|---:|
| CSI300 | 827,122.13 | 827,122.13 | 0.00 | 0.00 | 58 | 1 | 0 |
| CSI500 | 926,696.23 | 926,696.23 | 0.00 | 0.00 | 116 | 13 | 0 |
| CSI1000 | 862,955.83 | 862,955.83 | 0.00 | 0.00 | 58 | 0 | 0 |

累计收益保持：

- CSI300：**-17.2878%**
- CSI500：**-7.3304%**
- CSI1000：**-13.7044%**

这里的 0.00 是逐日 `account` 和 `cash` 的最大绝对差，不是四舍五入后的展示值。

## 5. 代码接入

本轮增加了可选的日期化 ST 路径，默认行为不变：

- `prepare_data(..., st_history=None)` 将 ST 历史带入 replay 数据；
- `replay()` 在 `st_history` 存在时，为每个执行日的 active 股票调用 `st_flags()`，并把 `st_flags` 传给 `compute_limit_masks()`；
- `st_history` 缺少任一执行日会 **fail closed**，不会静默当作非 ST；
- 无 `st_history` 时保持原有 reference-only 行为，所有现有调用不受影响。

新增回归：

- ST 股票在 2026-07-06 前按 5% 涨跌停限制，能阻止一笔非 ST 下可成交的买入；
- 缺少执行日的 ST 历史被拒绝；
- `prepare_data` 正确携带 `st_history`。

## 6. 边界

1. **这是 active universe 核实，不是全市场 ST 历史。**
   只查询了网站账户实际可能持有/交易的 131 只股票；没有为全市场 3000+ 股票建立历史 ST 库。

2. **没有真实 ST 成交样本。**
   本窗口 active universe 没有 ST 股票，因此真实数据上没有触发 5% ST 限价；5% 规则由合成回归覆盖。

3. **BaoStock 是单一历史源。**
   字段语义已用已知 ST/*ST/非 ST 样本验证，但没有对 131 只逐一做交易所公告交叉核对；BaoStock 的 `isST` 仍可能存在个别日期/股票的源误差。

4. **生产网站仍按 reference-only 生成。**
   本次是离线核实；`website_replay_core` 的默认 `build_market_impl` 尚未传入 `st_history`，所以已发布 JSON 仍保留 `ST_HISTORY_UNVERIFIED_REFERENCE_ONLY`。若要让未来日期也自动使用 ST 历史，需要把日期化文件接入 website worker 并建立每日维护流程。

5. **不改变生产数据或部署。**
   本轮没有重发网站 JSON、没有改 paper 账户、没有部署 App。

## 7. 证据

- 逐日 active universe：`quant/data/st_history/2026-11-11/active_by_date.json`
- 逐日 ST 名单：`quant/data/st_history/2026-11-11/st_history.json`
- 覆盖报告：`quant/data/st_history/2026-11-11/coverage.json`
- BaoStock 原始行：`quant/data/st_history/2026-11-11/raw_baostock.json`
- 重放对比：`quant/data/st_history/2026-11-11/replay_comparison.json`
- 文件哈希与来源：`quant/data/st_history/2026-11-11/source_manifest.json`

## 8. 后续建议

- 若只需要回答“当前 10/08–11/11 收益是否受 ST 影响”：**已关闭，答案为否**。
- 若要让网站未来日期也声明 `ST_VERIFIED_HISTORY`：需要把 `st_history` 接入 `build_market_impl`，按执行日维护 BaoStock `isST` 文件，并增加缺失/异常 fail-closed 监控。
- 在此之前，保留 `ST_HISTORY_UNVERIFIED_REFERENCE_ONLY` 是准确的保守标签；本报告不把它回写成已全面认证。
