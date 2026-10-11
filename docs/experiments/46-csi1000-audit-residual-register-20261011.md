# 46 — CSI1000 Audit Residual Register（工作线 B，2026-10-11）

> **约束优先级**：[42 Audit Closure Register](42-csi1000-mvp-handoff-after-ledger-pass-20261011.md) > [45 B-line handoff](45-csi1000-audit-residual-strategy-decision-handoff-20261011.md) > 本定向诊断。只记载遗留风险，不重启已关闭历史审计，也不阻塞 [44 A-line](44-csi1000-mvp-forward-operations-handoff-20261011.md)。冻结旧数据、模型、参数、报告、信号一律只读。研究证据不等于真实成交或全 55 格独立账本认证。

## 1. 2026-10-11 读取基线 / 证据锚

- GitHub `main` HEAD（读取时）`9316751309e35e4906d1d544118cbed4d190001f`；PR #59 原 HEAD `d616cbd77be92f8f4d62e54744659f9c17630b1d`（本文写入后会前移；不要把这些值当永久 HEAD）。
- `docs/experiments/42-...` Git blob SHA `a3f1ac404df9cce419f86f8452574493fb8ceec0`；45 号 Git blob SHA `b1a9f8bdf2ad53dde40344f63f20e0da18080166`。
- 冻结运行代码 SHA `36aa200b8600d9ab8dd4dc3d0f241be1f332afac`（审计目录 `HEAD.txt`）；Stage-B 原结果 commit `7a2676397b0f8e6f69c0bffc98d1764647f644ac`，snapshot token `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`，provider fingerprint `cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5`。
- `csi1000_production_config.py` Git blob SHA `de3d8793505cf57d3e2d9c43b6df7d5a13718256`：`CANONICAL_PROFILE=stage_b_winner`，`CANONICAL_PAPER_LINEAGE=stage_b_winner_canonical`；winner candidate `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`，model_config SHA256 `0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88`；baseline candidate `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`，model_config SHA256 `793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3`。
- 审计 JSON `audit/evidence/stage_b_seven_halts_baseline_p00/csi1000-seven-halts-p00-20261010b.json` Git blob SHA `f4d4bba443367826f0a420692532f22e6f46cccb`；该文件**内容 SHA256** `522f496e40c055f8f5126b1de65e8b5af2f044788529e51892956f9deb3825f3`。仅 baseline phase0 的 424 日、1,700 冻结成交、9 个公告停牌估值，研究账本 `PASS_RESEARCH_ACCOUNTING_ONLY`；差异 `fail_fields=[]`。
- corrected 55 结果 `audit/evidence/stage_b_corrected_rerank/batch_result.json` Git blob SHA `668bc83c27551a2a7990abac4d93a5b0225e7818`，文件 SHA256（见 38 号文档）`ce0075f5d8c73325a12a4ecdf8fa4be856443a6fc1bac933f3b82d5c5035ff1c`；11 candidates×5 phases，55/55 frozen legacy 复现 + 55/55 fixed 模拟，独立 corrected 账本**并未**扩至其余 54 格。
- 注意：Git *blob* SHA1、文件内容 SHA256、模型配置 SHA256、git *commit* SHA 是不同命名空间，不可混用。读取既有 GitHub JSON 只验证存档内部一致性，不证明原始私有 provider 的每字节 SHA 或外部行情准确性。

## 2. Register（审计最终状态不擅自改变）

