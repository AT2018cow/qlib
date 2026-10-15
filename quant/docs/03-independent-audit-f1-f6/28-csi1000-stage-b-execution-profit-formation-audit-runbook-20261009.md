# 28 — CSI1000 Stage-B 收益形成过程独立审计：实验环境只读操作手册（2026-10-09）

> **适用：PR #44 后续 Gate D / 原始 provider 日历完整性。** 本文件是待执行的实验审计计划，**不是已执行检查的证据**。目前原始 `report.parquet` 已独立核对 10/10 SHA256 与保存财务指标，逐日 **内部**账户恒等式通过；冻结 provider 日历、T-close/T+1-open 成交真实性、未来信息与 point-in-time universe 尚未独立验证。因此数值复算 **PASS**，`execution_integrity=BLOCKED`、`overall=BLOCKED`、未来收益 **INCONCLUSIVE**。详见 [27](27-csi1000-stage-b-raw-report-independent-recheck-20261009.md)。

## 0. 固定边界：不需要立即在 Modal 重算模型

- **不运行** Stage-A/B 选参、LightGBM 重新训练、全部 11 个候选重跑、生产 `modal deploy`、历史结果改写；不得用已消耗的 2025-01-02～2026-09-30 tail 调参。
- **可以**使用已有实验环境 / Volume 的授权**只读**访问，取回必要原始证据；不执行 `vol.commit()`、写入 Volume、删除/迁移历史证据或触碰 production/paper/website。
- **首轮 1 个 winner phase 0，CPU/文件 I/O 即可**；先排查最有信息量的交易日，确认方法正确后再扩到全部 424 个交易日和其他 winner/baseline phases。
- 必须由**不同于原 Qlib 回测器的实现**重建必要的订单、现金、持仓、收益；再次调用同一原始 Qlib 回测函数得出同一条收益曲线，**不能**算独立执行验证。
- 结果存为**新版本审计证据**，原 `report.parquet` / `signal.parquet` / `decisions.json` / 汇总 JSON 只读。若证据未找到，逐项 `BLOCKED`，不得因为 report 自洽就放行。

## 1. 冻结 provenance 与首轮文件定位

| 项目 | 固定值 |
|---|---|
| 原始 Stage-B full-result Git commit | `7a2676397b0f8e6f69c0bffc98d1764647f644ac` |
| 原始结果 JSON | `results/csi1000_stage_b/stage_b_full_51756897fc752304.json` |
| 十份原始 report 的 GitHub 导出 commit | `5418956d9858663613d9e9eaedf614d97da019c1` |
| Volume snapshot | `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1` |
| winner candidate ID | `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2` |
| baseline candidate ID | `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785` |
| execution | T-close signal → T+1-open; Top20/Drop2; 2025-01-02～2026-09-30 / 424 sessions; initial cash CNY 100,000,000 |

先从 full JSON 中的 `ranked_candidates[].phase_results[]` **按 candidate ID 和 phase=0 查找元数据，使用元数据的绝对路径，不要自行合成**。

Winner phase0 的冻结元数据：

| Artifact | 原始 Volume 中应有的相对文件 | 冻结 SHA256 | 保存数量 |
|---|---|---|---:|
| `report_artifact` | `phases/<winner>/phase00/report.parquet` | `649b26fec1760abeec9923adaa68db7240bfa9fe9f4bb1488e573f9373d39da0` | 424 日期 |
| `signal_artifact` | `phases/<winner>/phase00/signal.parquet` | `189d06cce7cdc52b1908439fb18fd9e3f1b8dcc8875a87bf5b6ec75d82e0278e` | 423876 评分 |
| `decision_artifact` | `phases/<winner>/phase00/decisions.json` | `8675619a028db77bad613aeede11b168ea4c40ba95f6be5e0a1039252da25db9` | 424 decisions / 482 orders |

三者完整的文件路径均以 `/vol/csi1000_stage_b/<snapshot>/` 开头。原数据是**同一个已冻结历史实验**的资产。Baseline phase0 **不是**普通 `phases/<baseline>/phase00`，而是 `_preflight/.../repeat_b/phases/<baseline>/phase00`；后续扩展时务必按其独立 JSON 指针获取，禁止伪造普通路径。

