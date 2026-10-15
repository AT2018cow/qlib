# 24 — 独立审计交接：核实 CSI1000 回测数据、Sharpe 与真实执行口径（2026-10-09）

> **这是一条独立的“质疑/核算”工作线，不是重新优化模型的授权。**
> 核验结论目前为 **PENDING — 尚未取得逐日原始 report 后独立复核**。
> STAR/ChiNext 研究由 [22](22-handoff-star-chinext-research-20261008.md) 负责；网页由 [23](23-web-ui-handoff-20261008.md) 负责。
> 此文档不能被解释为 CSI1000 Stage-B 已证实算错，也不能被解释为 Sharpe 已通过新的独立验证。

## 1. 任务目标和当前基线

- 仓库：`AT2018cow/qlib`。本交接核对的 `main` 参考 SHA：`3f6475fbdd1ef67ff4f6875dbcf317d97077b81d`（2026-10-09 检查时）；开始工作时必须先重新核对最新 `main`。
- 首要目的：核实 Stage-B winner 的 **Sharpe、收益率、波动率、IR、回撤、交易成本** 是否由正确的逐日交易/账户记录产生，尤其是否被时间对齐、年化方法、遗漏日期、成本或未来数据抬高。
- 首先回答两个彼此独立的问题：**A. 给定报告中的逐日收益，指标计算是否正确？ B. 逐日收益本身是否来自合法、真实的回测账户与 T/T+1 执行？** A 通过不能替代 B。
- 不修改 CSI1000 模型、参数、候选排名、Stage-B 留出区间、生产 cron、paper state 或信号历史文件；只有发现有证据支持的实现/数据完整性缺陷，才提出独立修复 PR。
- Stage-B confirmation tail `2025-01-02` 至 `2026-09-30` 已被使用。**审计可利用旧数据找错误，不得利用核验发现继续挑选更赚钱参数。**

## 2. 冻结的证据与可疑点：从事实出发

Stage-B 完整结果来自固定 result commit `7a2676397b0f8e6f69c0bffc98d1764647f644ac`，文件：

```text
results/csi1000_stage_b/stage_b_full_51756897fc752304.json
```

Winner candidate：`4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`。
冻结 snapshot：`51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`。

| Stage-B phase | Sharpe（保存值） | 年化波动率（保存值） | 日均净收益（保存值） | 策略 CAGR | Relative excess CAGR |
|---|---:|---:|---:|---:|---:|
| 0 | 1.276262 | 0.214666 | 0.00115113 | 29.3178% | 14.2516% |
| 4 | 1.334093 | 0.210544 | 0.00118019 | 30.3561% | 15.1689% |
| 6 | 1.109734 | 0.205029 | 0.00095600 | 23.5523% | 9.1578% |
| 10 | 1.395196 | 0.202383 | 0.00118640 | 30.7289% | 15.4982% |
| 15 | 1.177063 | 0.201244 | 0.00099528 | 24.8637% | 10.3164% |

- 每个 phase 保存 `n_days=424`、`account_return_max_error=0`；Phase0 保存策略 total return `+56.4702%`、benchmark total `+24.0726%`。
- 这些数字**并非独立证明**：日均收益 × 交易日数 / 年化波动率与 Sharpe 相近，只说明汇总之间大致自洽。
- 五个 phase 共享同一历史区间，是重训日历扰动，不是五个互相独立的 OOS 市场周期。
- 不要先假定“1.1–1.4 的 Sharpe 必然过高”；也不能因其数值处于常见范围就免除核验。

## 3. 当前仓库真实指标定义（先锁定口径）

源代码：`portfolio_performance.py`，metrics version：`portfolio_compound_v1`。