| issue_id | 42 原状态 → 本轮状态 | 机制 / 已见影响 | 风险级别；owner | 对 MVP 影响；定向重启条件 |
|---|---|---|---|---|
| AR-01 F1 held quote-union | CLOSED/FIXED → **CLOSED** | 冻结回放同一动态成员覆盖造成持仓报价脱落；corrected 采用执行报价静态并集 1307 codes；**不是**信号 PIT 股票池 | 历史 P1 已处置；A line 当前链路 | 不阻断；**仅当前**退指持仓失价/无法卖出同类缺陷复现时转 A line P0 |
| AR-02 baseline phase0 ledger | CLOSED/PASS_RESEARCH_ACCOUNTING_ONLY → **CLOSED** | 研究现金/仓位/费率/净值最大金额误差 < ¥0.01；不证明交易真实、OOS 或任何新晋级 | 历史 P1 已关闭；B line | 不阻断；仅冻结订单/账务规则/provider 身份变化重启针对性核对 |
| AR-03 nine suspension valuation marks | CLOSED/ACCOUNTING EVIDENCE ACCEPTED → **CLOSED** | 9 个仅公告支持的持仓缺价日用 prior held valid close；不逐日扩查 | 历史 P1 已关闭；B line | 不阻断；仅公告冲突/证据错误重开 |
| AR-04 four ST/reopen SELL | DEFERRED/NOT_CERTIFIED → **DEFERRED** | baseline phase0 四笔假定 full-fill 卖单；5% / 创业板 20% 涨跌幅限制与盘口/委托不可得；现金再投资路径与排名可能受影响，**不能量化为 0** | **P1**（历史研究解释），实盘认证 **P2**；B line | 研究 MVP 披露限制不阻断；**准备实盘自动下单**或**前向 paper 系统性无法成交**时依法重启市场执行核验 |
| AR-05 independent benchmark price | DEFERRED → **DEFERRED** | `bench` 来自存档 daily returns；存档共同 benchmark ≠ 独立价格认证；相对超额对指数口径敏感 | **P1**（对外收益表述），B line | Top20 不阻断；**正式对外发表独立认证相对收益**或 benchmark 异常时，以外部 source prices 触发 |
| AR-06 winner vs baseline | TRANSFERRED TO FORWARD RESEARCH → **TRANSFERRED** | 修正后 baseline rank 1 / winner rank 10，但本 tail 已用于研究与诊断，选择偏差；不等于 winner 当前 lineage 错误 | **P1**，B line → 前向评审 | canonical 不变；**不可回填的真正新前向样本达到 47 号协议门槛**再审议 |
| AR-07 historical full ST/IPO/limit/liquidity/impact | DEFERRED TO LIVE-TRADING READINESS → **DEFERRED** | 未有全市场逐单盘口、外部可成交、冲击成本或所有 PIT 与 IPO 核验 | **P2**；实盘 readiness owner | 不阻断研究 MVP；只在自动下单/实盘准备时全面认证 |
| AR-08 other 54 cells independent ledgers | CLOSED/NOT REQUIRED FOR MVP → **CLOSED** | 55/55 corrected 是模拟而非全 55 独立账本 | 历史 P1 已终止；B line | 不扩审；**具体系统性跨 cell 账务 bug**才定向重开 |
| AR-09 current PIT / label / as-of | TRANSFERRED TO MVP/HARD GATE → **TRANSFERRED** | 当日池、label 成熟/训练截止、防 lookahead 若出错会改变真实榜单 | **P0 if observed**；A line | 当日必须 fail-closed + 当日缺陷证据，不等历史账本结论 |
| AR-10 provider/calendar/cron/publication | TRANSFERRED TO MVP/HARD GATE → **TRANSFERRED** | 最新完整 bar、生产 cron、幂等和网站均非本次历史审计可证明 | **P0 if observed**；A line | 即时运维隔离，不以本报告宣布线上 PASS |
| AR-11 current ST/halt risk flags | TRANSFERRED TO MVP → **TRANSFERRED** | 当前可交易性未知应显式标注，不可宣传已成交 | **P0 if falsely advertised/affects publication**, otherwise P2；A line | 当日风控缺陷即时移交 |
| AR-12 upstream CSI500 PR #51 | CLOSED/SUPERSEDED → **CLOSED** | 非 CSI1000 门槛；不得声称 upstream qrun 已执行 | P2；未来独立研究 owner | 仅未来另立 upstream sanity 合同 |

