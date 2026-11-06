# 2026-11-06 扩展层纠错回放证据

本目录保留冻结信号/底稿下的日频账户报告，供独立复算。来源和结论见[修复验收记录](../../../docs/07-code-review/2026-11-06-extension-repair-acceptance.md)，每个文件的 SHA256 见 [SHA256SUMS.json](SHA256SUMS.json)。

| 目录 | 数量 | 含义 |
|---|---:|---|
| `frozen_baseline/` | 12 | 原始 G1 基础报告；与原汇总的 CAGR、MDD、Sharpe 逐项匹配 |
| `corrected_execution/` | 12 | 冻结预测，在修正数量/限价规则下重新成交与核算 |
| `corrected_overlay_frozen_base/` | 11 | 原始底稿不变，仅修正趋势/熔断/组合算法，便于归因 |
| `corrected_overlay_corrected_base/` | 11 | 在修正后基础报告上应用修正后的风控/组合算法 |

执行窗口为 2021-02-19 至 2024-02-05，初始资金 100M，属于已消费历史样本上的诊断回放。历史 ST 与开盘流动性未取得独立证据，均为 reference simulation；结果不证明可实盘成交，也不触发当前 1M 生产策略切换。

原始历史报告和排行榜保留在原目录，本目录不替换它们。可用 `portfolio_performance()`、实际初始资金和每个目录的 `report.parquet` 复算；成本压力采用修正后的 `apply_cost_stress()`。
