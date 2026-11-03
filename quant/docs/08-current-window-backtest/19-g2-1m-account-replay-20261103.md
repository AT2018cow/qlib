# 19 — G2 全周期表切换到 100 万账户口径（2026-11-03）

> **目的：** 方法论页面的 2018–2024 全周期复核表改为 100 万元账户计算，与线上三个 1M 模拟账户同一资金规模；不改变冻结信号、执行规则或策略参数。

## 1. 方法

- 使用 `regime_g2_csi300_20261102_a1` / `regime_g2_csi500_20261102_a1` / `regime_g2_csi1000_20261102_a1` 的冻结 `signal.parquet`；
- 当前生产 profile：
  - CSI300：spine B（10d）+ S01（Top10/Drop1）
  - CSI500：spine B（20d）+ S02（Top20/Drop2）
  - CSI1000：spine A（20d）+ S01（Top10/Drop1）
- 同一执行契约：T 日收盘信号 → T+1 开盘、board-aware 涨跌停/停牌、买入 5bp / 卖出 15bp、最低 5 元、100 股整手；
- 仅把 `collect_data(account=...)` 与 `portfolio_performance(initial_cash=...)` 从 1 亿元改为 100 万元；
- 回放窗口 `2018-01-03`–`2024-12-31`，1698 个报告日。

**同源校验：** 用同一脚本把 CSI300 账户设回 1 亿元，复现 G2 的 `12.19% / -46.22% / 0.563`，确认脚本与 G2 结果同源。

## 2. 1M 结果

| 市场 | 当前策略 | CAGR | MaxDD | Sharpe | 2018 MaxDD | +10bp CAGR | +20bp CAGR |
|---|---|---:|---:|---:|---:|---:|---:|
| CSI300 | 10d Top10/Drop1 | 13.69% | -43.28% | 0.615 | -24.60% | 8.93% | 2.45% |
| CSI500 | 20d Top20/Drop2 | 5.62% | -41.92% | 0.353 | -28.85% | 0.20% | -7.90% |
| CSI1000 | 20d Top10/Drop1 | 17.72% | -50.08% | 0.714 | -20.78% | 12.98% | 6.64% |

与 1 亿元口径的差异主要来自整手取整、最低佣金和现金余额路径，不是简单按比例缩放。

## 3. 门槛重算

- **CSI300：** D1/全周期回撤、D3、D7 +10bp、D7 +20bp 均通过；2018 回撤比基线深 **0.97pp**，硬 D2 仍差 0.97pp；v2 D2 以 2pp 容忍 + 全周期回撤改善 13.39pp 通过。1M 下 +10bp CAGR 为 8.93%，高于基准 +8pp 门槛，原先 1 亿元口径的“+10bp 差 0.46pp”不再成立。
- **CSI500：** D1（相对基准回撤）、D3（全周期收益）、D7 +10bp / +20bp 均未过；+10bp CAGR 为 +0.20%，+20bp 为 -7.90%，对成本更敏感；D2 通过。收益优先取舍仍需披露。
- **CSI1000：** 保留现役 S01；Top20/Drop2 的 1M 全周期 CAGR 19.68%、MaxDD -39.67%，但 2018 MaxDD -28.53% 比 S01 的 -20.78% 深 7.75pp，且既有相位/seed 稳定性复核不足，故不采用。

## 3.1 成本压力口径修正

首版 1M 回放的算术成本压力函数把首日 prior NAV 写死为 1 亿元，用于 100 万账户会高估首日成本。已改为 `apply_cost_stress(..., initial_cash=...)`，按实际账户规模计算；上表 +10bp/+20bp 为修正后结果。100M G2 结果不受影响（默认仍为 1 亿元）。

## 4. 产物与复现

- 汇总：`quant/data/current_window_backtest/g2_1m_summary.json`
- 回放脚本：`quant/code/research/regime_reselection/run_g1_baseline.py --account 1000000`
- 复现命令示例：

```bash
PYTHONPATH=".:quant:quant/code:quant/code/execution:quant/code/production:quant/code/research/regime_reselection:quant/code/research/stage_ab" \
python quant/code/research/regime_reselection/run_g1_baseline.py \
  --signal-root /tmp/opencode/g2_1m/signals \
  --provider /tmp/opencode/qlib_cn_sel/qlib_bin \
  --signal-start 2018-01-02 --exec-end 2024-12-31 \
  --markets csi300 --spines B --cells S01 --account 1000000
```

CSI500 使用 `--markets csi500 --spines B --cells S02`，CSI1000 使用 `--markets csi1000 --spines A --cells S01`。

## 5. 页面更新

`quant/website/methodology.html` 的全周期表、选型取舍和风险段落已改为 1M 数字；表格标题明确标注“100 万元账户”，并保留“研究回测不等于未来实盘收益”的声明。