**风险定义**：P0 是证实影响当前 Top20/数据泄漏/时钟/PIT/lineage 的**现时**问题；P1 是足以改变历史研究可信度的具体证据（但须尊重 42 号历史重启条件）；P2 是实盘准备阶段认证。任何 `DEFERRED` 不能标作 `PASS`。

## 3. AR-04：四笔 ST/复牌 SELL 的订单级核对（定向、不是成交认证）

来源：
- corrected baseline phase0 `audit/evidence/stage_b_corrected_rerank/23b92de05cf36c82_p00/fixed_diagnostic_decisions.json`，Git blob `11ca1ab5a1fbc4de2cd03007b471fdb4c2edb00e`，文件 SHA256 `ce7d37cdeb65b095114ad2ef16798b55958150a743692db8e3a292ee1badf8d7`。
- winner phase0 对照同名文件（目录 `4e908173705c76fe_p00`），blob `e06bfb63be69fbea9d905c7c041e47c46fe3ade7`，文件 SHA256 `4694e67bd1a1870707d02d0eaf0e9bb0b29533cb209707e894d648fdd2a11063`。
- 账本 JSON `cells[0].known_st_execution_review` 中 4 个监管公告 URL 与未认证状态（上方 SHA）。
- `direction=0` 为 SELL，`amount=deal_amount` 表示**模拟已填**，不是交易所实际成交；`amount` 为 adjusted-share units，**不可当 A 股自然股数直接乘原始行情价格**。

| date | ticker | limit risk / frozen SELL order index | adjusted qty (amount=deal_amount) | saved factor | winner phase0 同日同股 SELL |
|---|---|---|---:|---:|---|
| 2025-02-21 | SZ000488 | ST 5%，#0 | 3,767,049.5897504115 | 0.43896400928497314 | **无** |
| 2025-04-29 | SZ002214 | ST 5%，#0 | 3,288,724.962452481 | 0.20198145508766174 | **无** |
| 2025-05-06 | SH603398 | ST 5%，#0 | 2,873,594.246169154 | 0.15588606894016266 | **无** |
| 2025-05-06 | SZ300379 | 创业板 ST 20%，#1 | 5,534,865.47159786 | 0.2065725326538086 | **无** |

4 / 1,700 = **0.2353% 的冻结成交笔数**，只是数量占比，**不是 NAV 风险上限**；冻结决策无开盘价、逐档买盘量、排队情况或实际成交回报。不能从“模拟成交数量很少”推出价格/排名影响很小。四笔历史 sell 的委托窗及公告状态可复查，不对 9 个已关闭持仓估值逐日重审。

**预先限定的敏感性（尚未运行，缺真实同口径行情/盘口）**：四事件各作 0-fill/全部延期 1、3、5 个**有效交易日**对照；保持冻结原始订单/模型/参数/费用不动，未卖出则持仓继续按有效当日价格盯市，卖款不可提前复用、后续 BUY 需由现金与预注册的 pending/skip 规则约束，记录 NAV/现金/持仓/换手/成本及原排序指标 delta；同时对 winner phase0 同步使用相同执行限制。不得简单从收益上减一个武断 bp，亦不得把每日单笔差额相加伪造完整路径回放。确认有独立 `adjusted_open/close/factor/volume`、交易状态、原冻结对照和外部委托簿证据之前，`UNQUANTIFIED_PATH_DEPENDENCE`、`BLOCKED_MARKET_EXECUTION_NOT_CERTIFIED` 保持不变。

**负控已做**（基于 GitHub 两份冻结决策和账本 JSON 的结构/字符串检查，不是数据源外部认证）：四个 `(date,ticker)` 每个恰有一个 baseline SELL，winner 中均为零；错误 ticker 查询均为零；不存在重复四事件 key；ledger 四事件日期/代码/未认证状态逐一匹配；snapshot 和 provider fingerprint 在 corrected batch 与 ledger 中相同。不得将这些内部负控描述为实际盘口负控。