```text
annual_sessions = 238                     # 年化 Sharpe / vol / IR
rf_annual       = 0.0                     # 默认无风险利率
r_t             = report['return'] - report['cost']
b_t             = report['bench']
r_f,daily       = (1 + rf_annual)**(1 / 238) - 1
Sharpe          = mean(r_t - r_f,daily) / std(r_t - r_f,daily, ddof=1) * sqrt(238)
IR              = mean(r_t - b_t) / std(r_t - b_t, ddof=1) * sqrt(238)
annual_vol      = std(r_t, ddof=1) * sqrt(238)
account_nav     = report['account'] / initial_cash
initial_cash    = 100,000,000（相关 Stage-B 调用）
benchmark_nav   = cumprod(1 + b_t)
relative_nav    = account_nav / benchmark_nav
CAGR            = final_nav ** (1 / elapsed_calendar_years) - 1
```

关键实现细节：

- `aligned = report[['return','cost','bench']].astype(float).dropna()` 会删除任何一列缺失的行。**必须审计被删除的日期、与 424 个交易日/预期 calendar 的一致性及其偏差。**
- `account_return_max_error` 对比账户价值 `pct_change()` 和 `(return-cost)`；它验证的是这两个记录彼此一致，不是收益的真实可执行性或 Sharpe 样本正确性。
- 相对超额 **CAGR** 是 NAV 比值年化，不等同于策略 Sharpe 或 IR；也不应把总 CAGR 与旧模型的超额 CAGR 直接比较。
- 年化波动率使用 238 交易日，CAGR 使用真实日历时间（365.2425 days/year）；这种口径需明确披露，和 252 交易日年化口径做敏感性对照，但不能把“使用 238”未经比较就视为 bug。
- `rf=0` 只是当前定义。应在主结果忠实复现后，再额外给出不同合理 rf 和年化因子的敏感性表，不得静默修改官方口径。

## 4. 审计必须取得什么原始证据

GitHub 的结果 JSON 有逐 phase 的 `phase_results[].report_artifact` 元数据：`path`、`sha256`、`content_sha256`、`rows=424` 和 columns。
真实逐日报告是 **`report.parquet`**，原始存储在对应 Stage-B research Volume / artifact snapshot 中；不能把汇总 JSON 当成逐日报告。

优先只读获取如下 artifact：

1. winner 5 个 phases（0、4、6、10、15）各自的原始 `report.parquet`；
2. baseline 5 phases 的原始 `report.parquet`（同口径对照，最少先抽 phase0）；
3. 与每个 report 对应的 signal/decision artifacts、manifest、benchmark series 与必要 provider/calendar 元数据；
4. 原始 runner/config/hash 及 audit records，确认 provenance。

**取数第一关**：以 manifest 的期望 SHA256 检查每个文件的实际 bytes；校验 rows/columns、日期索引和 snapshot。
只需要访问原始 artifacts；**不要为“复核指标”重新训练几十个模型**。若文件丢失或无法读取，明确标记 BLOCKED，并记录哪个 source 缺失；不能凭已有 metric summary 认定 PASS。

## 5. 独立核算步骤（不要调用同一个 portfolio_performance 函数做自证）

### Gate A — 原始报告与交易日历完整性

- 从原始 Parquet 载入 `account/return/cost/bench/cash/value/turnover`，记录每列的缺失、NaN/Inf、min/max、异常量纲与日期重复情况。
- 对比 `2025-01-02..2026-09-30` 的计划交易日历、424 个 execution sessions 和 424 条 report；识别任何缺日、错日、休市日、首尾不一致或被 `dropna()` 丢弃的行。
- 检查报告 index 是否已按日排序、是否有中间插值/前向填充/多账户拼接，现金/资产市值是否从未非法为负或凭空增长。
- 报告哈希、日期、schema、数量不一致：**FAIL/BLOCKED**，不得继续宣称 Sharpe 通过。

### Gate B — 自主编写的“第二套”指标计算

