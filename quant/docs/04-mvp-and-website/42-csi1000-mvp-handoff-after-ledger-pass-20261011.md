# 42 — CSI1000 研究型选股 MVP：独立账本 PASS 后的新对话交接（2026-10-11）

> **这是当前唯一的新对话启动入口**，取代第 40/41 号文档中已过时的“账本尚未通过”状态说明。那些文档和 PR #52–55 仍是审计过程/实验手册，不是当前结论。**主目标是尽快交付可靠的每日 CSI1000 Top20 研究选股 + paper + 网站链路**，不是自动实盘交易，也不是继续无期限审计历史收益。

## 0. 先核对当前 GitHub main，不依赖旧 SHA

- PR [#55](https://github.com/AT2018cow/qlib/pull/55) 已合并：merge SHA `36aa200b8600d9ab8dd4dc3d0f241be1f332afac`，修改 2 个文件，7 条历史缺价按三份官方公告纳入**日期精确**的证据白名单，没有改生产模型、策略、冻结订单或报告。
- 合并后的代码 CI：[Actions success](https://github.com/AT2018cow/qlib/actions/runs/38069886394)。
- 完整真实 Modal 验证的**证据推送**：`65b37eaac910a16d21da3d3aa91a2fce3a437eb4`（新对话需先检查 `main` 是否已有更新，不盲信此为永久 HEAD）；实际运行代码 SHA 是合并提交 `36aa200b8600d9ab8dd4dc3d0f241be1f332afac`。
- 实验产物：[完整 JSON](https://github.com/AT2018cow/qlib/blob/65b37eaac910a16d21da3d3aa91a2fce3a437eb4/audit/evidence/stage_b_seven_halts_baseline_p00/csi1000-seven-halts-p00-20261010b.json)、[Modal 运行日志](https://github.com/AT2018cow/qlib/blob/65b37eaac910a16d21da3d3aa91a2fce3a437eb4/audit/evidence/stage_b_seven_halts_baseline_p00/modal-run.log)、`HEAD.txt`、`result-sha256.txt`。
- 结果文件在提交中记录的 SHA256：`522f496e40c055f8f5126b1de65e8b5af2f044788529e51892956f9deb3825f3`。必要时用实际文件重新 `sha256sum`；不能把 Git blob SHA 当成文件 SHA256。

## 1. 本轮 Stage-B 审计最终结论——请准确使用

**已通过：** baseline `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785` 的 `phase0`（只此一格），原始 SHA/快照指纹核对通过；2025-01-02～2026-09-30 共 **424 个交易日、1,700 笔冻结订单**；全持仓报价盘点 `unknown_held_close_count=0`、`blocking_count=0`；**9 条公告支持的停牌估值**按上一个有效持仓收盘估值计算；独立现金/持仓/手续费/净值账本 `PASS_RESEARCH_ACCOUNTING_ONLY`，`fail_fields=[]`、`first_diff_date={}`，头部指标与存档的数值差异在 1e-6 以内。

账本最大差异：`account=5.960464477539063e-8` 元、`cash=0`、`value=5.960464477539063e-8`、`total_cost=3.5390257835388184e-8` 元、`total_turnover=8.58306884765625e-6` 元；`net_return=6.746990119377294e-16`、`cost_rate=2.2415879916137804e-17`、`turnover_rate=1.479372180313021e-14`。均满足原定金额 ≤ 0.01 元、费率/收益 ≤ 1e-10 的固定验收门槛。

**研究模拟指标（仅供定义历史证据，不应宣传为实盘收益）：**
- 年复合增长率 `27.96863413010533%`；Sharpe（238 交易日、rf=0）`1.192139266404713`；最大回撤 `−24.35216513080376%`；总模拟收益 `53.6385972274664%`；对保存的 benchmark 序列的相对超额 CAGR `13.059558410379468%`。
- 成本敏感性是针对**原来已冻结成交额**额外加成本，不重算订单成交、调仓或市场冲击；不得将之称为真实滑点模拟。

**尚未验证（重要，不能混淆为 PASS）：**
- 4 笔受历史 ST/涨跌幅限制影响的 `SELL`：`SZ000488/2025-02-21`、`SZ002214/2025-04-29`、`SH603398/2025-05-06`、`SZ300379/2025-05-06`（创业板风险警示为 20% 限幅），全部标记 `BLOCKED_MARKET_EXECUTION_NOT_CERTIFIED`。订单簿、真实可成交性、真实流动性和冲击成本**没有认证**。
- `independent_benchmark_price_audit=BLOCKED_SAVED_BENCH_RETURNS_USED`：相对超额仍使用已保存的 benchmark 日回报，没有重新外部采集价格比对。
- `external_fill_liquidity_st_ipo_pit_maturity=BLOCKED_NOT_TESTED`：未独立认证历史 ST 全口径、IPO/可交易约束、所有 PIT 数据和标签成熟度。
- **只验证 baseline phase0 这 1 格**，绝不可把“全 55 格独立通过”写进网站/报告；Stage-B 55 格 corrected 排名来自已经使用和检查过的 2025–2026 区间，**不是新 OOS**，不能用它宣传“预测收益已证明”或自动调参选优。
- 当前研究系统/网页当日状态、Cron 是否正在运行、最新 provider 是否新鲜、实时 ST/停牌是否正确、股票池 PIT 是否与当天一致、当前 model fit/label 时点是否无穿越——**均须下一对话按真实数据重新验收**。

**审计工作在这个阶段停止**。不要再围绕过去每个停牌事件建逐日审计 PR、扩展 10/55 格或重训参数。若 MVP 的实际信号出现穿越或重要数据错误，需阻断发布/修复的是**当前产品链路**，不是在此重启历史审计。

## 1A. Audit Closure Register — 历史审计正式收口

以下状态是**项目生命周期决策**，不是把所有风险“做成 PASS”。新对话应按此处理；除非触发对应重启条件，不再把 DEFERRED 项重新变成历史审计主线。

| 审计/研究项 | 最终状态 | 对 MVP 的处理 | 重新启动条件 |
|---|---|---|---|
| 原 F1：动态 CSI1000 成分被误作持仓报价范围 | **CLOSED / FIXED** | 保留回归测试；信号 PIT 股票池与持仓 quote-union 必须继续分离 | 当前链路再次出现退指持仓失价/无法卖出的同类故障 |
| baseline phase0 424 日独立现金/持仓/费用/NAV 账本 | **CLOSED / PASS_RESEARCH_ACCOUNTING_ONLY** | 作为只读历史证据；不得包装成真实成交/OOS | 冻结输入、账务规则或 provider 身份被修改 |
| 9 个公告支持的历史停牌估值日 | **CLOSED / ACCOUNTING EVIDENCE ACCEPTED** | 保留日期精确的证据和负控；不再逐日扩审 | 出现与公告冲突的行情或发现该证据错误 |
| 4 笔历史 ST 复牌 SELL 的真实可成交性 | **DEFERRED / NOT_CERTIFIED** | MVP 必须把模拟成交与真实可成交性区分；不阻塞研究榜单 | 准备自动实盘/真实资金，或前向 paper 出现系统性不可成交 |
| 历史 benchmark 独立价格复核 | **DEFERRED** | 历史“相对超额”不得标成独立外部认证；不影响 Top20 生成 | 对外正式发布经独立认证的相对收益，或 benchmark 数据出现异常 |
| 全历史 ST / IPO / 涨跌停盘口 / 流动性 / 市场冲击 | **DEFERRED TO LIVE-TRADING READINESS** | 研究型 MVP 仅做清楚风险状态与模拟假设 | 进入自动下单/实盘准备阶段 |
| 其余 54 个 Stage-B cell 的独立账本逐格复核 | **CLOSED / NOT REQUIRED FOR MVP** | 不再扩大历史审计；55 格 corrected 排名仍只是 consumed-tail 诊断 | 只有发现系统性账务 bug 会跨 cell 影响现有结论时才重启 |
| 旧 Stage-B winner vs corrected baseline 的最终模型选择 | **TRANSFERRED TO FORWARD RESEARCH** | 当前 canonical 不变；未来冻结并行观察，不回填预测 | 新的真正前向数据达到预先定义的决策条件 |
| 当前 CSI1000 PIT 股票池正确性 | **TRANSFERRED TO MVP / HARD GATE** | 当日/使用日时点错误必须 fail-closed；直接影响“今天选谁” | 每日 MVP 验收持续执行，不属于历史审计 |
| 当前标签成熟、训练/预测时点、防未来信息 | **TRANSFERRED TO MVP / HARD GATE** | 模型 fit/train_end/as-of 不合格不得发布新鲜信号 | 每日/重训 smoke 与 CI 持续检查 |
| 当前 provider 新鲜度、交易日历、Modal cron、发布幂等 | **TRANSFERRED TO MVP / HARD GATE** | 过期或状态不明时必须阻断或显著降级 | 日常运维/发布监控持续检查 |
| 当前 ST / 停牌 / 不可交易风险状态 | **TRANSFERRED TO MVP** | 至少标记“未知/受限”；不能将未验证状态宣传为可交易 | 当前数据源/规则实现变化或前向异常 |
| PR #51：官方 Qlib CSI500 / Alpha158 / LightGBM 原版复现实验 | **CLOSED / SUPERSEDED** | 不是 CSI1000 MVP 前置项；实际 qrun 从未被宣称执行或 PASS | 未来确有需要做独立 upstream-Qlib sanity experiment 时可重开/复用分支 |

**统一状态口径：**

- `CLOSED`：本阶段不再继续投入；其中 `PASS_RESEARCH_ACCOUNTING_ONLY` 只表示研究账务一致。
- `DEFERRED`：已知风险被明确保留，不阻塞研究型 MVP；到了表格中的触发条件必须重新进入验证。
- `TRANSFERRED TO MVP`：从现在起属于**当前产品正确性/运行质量**，如果失败应修复或 fail-closed，而不是重新扩大历史审计。
- `TRANSFERRED TO FORWARD RESEARCH`：需要新的不可回填前向证据，不得继续消费旧 reserved tail 以制造“新最优”。

仓库层面，PR #51 已于 2026-10-11 按 **superseded / not required for CSI1000 MVP** 关闭；关闭不代表其官方 CSI500 reproduction 实验已运行或通过。

## 2. 当前模型选择状态（不得静默更改）

`csi1000_production_config.py` 的 `CANONICAL_PROFILE=WINNER_PROFILE`，对应旧 Stage-B “winner” candidate `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`，`CANONICAL_PAPER_LINEAGE=stage_b_winner_canonical`。

已纠正历史报价覆盖问题后的 **55 格诊断排序**：baseline `23b92de0…` 第 1，`c98856b4…` 第 2，旧 winner 第 10。**本次 PASS 的是 baseline phase0 的账务算术，不是策略优越性证书，不是前向 OOS，也不是切换生产模型的授权**。

第一批 MVP 开发不换 `CANONICAL_PROFILE`；确保网页和信号清楚报告当前 `candidate_id`、`model_config_sha256`、`profile`、`stage_b_snapshot_token`、`signal_data_date`、`usage_date`、`model_fit_asof`（如可从运行时可靠获取，亦应包含 `train_end`/provider 身份）。冻结 baseline 和当前 winner 的未来并列观察可在 MVP 稳定后新增，不能回填新预测，不得将现役称为已认证最优。

## 3. 现有可复用模块（优先集成而不是重写）

| 能力 | 当前文件 | 重点验证 |
|---|---|---|
| Modal 生产任务/定时任务 | `modal_qlib_cn_a10g.py` | 实际已部署版本、计划任务、最近运行状态、数据生成/发布链条；代码中有旧模型说明不代表当前结果 |
| LightGBM 训练时钟/标签 | `qlib_live_retrain.py`、`qlib_audit_fixes.py` | 20 交易日训练时钟、252 session 验证、训练标签最后成熟时间、缓存标识、禁止 as-of 穿越 |
| 模型配置与身份 | `csi1000_production_config.py` | 固定 canonical ID/hash/lineage，别因 baseline 的账本 PASS 自动切换 |
| 信号发布门禁 | `signal_publication_gate.py` | **CSI1000 canonical** 要有可信日历与准确前一交易日 bar，缺失时 fail-closed；切勿误用 legacy fail-open 分支 |
| Top20 / PIT 股票池 | `deterministic_strategy.py`、`board_rules.py`、`execution_quote_universe.py` | 选股当日 PIT CSI1000 候选，报价覆盖历史持仓联合，两者不得混同； Top20/Drop2 合同 |
| 成交假设/模拟组合 | `paper_portfolio.py`、`board_execution.py` | T-close 排名 -> T+1-open pending 计划，按可用后续 bar 结算，无法交易不可虚构成交、持仓估值缺价风险 |
| 前向净值 | `forward_performance.py` | **2026-10-12 才开始真实前向 paper 观测**；历史 Stage-B 曲线不能拼到前向业绩 |
| 网站与发行 | `website/app.js`、`website/build_index.py`、`website/index.html`、`website/methodology.html`、`.github/workflows/website-deploy.yml` | 展示最新真实日期、正确模型、风险说明；历史信号可审计，不能伪造当前状态 |
| MVP 静态检查 | `audit/mvp_entry_review.py`、`tests/test_stage_b_mvp_closeout.py` | `PASS_STATIC_ONLY` 不能冒充线上健康和实时风险检查 |
| 冻结审计证据 | `audit/evidence/stage_b_seven_halts_baseline_p00/` | 仅用来证明 baseline phase0 算术一致；**只读** |

## 4. 当前状态时间线

- 2026-10-10 的提交已经含有 2026-10-09 的 `results/signals/2026-10-09_top20_lgb158.csv` 和 `2026-10-09_paper_portfolio.json`。静态 paper 示例写有 `signal_data_date=2026-10-08`、`usage_date=2026-10-09`、旧 winner lineage，尚不能证明 Modal 在线、最新生产成功或市场风险得到完整过滤。
- `forward_performance.py` 定义 paper 前向起始日期 **2026-10-12**。在确实产生第一天结算后的真实 artifact 之前，应显示“等待前向数据”，不能凭历史结果或补画 2026-10-12 前的前向业绩。
- 本交接整理时为 2026-10-11。新对话**不要假设日期仍未变化**；先查 `main`、最后完整交易日、最新 artifact、站点内容和 Modal 运行记录。

## 5. 下一对话首个 MVP PR：明确范围、测试与验收

**先交付一个小而完整的端到端 MVP PR，不做重新调参或新一轮历史回测。建议步骤：**

1. **盘点实际运行状态：** 查 `main` / GHA / 有授权的 Modal deployment 与 cron / 最近两次实际信号与 provider 最后一个完整 bar。若无法查看 Modal，要求授权账户按 PR runbook 执行 smoke 并回传日志，而不是假定 cron 已经执行。
2. **规范输出合同并设 fail-closed：** 榜单/元数据/页面应追踪数据日期、发布使用日、Top20、模型 ID/hash/版本、provider 身份与信号 SHA256；当日历错误、过期 bar、训练标签未成熟或模型身份不一致时，不发布/明确阻断“新鲜选股信号”。验证现有 canonical gate 被实际调用。保留旧历史 artifact，不静默覆盖。
3. **明确当日可交易性边界：** 基于**使用日之前已知**的日期/状态对 ST、停牌、涨跌幅、未确认风险标注，至少不将未知状态宣传为“可交易”。Held suspended 股票维持账面持仓，不允许瞬时假卖出。若引入硬过滤，单独审查前向时点和 Top20 变化，**不隐式改变冻结策略**。
4. **纸面组合闭环：** T 收盘冻结 rank/计划，T+1 开盘数据真正可用后才结算；去重/idempotency、停牌/限价/无报价负路径应可测。保存冻结计划、成交/未成交原因、期初期末 NAV；无实时权限时标为模拟，而不是实际成交。
5. **网站校验：** CSI1000 Top20、模型身份、数据日、风险和 paper 状态一致；缺失前向观测显示等待，永不把已消耗的历史收益接到真实前向曲线。不推 STAR/ChiNext 的历史待办。
6. **必要时冻结并行观察的候选：** 仅在主线发布稳定后追加真正前向不可回填的 baseline/旧 winner 预测记录，不必等待统计显著性再发 MVP。

**PR 验收证据：**
- CI 包括成功样例 + 失败样例：缺价、过期/非交易日、错误模型 hash、未来标签、ST/停牌未知、重复发布与 pending 状态损坏；
- 有至少一个“完整交易日收盘 bar -> Top20/metadata -> paper pending -> 网站索引”**端到端 smoke**，及一个后续数据已到时的成交结算冒烟（若尚未开市则标为待运行，不造数据），明确 SHA / snapshot / as-of；
- 交付明确的每日 Modal 操作/监控/失败复原 runbook，规定 GHA/网站/纸面净值读取的确切文件与默认隐藏/警告状态；
- 原定 `CANONICAL_PROFILE`、Stage-B 冻结报告和历史 audit JSON 未改；paper 不自动实际下单；无新 OOS 证据不宣称策略已获优化确认。

**停止标准：** 每日可追溯、失败可见的 Top20 研究服务 + 合格 paper 计划/结算合同 + 可被最终用户理解的页面风险提示。后续选择哪个 LightGBM 候选/是否需要调参是下一阶段的单独策略决策。

## 6. 新对话可以直接发送的任务原文

> 请以 `docs/experiments/42-csi1000-mvp-handoff-after-ledger-pass-20261011.md` 为最新交接入口，核对当前 GitHub main、最近信号/网页和可访问的 Modal 运行状态。PR #55 合并并经真实 Modal 核对，**只有 baseline phase0 的 424 日独立研究账本 `PASS_RESEARCH_ACCOUNTING_ONLY`**；9 个公告停牌日估值通过，4 笔 ST 卖单真实成交与独立 benchmark 仍未认证。这不是样本外验证，也没有批准切换生产模型。请尽快在现有代码基础上开发第一个 CSI1000 研究选股 MVP PR：真实每日数据新鲜度、模型/股票池 PIT 与成熟标签时点、Top20 和 paper 订单状态、ST/停牌风险、网站展示、端到端 smoke、实验提交清单。先交付稳定的每日选股链路，不继续扩大历史审计/参数搜索，不自动修改当前 canonical winner，不承诺实际收益或真实交易。