## 4. AR-05：benchmark 独立价源的零假阳性核对

- corrected 55 个 `phase_comparisons[].fixed_metrics.benchmark_total_return` 均为 **0.24072600816110556**；同一 batch 的两候选 phase0 数值一致，说明**内部引用序列口径一致**，不是外部价格对齐或证实。ledger `independent_benchmark_price_audit=BLOCKED_SAVED_BENCH_RETURNS_USED`。
- 独立价格尚**未取得并校验**：`external_daily_close_date/level + 上一交易日 close + index identity(中证1000 000852) + price-return/total-return 分类 + 来源 + 查询时间 + 时区 + 交易日对齐 + 内容 SHA256`。计算 424 个逐日 close-to-close returns 与冻结 report `bench` 逐日比较，容差 `max_abs_daily<=1e-6`、全 424 无缺/重/错位、首日基准前收盘存在；任意 source 与冻结 provider 相同不得标为 `INDEPENDENT`。
- 阴性测试规范：打乱两天日期、缺少前收盘、重复日期、换 total-return 指数、扰动一个 close、替换为同源保存的 returns，均必须拒绝认证。独立 price feed/原 Parquet 没有可核对的 SHA 之前，**严禁公布“外部独立认证的超额 CAGR 13.06%”**；研究报告只能注明相对**saved benchmark returns** 的历史模拟值。

## 5. AR-06：排名变化定位、归因范围与负控

| 同一 phase0（2025-01-02 至 2026-09-30） | winner 原冻结 → corrected | baseline 原冻结 → corrected |
|---|---:|---:|
| Sharpe（238 日、rf 0） | 1.276262 → **0.998878** | 1.122058 → **1.192139** |
| CAGR | 29.3178% → **23.4107%** | 23.0149% → **27.9686%** |
| 订单数 | 482 → **1,696** | 482 → **1,700** |
| mean daily turnover | 0.056751 → **0.201329** | 0.056408 → **0.202967** |
| 首个 NAV 分歧日 | 2025-06-30 | 2025-06-30 |
| 首个订单分歧日 | 2025-07-01 | 2025-07-01 |

- 同一 phase0 corrected baseline 相对 winner Sharpe +0.193261、CAGR +4.5579 percentage points；**不能**仅据该期或已经消费的五 phase 评价新的 OOS。
- 11 candidate 五 phase 在原始冻结报告/订单复现后，仅执行报价范围从动态指数成员改成静态 1307 code 联集（`static_quote_union_sha256=7d27c49ee7dcb2f4620c9a3aae7c620a1ecdc4890a18fc46b74cfcd468cec405`）。此为已证实的 **F1 execution/quote coverage mechanism**，不是重新拟合、改变预测信号或随机超参；原排序失效的直接证据是 55 个 paired replay + first-diff + 订单/换手变化。
- 冻结 Stage-B 原 winner rank 1 / baseline rank 5，corrected 诊断为 winner rank 10 / baseline rank 1。诊断 rank 按既有 `rank_stage_b_candidates`（worst-phase relative CAGR、median Sharpe 等）生成，未更换规则；**未证明**“仅 F1 导致所有差异”，ST/benchmark/PIT 未验证边界仍在。
- 负控：55/55 legacy 字节/订单重放 `PASS`；55/55 corrected；`training_performed=false`、`parameter_search_performed=false`、`promotion_authorized=false`；两个候选 phase0 **均到 2025-06-30 才开始 F1 所致 NAV 分歧**，而四笔 ST 日期在此之前——所以 ST 问题**不可**错误归因于 F1 修复。模拟结果内部 benchmark 一致，但不提供独立 benchmark 证书。
- 任何不同随机种子/未锁线程/训练版本、当期 PIT/label 可得性验证不足，应单列不确定性；当前固定配置和回放记录**不足以断言**这些机制均已排除。