### 实验环境最小只读检查（不启动任何训练）

以下 Python 段只读取现有目录和冻结 JSON；可以在**已挂载原始只读证据的实验容器**里执行。它不负责配置或调用 Modal，也不下载/写入 Volume：

```python
from pathlib import Path
import hashlib
import json

result_path = Path("results/csi1000_stage_b/stage_b_full_51756897fc752304.json")
payload = json.loads(result_path.read_text())
winner_id = "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2"
winner = next(c for c in payload["ranked_candidates"] if c["candidate_id"] == winner_id)
phase = next(x for x in winner["phase_results"] if x["phase"] == 0)

for key in ("report_artifact", "signal_artifact", "decision_artifact"):
    meta = phase[key]
    source = Path(meta["path"])
    if not source.is_file():
        raise FileNotFoundError(f"{key}: {source}")
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    assert actual == meta["sha256"], f"{key}: SHA256 mismatch"
    print(key, "PASS", source.name, actual, meta.get("rows"),
          meta.get("decision_count"), meta.get("order_count"))
```

这仅证明原始字节身份；**不能**自行宣布收益可成交。若授权环境使用 `/root/qlib` 作为仓库目录，先切到其包含该结果 JSON 的 checkout；不要更改上面的冻结标识。若需要从 Modal Volume 导出到本地，使用实验环境中现有的授权**只读下载**方式，并在本地再次 SHA256；不能为了访问数据而运行 `stage_b_phase_worker`。

## 2. 按最小必要原则导出数据（分两批）

**A. 立即必需：** phase0 的 `signal.parquet`、`decisions.json`、其 `result.json`（若可访问）及原 provider 的 **`/vol/cn_data/calendars/day.txt`**。已在 GitHub 的原始 `report.parquet` 可直接使用，但仍要关联同一 SHA256/snapshot。记录 Volume 数据集 fingerprint、各文件 SHA256、文件字节数和来源位置，核对 calendar 与全部 424 条 report 日期完全相符，不能仅对比十份 report 之间的一致性。

**B. 按 A 中交易股票和审计日期提取 provider 行情：** 此交易池的 T-1 收盘、T 开盘、T 收盘、成交量/成交状态、当日及历史 `$factor`、当日复权/除权事件；股票的板块、上市与首个交易日、历史 ST/风险警示、交易涨跌停价/规则，交易所日历和 **point-in-time** CSI1000 成分。至少覆盖每笔订单/成交及前一交易日，支持完整 424 日持仓日终估值。如果原始 provider 不包含历史 ST/PIT 成分或无实际成交容量信息，则标记该判断 `BLOCKED/INCONCLUSIVE`，不能用最新 ST 名单或静态股票池替代当年的状态。

**最小可交付 manifest**（以下为字段规范，不是预填的核验结果）：

```json
{
  "audit_schema": "csi1000_stage_b_execution_evidence_v1",
  "frozen_result_commit": "7a2676397b0f8e6f69c0bffc98d1764647f644ac",
  "snapshot_token": "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1",
  "candidate_id": "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
  "phase": 0,
  "provider_fingerprint": null,
  "calendar": {"sha256": null, "source": "original cn_data/calendars/day.txt"},
  "artifacts": {
    "signal": {"expected_sha256": "189d06cce7cdc52b1908439fb18fd9e3f1b8dcc8875a87bf5b6ec75d82e0278e", "actual_sha256": null, "rows": null},
    "decisions": {"expected_sha256": "8675619a028db77bad613aeede11b168ea4c40ba95f6be5e0a1039252da25db9", "actual_sha256": null, "decisions": null, "orders": null},
    "report": {"expected_sha256": "649b26fec1760abeec9923adaa68db7240bfa9fe9f4bb1488e573f9373d39da0", "actual_sha256": null, "rows": null}
  },
  "price_snapshot": {"source": null, "sha256": null, "field_definitions": null},
  "listing_st_pit_source": null,
  "audit_asof_utc": null
}
```

只将允许公开/再分发的最小证据和派生统计提交 GitHub；**不要将非公开 licensed provider 数据、生产凭据、私有 profile/workspace 名称、个人路径和大体积全市场行情原件推送公共仓库**。必要时把明细仅保存在实验环境，并在 PR 留可验证哈希、脱敏失败样例和可复现实验步骤。