- 从 `account` 和 100m 初始值独立计算 `r_account,t = A_t/A_(t-1)-1`（首日 `A_0/initial_cash - 1`）。
- 单独从 `return-cost` 构造 `r_report,t`，逐交易日对比两套 net returns、尤其首日/换仓日/大波动日；报告最大绝对误差和所有差异。
- 只用独立计算代码（不 import `portfolio_performance`、不调用 Stage-B audit/rank helper）重算算术均值、样本方差（ddof=1）、`sqrt(238)` Sharpe、IR、年化波动率。
- 从每日 NAV 独立重算复利 total return、按实际日期的 CAGR、MDD（包含初始 NAV=1）、benchmark total/CAGR 和 relative NAV/CAGR。
- 分别给出 Qlib report net-return Sharpe 和 account-derived Sharpe，明确二者一致的前提及每 phase 差值。
- 对全部 winner 5 phases 和 baseline 5 phases 输出对照明细；验证源 result JSON 里保存的每个指标，而不是只重算中位数。
- 精确的无四舍五入结果和来源采用可复核 CSV/JSON；与保存的 6 位数指标比较时需按原始报告和 rounding 区分容差。

### Gate C — Sharpe 稳健性与不合理“变高”排查

- 用同一条 daily returns 额外给出：238 与 252 年化、rf=0 与合理非零 rf、ddof=0 与 ddof=1 的 **sensitivity only**；官方定义仍是 238/rf0/ddof1。
- 按执行年/半年或可解释的完整时间块展示 Sharpe/日均收益/波动率、极端获利日贡献；不能拿短片段的高 Sharpe 当长期性能。
- 检查涨跌停、一字板、退市/停牌/上市首日、缺价数据处理是否会使极端亏损日丢失而产生偏差。
- 核查复权价格、benchmark 对齐、利息/融资/无风险假设是否造成 Sharpe、波动率或收益口径偏差。
- 看高 Sharpe 是否只是策略随市场上涨（strategy Sharpe）而非稳定 alpha：同时展示 `IR`、benchmark Sharpe 和 beta/correlation（后两项可作为新增**诊断指标**，不能用于重新挑选模型）。

### Gate D — 逐日账户收益是否真实可由策略执行产生

- 以上 Gates A–C 只能确认 report 内的指标正确，仍需抽样或独立回放交易决策（如发现异常则深入）。
- 从冻结 signal → T close、订单 → T+1 open、Top20/Drop2、cost/slippage、board/date-aware 涨跌停与不可成交处理独立检查；避免使用 T+1 close 信息决策 T+1 open 订单。
- 逐笔抽查至少首日、模型重训边界、最大收益日、最大损失日、最大换手日、涨跌停/停牌日，验证 open/close、持仓、现金、费用、无法成交订单的记账归因。
- 另验 dataset 的目标 20 日前瞻标签成熟度与 purge (20 sessions)、训练时点和 universe 历史成分，排除幸存者偏差与未来泄漏。
- 若存在不可解释账户收益、成交不可复核或标签穿越，即使数字算式吻合，整体结论仍 **FAIL / INCONCLUSIVE**。

## 6. 明确的交付物与验收级别

要求产出一份 versioned 审计报告及机器可复算的最小验证工具。至少包括：

- **Evidence manifest**：git SHA、snapshot、candidate、phase、report 原始路径/哈希、数据日期、代码版本。
- **Daily reconciliation**：每日日期/account/report net return/benchmark/fees、missing/invalid rows、异常天数、差异样本；不在 public web 暴露私有运行标识。
- **Phase matrix**：5 winner + 5 baseline phases 的官方值 vs 独立复算值、绝对误差、敏感性。
- **Execution checks**：样本日的信号/订单/成交/费用/可成交限制是否符合 T/T+1 合同；若没有底层原始数据注明缺口。
- **Risk assessment**：把问题分成指标公式错误、原始账户路径错误、执行/训练泄漏、样本期/选择偏差、单纯口径差异。
- **Verdict**：`PASS / FAIL / BLOCKED / INCONCLUSIVE`。至少分别报告 `metric_formula`, `daily_account`, `execution_integrity`, `overall`，附证据、原因、后续建议。

推荐判断标准：

