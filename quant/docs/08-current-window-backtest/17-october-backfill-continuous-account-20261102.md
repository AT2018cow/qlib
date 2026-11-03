# 17 — 2026-10 至 11/02 连续账户回补（新策略）

> **性质：`BACKFILL_RESEARCH`。** 2026-10-08–2026-11-02 为 point-in-time 回补，不是当时实时发布记录；11/3 起为同一 1M 账户的真实前向 paper。网站标题不标注“回补”，方法论披露。

## 1. 口径

- 2026-09-30 收盘：三市场各 100 万，NAV=1.0000，空仓；
- 2026-10-08 开盘：首次建仓（国庆后首个交易日）；
- 每个交易日 T+1 开盘执行，board-aware 涨跌停/停牌，5bp 买 / 15bp 卖；
- 2026-11-02 收盘：回补结束，持仓/现金作为 11/3 的连续 paper state；
- 信号使用新生产 profile：
  - CSI300：10d + Top10/Drop1；
  - CSI500：20d + Top20/Drop2；
  - CSI1000：20d + Top10/Drop1；
- 回补段模型谱系与生产一致：10/08 bootstrap，10/27 重训，11/02 复用 10/27 模型；无前视。

## 2. 回补结果

| 市场 | 9/30–11/02 | 11/02 单日 | 同期基准 | 10/16–10/30 新策略 | 旧策略同窗口 |
|---|---:|---:|---:|---:|---:|
| CSI300 | **-24.60%** | +1.59% | -8.54% | **-15.26%** | -18.34% |
| CSI500 | **-18.27%** | +3.52% | -15.72% | **-12.30%** | -19.49% |
| CSI1000 | **-23.94%** | +3.98% | -15.59% | **-18.14%** | -20.60% |

- 全段亏损主要来自 10/08–10/15 急跌；
- 旧策略 10/16 才上线，不能把“新策略全段”与“旧策略半个月”直接比较；
- 10/16–10/30 同窗口，新组合均优于旧策略。

## 3. 账户连续性

- 11/02 收盘状态已从 Qlib 回测最后持仓提取：
  - CSI300：10 只，现金 9,710.66；
  - CSI500：20 只，现金 27,149.55；
  - CSI1000：10 只，现金 6,803.30；
- 已写入新 1M lineage 的 `paper_portfolio` state；
- 已重新锚定 journal：旧 10/30 anchor 保留在 `bootstrap_anchors.json` 的 `superseded` 审计字段，新 11/02 hash 生效；
- 11/3 起从 11/02 收盘状态继续，不重新空仓。

> 成本基准/入场日期在回补 state 中使用 11/02 收盘价近似固化；后续交易按真实 paper 执行继续。

## 4. 网站与归档

- 旧 10 月及 11/02 的 100M 信号、chart、paper、metadata 保留在 `quant/data/signals/archive/legacy_100m_2026-10/`，不公开展示；
- 网站日期索引包含回补段 18 个交易日（10/08–11/02）；11/03 起追加真实前向日期，旧数据不进入索引；
- 三份 `csi*_forward_performance.json` 从 2026-09-30 起；回补段共 19 个点，latest 2026-11-02；11/03 待结算，11/04 追加 11/03 估值；
- 网站标题仍为“策略累计收益 · 模拟盘参考”，不写“回补”；
- 方法论明确说明 10/08–11/02 为回补、11/3 起为真实前向 paper，二者是同一账户连续状态。

## 5. 历史 sidecar 修正

首版回补 sidecar 生成器在单次 `collect_data` 结束后读取 `decision.strategy.trade_position`，而 Qlib 复用同一个 strategy 对象，导致所有历史日期的 `paper_portfolio` 都写成最后一天的持仓。本次已修正为：

- 在下一日 decision 产出时快照前一日执行后的 position；
- 重算 18 个交易日 × 3 市场全部 `paper_portfolio` / `mvp_metadata`；
- 10/08 现为 9 只 / 现金约 21.31 万（首日有一只涨停未成交），10/16、10/30、11/02 各自独立；
- 11/02 state 与 11/02 paper artifact 的现金/持仓逐项一致。

该修正不改变任何榜单、chart 或 NAV；只修正历史持仓/调仓展示。

## 6. 风险

- 回补段不能用于策略未来收益证明；
- 回补 state 的持仓成本基准为近似值；
- 11/03 首个真实 paper 已发布；11/04 结算 11/03 开盘/收盘并追加 11/03 估值；
- 若 journal/state 校验失败，生产应 fail-closed，不允许静默重置。

## 7. 产物

- `quant/data/signals/2026-10-*` / `2026-11-02_*`：新回补榜单/chart/paper/metadata
- `quant/data/signals/csi*_backfill_performance.json`：19 点回补净值
- `quant/data/signals/csi*_forward_performance.json`：连续回补净值
- `quant/data/signals/available_dates*.json`：新日期索引
- `quant/data/signals/archive/legacy_100m_2026-10/`：旧数据归档
- 生产 state：`/vol/paper_portfolio/*_1m.json`
- journal：`/vol/publication_journal/bootstrap_anchors.json`