## 3. 独立收益路径审计 — 推荐执行顺序

### D0：证据身份与缺日门禁

- SHA256 原始字节必须与**冻结 result JSON** 中的 `sha256` 完全一致（不是只看上传 manifest 的 `match:true`）。
- `signal.parquet` 应解为一列 `score`，由 time/instrument MultiIndex 标识；校验索引、非有限值、排序、重复记录。`decisions.json` 是 `topk_decisions_v1`、由 424 个决策对象和 482 个订单构成。特别核验每个订单的 `stock_id/direction/amount/deal_amount/factor/start_time/end_time` 及决策时间；不要将 `deal_amount` 当作独立成交价格或已按券商逐笔核算费用。
- 逐个比较真实 provider `calendars/day.txt` 的预期执行日与 report 的 424 天、decision 的执行日，发现漏日/移位/节假日填充要量化。
- 失败停止相关 PASS 宣告；缺少原始 provider 日历则 calendar 门禁 **BLOCKED**，不允许以 424 条记录取代。

### D1：时间因果链（T / T+1、重训）

- 对每个执行日 `E`，从冻结 provider 日历得到**前一交易日** `S`，核对代码 `deterministic_strategy.generate_trade_decision()` 使用 `shift=1`、决策 `start_time/end_time = E`、订单也是 `E`。
- 每个决策中使用的股票必须从 `signal` 在 `S` 的评分独立推出其排序候选；验证 Top20/Drop2、并列分数排序、持仓股卖出顺序及不能成交/持仓天数约束。不应将 `E` 当日收盘评分作为开盘买入决策。
- 对每个 `chunk_predictions` 的 `retrain_asof`、`signal_end` 和训练/验证/处理器 fit 的 as-of，检查 20-session forward label 已成熟且正确 purge；不能只看 Python 中调用了 `last_matured_sample` 就签字。需要对应训练窗口日期/target 依赖与实际产生时间证据。**任何未来标签或未来修订成分泄漏是重大 FAIL**。
- 无历史可得性记录时明确 `INCONCLUSIVE` 或 `BLOCKED`，禁止对无法证明的字段用假定填充。

### D2：T+1 开盘真实可成交性与价格

冻结调用链：`csi1000_stage_b._run_signal_backtest()` → `research_exchange()` → `BoardAwareExchange`。与真实行情独立对照：

- 成交口径 `deal_price=open`；买佣金率 `0.0005`，卖佣金率 `0.0015`，最小费用 CNY 5（**具体最小费用作用域须以冻结 Qlib 代码为准**）；区分订单数量、`deal_amount`、复权后持仓股数、交易币值、费用与真实未成交订单。
- 根据**执行日开盘可知**的报价和上一交易日价格、复权及交易所 tick/涨跌停规则检查买卖限制，不使用当天收盘价或 `$change` 决定是否准许当天开盘成交。单独审计涨跌停触价可否有实际对手盘，不能仅把触价当成必然成交。
- 识别停牌、未上市、ST/退市整理、除权除息、价格/因子异常、整手交易单位及开盘流动性；其中两项需要专项记录：`_run_signal_backtest` 调用 `research_exchange` 时未显式提供历史 `st_symbols`，`BoardAwareExchange._update_limit` 源代码以执行日 `$close` 的缺失值来标记 suspension。**这只是静态风险，不是已证明的虚假成交或收益错误**；必须按实际订单及当日状态判定。
- 记录实际 `price_source, adjusted_open, factor, raw_open, previous_raw_close, trade_limit, is_tradable_at_open, deal_amount, execution_price, economic_notional, reason`。要确定复权因子约定才能把“归一化复权价”转换成实际人民币价，不允许直接乘错因子。
- `decisions.json` 本身**未包含逐笔成交价格、成交费与完整 broker fills**。无法从原 artifacts直接证实券商真实可成交性；独立验证的是“研究模拟能否符合当时规则”，市场冲击/盘口数据不足时须列局限。

### D3：第二套独立现金、仓位及收益账本

以初始 CNY 100m、首日 `account/(100m)-1` 为起点；按冻结订单顺序、方向、正确成交量/lot、实际开盘价与费率逐笔计价（注意先卖出更新现金，再买入）。逐日现金恒等式：