## 6. 当期结论、交接与审计停止线

**`KEEP_CANONICAL_PENDING_FORWARD` / `INSUFFICIENT_FORWARD_EVIDENCE`**：当前无 2026-10-12 起真实、同时、不可回填双候选前向证据。现役 winner identity 保持，baseline 仅作为未来预冻结 shadow；本轮**没有观察到新的 A line 当前 P0 证据**，这不构成 A line 当期线上健康认证。无权限核验的原始 provider/独立指数价格/真实盘口明确标记未认证，不伪造数值或“完成影响量化”。

B line 仅在具有**具体可复现当期** PIT、label/as-of、报价、身份、日历导致错误发布的 P0 时，把 `date, artifact SHA256, provider snapshot, model id/hash, expected vs observed, fail-closed proof` 交 A line；历史 P1/P2 不阻挡 Top20。下一步固定判据见 [47](47-csi1000-forward-shadow-strategy-decision-protocol-20261011.md)。旧审计 JSON/55 回放、config 和历史信号均未编辑。

## 7. 新增可重复核验与官方 index-price 来源（不改变 DEFERRED）

**基准合约已逐字验证**：`csi1000_stage_b_core.py`（main Git blob `91f89641f075e1a5d1130ec0053d5953db8aeb8e`）指定 `BENCHMARK="SH000852"`，不是从项目标题臆测；CSIndex 官网页面标识 `000852` 为中证1000。独立价源的定向采集路径为中证指数公司 `https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000852&startDate=20241230&endDate=20260930`，这是 AKShare 的 `stock_zh_index_hist_csindex` 所封装的官方 `index-perf` 历史日行情接口。**本会话直接取数未成功**，无完整的 425 行外部可读价格供 424 日比对，故没有外部 benchmark PASS，更不能从第三方网站只见到末日行情而称全期认证。授权网络环境执行：

```bash
python -m audit.export_csi1000_official_index_closes --out-dir /tmp/csi1000-index-20261011
python -m audit.csi1000_residual_evidence \
  --frozen-report /readonly/baseline-phase0/fixed_diagnostic_report.parquet \
  --external-index /tmp/csi1000-index-20261011/000852_prices.csv \
  --output /tmp/csi1000-benchmark-residual.json
python -m unittest discover -s tests -p 'test_csi1000_residual_evidence.py' -v
python -m unittest discover -s tests -p 'test_export_csi1000_official_index_closes.py' -v
```

默认的四文件身份+ST+ranking 静态检查不需要私有 provider：`python -m audit.csi1000_residual_evidence --output /tmp/csi1000-residual.json`；导出器将官方响应 SHA256、CSV SHA256、来源 URL 和查询 UTC 时钟记录在单独 receipt，**不提交下载价表到公开仓库**。独立 benchmark 即使内部数值满足误差阈值，也保留 `PENDING_HUMAN_SOURCE_CERTIFICATION`，须核对来源授权、价格指数/全收益口径、精度与末日/初日前 close 的日历无误。

**已实做的非模拟静态核对**：通过连接的 GitHub 文件原文字节独立计算 SHA256，四份冻结 JSON（corrected batch、ledger、baseline phase0 decisions、winner phase0 decisions）均与固定 SHA256 **逐一相等（4/4）**；四笔 SELL、负控 wrong-ticker、winner-absence、唯一日期/代码 key、55 个已保存 benchmark 总回报相同和 snapshot/provider 双身份均一致。该结论不证明真实行情或委托成交。仓库新增 Python 合成负控测试供 CI/授权环境执行；**截至本次登记，未观察到这些新增 unittest 的运行结果，状态 `NOT_EXECUTED_IN_VERIFIED_RUNTIME`**，不得写作 `PASS_TESTS`。
