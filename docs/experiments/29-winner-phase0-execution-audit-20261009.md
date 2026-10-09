# 29 — Winner Phase 0 执行与利润形成独立审计报告（2026-10-09）

> **状态：winner phase 0 的 D0–D3 已完成并通过；执行完整性整体仍为 BLOCKED（待全量覆盖扩展与真实市场证据）。**
> 执行依据 [doc 28 runbook](28-csi1000-stage-b-execution-profit-formation-audit-runbook-20261009.md) 第 1–3 节，仅处理 winner phase 0。
> 独立实现：`audit/run_winner_phase0_execution_audit.py`。原始证据：`audit/evidence/winner_phase0/`。

## 1. 证据身份与导出（runbook §1–§2A）

| 项目 | 结果 |
|---|---|
| winner candidate | `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2` |
| snapshot | `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1` |
| signal.parquet（423,876 行） | SHA256 与冻结 JSON 一致 ✓ |
| decisions.json（424 决策 / 482 订单） | SHA256 与冻结 JSON 一致 ✓ |
| report.parquet（424 交易日） | SHA256 与冻结 JSON 一致 ✓（先前已核） |
| provider 日历 `cn_data/calendars/day.txt`（6,482 行） | 已导出并留档 ✓ |
| provider 行情（206 只订单股票，open/close/factor/volume，2024-12-30..2026-09-30） | 经授权只读导出 ✓ |
| 成分定义 `cn_data/instruments/csi1000.txt`（33,008 行 span） | 已导出并留档 ✓ |

日历门禁：报告 424 天 = 决策 424 天，全部落在 provider 日历中，且为日历上的**连续** 424 个交易日（无缺日、无替换）→ **PASS**。

## 2. D1 决策重建（全量 424 天，非抽样）

以冻结 signal 在前一交易日（shift=1）的分数，独立复刻 `deterministic_score_order`（score desc / instrument asc / NaN last）+ Top20/Drop2 组合规则，逐日重建应卖/应买集合，并与 decisions.json 逐字段比对：

- **424/424 天重建与实际完全一致（0 偏差）**，包括：304 个无订单日、109 个标准 4 单日、首日 20 单建仓、以及若干因涨跌停/指数剔除导致的不对称日。
- 482 个订单的 factor 与 provider 当日 `$factor` 全部逐位一致。
- 252 笔买入金额全部与独立公式逐位复现：`amount = floor_lot(cash_after_sells × 0.95 / n_buy / open, factor)`（Qlib `(a×f+0.1)//100×100/f`，REG_CN trade_unit=100）。
- 230 笔卖出金额全部等于持仓全量（`trade_val/trade_price` 语义 + `np.isclose` 清仓判定）。
- 22 个重训边界（retrain_asof / signal_end）连续无缺口；20-session 标签成熟与 purge 的**独立证明仍 BLOCKED**（训练窗口原始证据未导出，见 §5）。

## 3. D2 可交易性与定价

独立重实现板块涨跌停规则（主板 ±10%、科创板/创业板 ±20%、法定 0.01 元 tick 上取整、prev_close 缺失时跳过涨跌停判定、当日 close-NaN 即冻结口径的停牌）：

- 482 个订单全部通过独立可交易性判定（0 违规）；卖出前现金恒非负。
- 首例验证：每个重建出的"应卖/应买"订单都在实际订单中，每个被规则阻挡的候选都确实未出现在订单中（双向完整性）。

## 4. D3 独立账本（第二套实现，非 Qlib 重放）

以初始 CNY 100m、逐笔按 冻结订单顺序 → 卖出先行 → 实际开盘价 → 冻结费率（买 0.05% / 卖 0.15%，最低 CNY 5）重建现金、持仓、估值与账户：

- **424 天账户 / 现金 / 估值最大绝对差 7e-8 CNY（容差 0.01）；日毛收益最大差 4.7e-16（容差 1e-10）→ PASS。**
- 累计换手与累计费用与报告逐位一致（0.00 差异）——交易执行记录完整无误。
- 报告 `return` 为毛收益口径（代码语义 `(now_earning + now_cost)/last_account_value`），净值 = `return - cost`，与 `portfolio_performance.py` 约定一致。