```text
cash_close = cash_previous + sum(sell_notional) - sum(buy_notional) - fees
positions_close = positions_previous + bought_shares - sold_shares
value_close = sum(positions_close * close_price_under_consistent_factor_convention)
account_close = cash_close + value_close
net_return = account_close / account_previous - 1
```

- 严格执行持仓成本/复权换股、公司行为、无法成交订单不入账、未成交挂单不计费、现金约束与负仓位等检查；对同日分红/因子变化需参照当日原始 provider 标注。不要同时将交易成本从 `account` 和 `return-cost` 扣两次。
- 把**重新从价格+订单导出的** `cash/value/account`、每笔费用、日 `cost`/ `turnover`、前后持仓，逐日和已保存 report 对照。既有 `account= cash + value` 一致并不表示重新建的账本通过。
- **验收阈值预先固定：**账户/现金/估值/累计费用的绝对 CNY 差异原则上不超过 **0.01 元**（大于时必须列出浮点/复权/记录单位导致的确定性差异及每笔证据）；逐日报告净收益与独立账户收益最大绝对差异不超过 **1e-10**；价格用原始输入精度并符合 tick/交易单位定义。发现差异不得事后放宽容差；无法获得足够字段算账即标 **BLOCKED**，不把原报告自洽当作新的独立核算。

### D4：优先异常日与全段扩展

先生成**事前固定**的样本日清单（只作为诊断，不能用于策略挑选）：

1. 首个及最后一个执行日；
2. 每个 `retrain_asof` 前后一日和 20-session 标签成熟边界；
3. 按已有报告绝对收益选择最大正收益 10 天、最大负收益 10 天、换手率最大 10 天；
4. 全部涉及涨跌停/停牌/ST/除权异常的已有订单日期；如太多，完整统计数量，先挑出最大名义金额的前 10 例；
5. 订单成交 `deal_amount/amount` 出现非预期、缺价、整手舍入或 cash 变负的日期。

先做样本逐单检查，若存在无效成交或净值差异，立刻对异常日及其之后的持仓路径做**全段**核算。即使样本无异常，**整体 execution PASS 仍需对全部 424 日的实际订单、账务连续性与交易可得性作完整覆盖**；事件抽样 PASS 只能写 `sampled_execution=PASS`，不能写 `execution_integrity=PASS`。扩展到其他九个 phase 时保留各自的候选 ID、指针、时间对齐和冻结哈希。

## 4. 什么情况下才动用 Modal 重放？

| Gate | 何时需要 | 最小动作 | 禁止误判 |
|---|---|---|---|
| 原始证据导出 | GitHub 没有 signal/decision/provider 原件 | 授权实验环境 read-only download 或一次性小 CPU 取数；校验 SHA | **不**启动 stage worker/LightGBM |
| 交易日历差异 | 发现 424 日期与原 provider 日历不符 | 核验原 provider snapshot/fingerprint、是否有填补或缺行 | 不能改日历来“重新算高 Sharpe” |
| 决策/成交核对差异 | 独立依据 signal/行情无法复现少量订单 | 对指定日期、指定候选，以冻结 signal 在隔离 CPU 容器重放 Qlib **仅作为诊断**，对照第二实现 | 同一回测器重放一致不证明正确 |
| 无法解释的账本差异 | 独立现金/估值/费用账与 report 不符 | 只读跟踪受影响日的详细 order/exchange 日志，定位最早分歧 | 不重写原 report |
| 标签/成分穿越 | 发现未来信息依赖线索 | 小样本 point-in-time 训练输入追踪并保存最小复现 | 不用 tail 重新筛参 |
| 大规模全模型重训 | 上述操作不能识别确定缺陷且有明确证据价值时 | **必须单独提出成本/必要性并获用户明确批准** | 当前 **不授权** |

费用/资源上限建议：Tier 0 从 GitHub 用 CPU 核验 0 GPU；Tier 1 一次 Volume 小规模只读导出 + CPU 解析（只导出 phase0 相关 424 日期/交易股票，不复制全市场）; Tier 2 异常日定点回放才使用隔离 Modal CPU；不触发 GPU 训练。**由于原 provider 字节量未知，不承诺具体执行费用。**