- **PASS（指标计算）**：原始文件哈希正确；交易日样本完整或缺口经解释；逐日账户与净收益核对；所有 phase Sharpe/IR/vol/CAGR/MDD 与冻结结果在预先声明的计算/舍入容差内一致。
- **PASS（整体）**：在指标 PASS 之外，执行账务/未来数据/benchmark/费用和抽样交易无尚未解决的关键缺陷。
- **BLOCKED/INCONCLUSIVE**：证据或关键访问权限缺失、执行路径无法独立核验。不能因为既有 `account_return_max_error=0` 自动放行。
- **FAIL**：发现重大错误时以最小复现、受影响 phase/结果边界和修正前后差异证明；先告知用户影响，**不擅自重训、更新生产或修改已消费 Stage-B 选择规则**。

## 7. 资源成本、工作流与其他两条对话的边界

建议分级执行，避免前期高算力：

1. **静态核查**：读取汇总 JSON、源代码与哈希，建立证据列表，零重训练。
2. **原始 report 核算**：只读 Parquet、脚本独立重算，少量 CPU，先 winner phase0，再全部 winner/baseline。
3. **只有发现问题时**，才做针对性执行重放 / 模型时间泄漏调查；大规模重新训练必须明确解释收益、成本与必要性并获得用户同意。

其他对话职责：

- [22：STAR/ChiNext 研究](22-handoff-star-chinext-research-20261008.md) 不使用本审计来顺手重新调 CSI1000。
- [23：网页设计](23-web-ui-handoff-20261008.md) 不改变 Sharpe 展示口径或官网 Stage-B 数字，除非审计结论明确要求更正。
- 本任务优先新增独立 `audit` 脚本、测试、报告；涉及 `portfolio_performance.py`、`modal_qlib_cn_a10g.py`、`csi1000_production_config.py`、`website/**` 或 signal artifacts 的任何修改须先说明交叉影响并单独提 PR。
- 所有操作从最新 `main` 创建独立工作分支；只读访问实验 Volume，不重写、删除或迁移历史 artifacts；不提交 token、凭据、私有 workspace/profile 信息。

## 8. 推荐第一轮行动

1. 重新确认最新 `main`、冻结 result commit、`portfolio_performance.py` 的 Sharpe 实现与 Stage-B runner 的调用参数。
2. 读取 winner phase0 的 `report_artifact` 指针，**确认原始 `report.parquet` 可取、SHA256 可验证**。
3. 提供 10–20 行独立计算伪代码/最小脚本规划、预定容差与审计检查表，再运行低成本核算。
4. 独立复算 winner phase0，列出 `n_days`、daily mean/std、Sharpe、account NAV、CAGR、MDD 的原始值与保存值对照。
5. 确认脚本可信后扩展到 5 winner + 5 baseline phases，并继续执行 Gate C/D。

## 9. 新对话首条 Prompt（可复制）

> 请在 `AT2018cow/qlib` 中开展一项**独立的 CSI1000 Stage-B 回测财务与 Sharpe 比率审计**。先完整读取 `docs/experiments/24-csi1000-independent-sharpe-audit-handoff-20261009.md`、`AGENTS.md`、`portfolio_performance.py`、冻结的 Stage-B full result JSON 与 runner/audit 代码，并核实最新 `main`。我怀疑 Sharpe 和回测收益可能被计算或执行口径错误抬高。你的任务是**主动寻找错误，而不是为旧结论背书**。第一步确认原始 `report.parquet`（尤其 winner phase0）是否能从 Stage-B artifact snapshot 只读取得且哈希正确；如果不能，请列清 blocker。之后**禁止调用现有 portfolio_performance 函数当作独立证明**，从逐日账户、`return-cost`、benchmark、费用和日历自主重算 Sharpe（238 交易日、rf0、ddof1）、IR、年化波动率、CAGR、回撤与账户一致性；检查缺失日、极端值、T-close/T+1-open、未来数据、重训边界和成交限制。先交付 evidence manifest、最小审计计划、计算公式、容差与预算，获得确认后再按顺序审核全部 winner/baseline phases 并给出分层 PASS/FAIL/BLOCKED/INCONCLUSIVE。不要优化 CSI1000 参数、使用已消费 Stage-B tail 调参、重写历史结果或修改生产环境；确有 bug 时先给最小复现与影响范围，再另提修复 PR。

---

研究审计不是投资保证。所有收益和 Sharpe 结论必须附原始来源与计算口径。