## 5. 重大发现 F1：动态成分 universe 导致组合冻结一年（材料级模拟伪影）

**现象**：正常交易止于 2025-07-01；此后直至 2026-06-29（约 240 个交易日）组合**零订单**，仅存 2026-06-30 卖出 SH603301、2026-07-01 买入 SZ000603 两笔。

**根因（已精确归因）**：冻结交易所的行情表按**动态 csi1000 成分**构建。SH603301 与 SH688066 于 2025-06-30 被调出指数后，行情从交易所中消失 → 在冻结模拟中永久"停牌"（不可卖出、估值冻结在最后可得收盘价）。持仓 22 只时 TopK/Drop2 的买入槽恒为空（`today[:n_sell+20-22] = ∅`），故组合数学上无法再交易。SH603301 于 2026-06-30 重新入选指数 → 数据恢复 → 被卖出；SH688066 此后再未入指，永久冻结。

**定量证据**：2025-06-30（首个分歧日）价值差 +242,577.4439 CNY 恰好分解为两只被剔除持仓的过期价格效应（SH603301 +71,983.29、SH688066 +170,594.15，分毫不差）。用成分 span 限制数据可用性后，独立账本与冻结报告全程吻合至 7e-8 CNY。

**口径说明**：成分区间以数据包内 `instruments/csi1000.txt` 为准（按月末分段；与 CSI 官方半年度调整的生效日可能相差数日）。冻结运行消费的就是该文件，因此审计以同一文件复刻其行为是正确的；但"调出生效日"的表述指该文件的区间边界，不是官方生效日。

**影响**：冻结回测**内部自洽**，但其市场可达性假设与现实相悖——真实账户中这两只被调出指数的股票始终可以正常卖出。因此该报告的 Sharpe 1.122 / CAGR 23.01% 是**这一模拟口径的属性**，并非可交易真实策略路径的属性。若以"指数剔除后仍可交易"的现实口径重估，收益路径将显著不同（本报告不重算，避免使用已消耗 tail 重新评价）。

## 6. 结论与剩余 BLOCKED 项

| 门禁 | 结论 |
|---|---|
| 原始报告 SHA + 保存指标（doc 27） | PASS |
| 日历完整性（provider 原始日历，424 天连续） | **PASS** |
| 决策逻辑独立重建（全量 424 天） | **PASS** |
| 订单可交易性 / 金额 / 手数 / 因子（全量 482 单） | **PASS** |
| 独立账本重建（第二实现，全量 424 天） | **PASS** |
| `execution_integrity` | **BLOCKED**（待扩展至其余 9 个 phase；真实停牌/ST/盘口容量证据缺失；st_symbols 为空的静态风险需按实际订单逐案判定） |
| `overall` | **BLOCKED** |
| 未来盈利预期 | INCONCLUSIVE（沿用已消耗 tail，不构成外推依据） |

尚未完成（按 doc 28 D4 与 §6 清单）：
1. 扩展至 winner 4/6/10/15 与 baseline 0/4/6/10/15（baseline phase 0 须取 preflight repeat_b 原始路径）；
2. 真实世界停牌 / 历史ST / 涨跌停盘口可成交性的外部证据（provider 数据不足以独立证明，判 BLOCKED/INCONCLUSIVE）；
3. 20-session 标签成熟与 purge 的训练窗口原始证据级验证；
4. F1 发现对历史结论解释的影响评估（需单独决策，不在本审计内重写任何冻结结果）。

## 7. 复现

```bash
python audit/run_winner_phase0_execution_audit.py   # 默认读取 audit/evidence/winner_phase0/raw
```

原始证据获取（授权只读，实验环境）：

```bash
modal volume get qlib-cn-data "cn_data/calendars/day.txt" day.txt --profile infi
modal volume get qlib-cn-data "cn_data/instruments/csi1000.txt" csi1000_instruments.txt --profile infi
modal volume get qlib-cn-data "<snapshot>/phases/<winner>/phase00/signal.parquet" signal.parquet --profile infi
modal volume get qlib-cn-data "<snapshot>/phases/<winner>/phase00/decisions.json" decisions.json --profile infi
# 行情导出：对 206 只订单股票调用一次只读 CPU 作业（D.features，禁用缓存）
```