## 5. 提交实验审计结果时的固定格式

新增文件，不能覆盖 25/26/27 或冻结原始文件。下列内容可分公有脱敏摘要与实验环境中的原始明细：

- `execution_export_manifest.json`：固定 git SHA/snapshot/provider fingerprint、各原始文件哈希/行数/来源、市场规则/PIT 资料是否可用、数据版本时间；区分 `expected_sha256`、本次亲自测的 `actual_sha256`。
- `calendar_compare.csv`：原 provider 交易日、report 日期、decision 日期、前一 signal 日期、缺失/多余日期计数。
- `decision_vs_signal.csv`：执行日期、信号日期、标的、评分排序、当前持仓、下单方向、订单及是否符合 Top20/Drop2。
- `execution_orders.csv`：date, stock, side, requested_qty, filled_qty, open_adj, factor, open_raw, limit_price, tradable, fee_cny, turnover_cny, discrepancy_reason。
- `account_rebuild_daily.csv`：date, begin_cash, begin_positions_value, buys, sells, fees, close_cash, close_value, reconstructed_account, frozen_account, absolute_diff, reproduced_daily_return。
- `execution_audit_summary.json` 与简短报告：按问题分类输出影响日数、订单数、名义价值、估计收益/Sharpe 方向和**证据链接**；`metric_formula`、`daily_account_internal`、`independent_execution_account`、`execution_integrity`、`overall` 分别 `PASS/FAIL/BLOCKED/INCONCLUSIVE`。

**判定规则：**

- **PASS（仅已验证的局部）：** 原报告 SHA、保存指标、内部账务已通过；本轮新增外部 calendar 必须精确匹配，signal 及 decisions 必须与冻结 sha 一致且严格时序；全覆盖独立成交/费用/净值对照无无法解释的重大差异、同日数据可得性及训练标签的 as-of 有证据，才可把 `execution_integrity` 与 `overall` 升为 PASS。
- **FAIL：** 用确凿的交易样本、原始行情、原始代码版本和最小复现证明不能成交、未来泄漏或重大账户偏差；量化影响范围及原结论可能变化，先披露，再另提修复 PR，**不得自行改写生产**。
- **BLOCKED：** 关键原始 calendar/行情/成交/信号/权限缺失，不能独立判断；**INCONCLUSIVE：** 证据已有但相互冲突或不足以证明方向/幅度，例如无法模拟盘口成交容量。
- 因五个 phase 均沿用同一已经消费的 tail，**即使历史审计整体 PASS，也不能得出未来 Sharpe、长期可复制收益或投资保证**。

## 6. 给实验环境执行者的勾选清单

- [ ] 仍使用同一个冻结 snapshot、result JSON 及源代码/数据 fingerprint；实验期间不写 Modal Volume、不触及生产。
- [ ] Winner phase0 的 signal + decision + report 逐一**实测字节 SHA256**；原始 provider calendar 及足够 market data 的来源、日期、hash 已留档。
- [ ] 日历精确比较：报告、decision 和前一日 signal 相互对齐；没有缺失的损失日或无解释交易日。
- [ ] 424 decisions / 482 orders 与原件逐字段核对，重训边界及 20-session label maturation/PIT 全部有明确证据。
- [ ] 抽样首尾/极端收益/极端换手/重训/涨跌停与停牌；每笔独立核对开盘规则、ST、价格、复权、整手、费用和未成交。
- [ ] 独立从订单+价格全覆盖重建 424 日现金、仓位与账户收益，输出首次发生差异的日期及影响路径。
- [ ] 不把同一个 Qlib 重放结果当作“第二套实现”；不把抽样 PASS 冒充全覆盖 PASS。
- [ ] 后续按 winner 4/6/10/15 和 baseline 0/4/6/10/15 扩大覆盖，特别注意 baseline phase0 的 preflight repeat_b 原始路径。
- [ ] 仅提交可公开的派生结果与脱敏证据；不上传凭据、私有 profile、licensed 原始全市场行情。
- [ ] 更新 Gate 状态及证据、指出仍未核实风险；必要时用 **独立修复 PR** 报告真正被证明的 bug。
