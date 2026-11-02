# 17 — 2026 年 10 月连续账户回补（新策略）

> **性质：`BACKFILL_RESEARCH`。** 10 月为 point-in-time 回补，不是当时实时发布记录；11/3 起为同一 1M 账户的真实前向 paper。网站标题不标注“回补”，方法论披露。

## 1. 口径

- 2026-09-30 收盘：三市场各 100 万，NAV=1.0000，空仓；
- 2026-10-08 开盘：首次建仓（国庆后首个交易日）；
- 每个交易日 T+1 开盘执行，board-aware 涨跌停/停牌，5bp 买 / 15bp 卖；
- 2026-10-30 收盘：回补结束，持仓/现金作为 11/3 的连续 paper state；
- 信号使用新生产 profile：
  - CSI300：10d + Top10/Drop1；
  - CSI500：20d + Top20/Drop2；
  - CSI1000：20d + Top10/Drop1。

## 2. 回补结果

| 市场 | 9/30–10/30 | 同期基准 | 10/16–10/30 新策略 | 旧策略同窗口 |
|---|---:|---:|---:|---:|
| CSI300 | **-25.78%** | -8.54% | **-15.26%** | -18.34% |
| CSI500 | **-21.05%** | -15.72% | **-12.30%** | -19.49% |
| CSI1000 | **-26.85%** | -15.59% | **-18.14%** | -20.60% |

- 全 10 月亏损主要来自 10/8–10/15 急跌；
- 旧策略 10/16 才上线，不能把“新策略全月”与“旧策略半个月”直接比较；
- 10/16–10/30 同窗口，新组合均优于旧策略。

## 3. 账户连续性

- 10/30 收盘状态已从 Qlib 回测最后持仓提取：
  - CSI300：10 只，现金约 1.34 万；
  - CSI500：20 只，现金约 1.64 万；
  - CSI1000：10 只，现金约 0.76 万；
- 已写入新 1M lineage 的 `paper_portfolio` state；
- 已执行 journal anchor；
- 11/3 起从该状态继续，不重新空仓。

> 成本基准/入场日期在回补 state 中使用 10/30 收盘价近似固化；后续交易按真实 paper 执行继续。

## 4. 网站与归档

- 旧 10 月 100M 信号、chart、paper、metadata 已移至 `quant/data/signals/archive/legacy_100m_2026-10/`；
- 网站日期索引只保留新回补的 17 个交易日（10/08–10/30）；
- 新 `csi300_forward_performance.json` / `csi500_forward_performance.json` / `csi1000_forward_performance.json` 从 2026-09-30 起；
- 网站标题仍为“策略累计收益 · 模拟盘参考”，不写“回补”；
- 方法论明确说明 10 月为回补、11/3 起为真实前向 paper，二者是同一账户连续状态。

## 5. 风险

- 10 月回补不能用于策略未来收益证明；
- 回补 state 的持仓成本基准为近似值；
- 11/3 的首个真实 paper 日需核对 state、pending、NAV；
- 若 journal/state 校验失败，生产应 fail-closed，不允许静默重置。

## 6. 产物

- `quant/data/signals/2026-10-*`：新回补榜单/chart
- `quant/data/signals/csi*_forward_performance.json`：连续回补净值
- `quant/data/signals/available_dates*.json`：新日期索引
- `quant/data/signals/archive/legacy_100m_2026-10/`：旧数据归档
- 生产 state：`/vol/paper_portfolio/*_1m.json`
