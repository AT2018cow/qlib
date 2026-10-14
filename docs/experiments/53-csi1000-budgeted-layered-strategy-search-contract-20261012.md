# 53 — CSI1000 有预算上限的分层历史策略搜索合同（2026-10-12）

## 2026-10-14 L2 fast-lane operator amendment (execution requires separate reviewed runner)

After L1-C closed 264/264 with a distinct audited decision, the user requests
shorter L2 execution handoffs: one genuine training smoke and then maximum
safe progress through a single foreground auto-coordinator. This is an
**administrative execution policy**, not a relaxation of the L2 experimental
search space, source/fit identity gates, USD 12 L2 hard spend ceiling, 320
all-attempt fit cap, USD 30 global cap or the need for a new training-runner
PR and independent tests. Hard STOP on unavailable current billing/spend data
or insufficient reserve still applies; actual usage and fit receipts remain
mandatory. Repeated manual P90 worksheets, human batch-by-batch approvals
and legacy ledger reservations are **not** mandatory for healthy auto-waves
within those caps. Automatic waves must account for cost and new fits,
including failures and retries, using the single-run immutable log.

Freeze L2 seed=20261012 and the four diverse unused Stage-A models plus
four bounded new proposals **before training**; 16 short-fold fits across the
four new proposals, and only 0–2 qualified new proposals enter long
evaluation alongside the four existing Stage-A models. All L2 long
evaluation is true retrain at phases 0/10 with fixed Top20/Drop2 and the
frozen 424-session F1 execution account, at most 6 configs, 12 lineages and
320 total new fits. One smoke invocation must exercise *both* a new proposal
short fold and a long rolling train cell; after verified smoke the operator
can launch a single foreground bounded auto-coordinator that processes
short folds, shortlist, long fits and result verification with monotonic
checkpointing and no repeated approval. No unchecked detached sessions or
concurrent coordinators, and cap any run to the proven attached timeout.
The automatic workflow must STOP on any reproducibility, as-of, fee, quota,
OOM, artifact SHA, leakage, training-count or billing conflict rather than
pretending a partial experiment is complete. No production Winner update,
and L4 awaits independent full-roster approval.

See [runbook 56](56-csi1000-l2-fastlane-execution-20261014.md) and
`audit/csi1000_l2_fastlane.py` for the **plan-only** geometry and caps.
Neither constitutes a real Modal fit runner nor authorizes immediate paid
execution. The original §4 exploration and §6 final gates still apply.

## 2026-10-13 L1-C runner implementation addendum (review required)

L1-B reached 144/144 independently reconciled cells, including 32 L1-A
reuse attestations. The pre-registered L1-C promotion condition is satisfied
by six two-phase coarse-screen configurations across four different models,
as reported in [PR #68](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6149341657).
This does **not** constitute new OOS evidence, five-phase selection, or
permission to change production Winner.

L1-C implementation may reuse exactly **144** byte-verified L1-B cells and
replay **120** new zero-fit portfolio cells, reaching **264 cumulative**
cells (11 Stage-B models × 12 portfolios × 2 phases). Before launching any
replay, validate the complete signed 144/144 L1-B export, all original
32 L1-A attestations, independent two-phase screening against verified
P0 Baseline, 55 frozen source rows, current provider/quote identity and
144 artifact SHA attestations. A changed code SHA needs a recorded reviewed
replay-equivalence decision; otherwise STOP.

The user's simplified L1-B execution policy extends to **this specifically
reviewed L1-C zero-fit runner**: first run 1 worker × 1 new cell smoke;
then 2 workers × 8, scaling to 4 workers × 16–32 only after observed
stability. Per-container replay allocation remains 2 vCPU / 4 GiB, timeout
3600s, no automatic retries. P90 cost quotes, per-batch billing ledgers,
USD budget pre-approvals and repeated manual cost signoffs are **not**
mandatory blockers for L1-C. Avoid obvious waste with smoke, bounded
concurrency, no unnecessary model fits, one active foreground coordinator,
run-ID/SHA-scoped outputs and STOP on deterministic failures. The old
P0/L1-A accounting history is untouched; later L2/L3 and production gates
remain separate. This L1-C amendment supersedes conflicting historical
expense paperwork provisions for L1-C alone.

L1-C code must pass independent offline pytest, Black, pyflakes and
argument smoke checks on the exact merged SHA **before a paid task**. See
[runbook 55](55-csi1000-l1c-smoke-first-operator-20261013.md). No paid
Modal execution is authorized merely by creating this code PR.


> **状态：历史合同及 L1-B 执行修订。** P0 和 L1-A 已有独立运行证据；L1-B 尚未执行。本文件不授权自动切换生产。**目标是尽快选定可用于每日选股和收益网页的历史优选策略，不要求先等待数月前向数据。**
>
> **独立研究例外与权限边界：** 本文建议在新的隔离研究分支、独立 Modal research Volume 内对已经消费过的历史数据开展公开标注的产品选型。它**不**改写既有 Stage-A / Stage-B 实验结论，不将已观察的 2025–2026 区间重新包装成 OOS，不覆盖当前 Winner 的生产配置；正式选择和上线需要单独 reviewed decision/release PR。当前默认仍是 KEEP_CANONICAL_PENDING_RESEARCH_DECISION，直到新的书面决策获批。

## 2026-10-13 L1-B 运行政策修订（优先于本文旧费用门禁）

**用户要求：实验进度优先，避免明显的大额浪费，但不要求每批填写费用账本、生成实时 P90 报价或另走预算审批。** 下述政策只适用于已审查、已合并的 L1-B 组合回放代码；此前 P0/L1-A 的历史实际费用/审计资料保持原样，L2/L3/L4 的实施及生产晋级仍须相应代码与数据验收。本节明确替代下文 §2.1/§2.2、§7.2、§7.3 中与 L1-B 冲突的 **USD30/$6 硬费用门、每批 P90/20% 预留、账本预占与结算、每批人工预算审批** 条文。原 $30 和各层数字保留作历史研究规划基线，**不再阻断 L1-B 派发**。

- **第一成功率优先**：检出干净且固定的 commit SHA，离线 pytest、Black -l 120、pyflakes 和 Modal CLI 验收通过；用同一 Modal image、原冻结 Volume、原研究输出前缀跑 **1 格新增计算/1 worker** 的实际付费 `--smoke --batch-limit 1 --max-workers 1`。预检仍必须核验 55 条冻结来源、live provider/F1 quote SHA、32 格 L1-A 字节复用。smoke 必须经过完整文件、指标与汇总校验并产生 `smoke_pass.json`；无有效 smoke proof，程序拒绝长批任务。
- **可控扩并发**：smoke PASS 后，同一 `run_id`、同一 code SHA/resume prefix，先用 **2 workers × 8 新格**验证稳定性，然后可提高至 **4 workers、每批 16–32 新格**；仅在资源与延迟稳定时考虑进一步提高，**最多 8 workers**。每个 replay 容器固定 **2 vCPU、4 GiB RAM、3600 秒单格超时、0 次自动重试**；4 workers 理论峰值约 8 vCPU/16 GiB，不改变冻结策略及算法。前台连接、工具超时覆盖整个批次；不能满足则减小批次。不得无约束长批、自动无限重试、切换 SHA 混用结果。
- **轻量现场保护**：保留 Modal App ID、真正退出码、耗时、失败类型、容器 OOM/timeout、完成格数以及出现异常时可见的账单概览；**不强制价格快照、P90 文件、手工入账或逐批审批**。如 image 构建或 import/Cython smoke 失败，停止而非盲目多次重启；相同故障连续出现、OOM、费用突增或重复跑已完成格，立即中止并定位原因。
- **不能放松的可信度门**：只读原数据 Volume、既有 4/4 P0 和 32/32 L1-A 导出、复用 SHA/代码等价证明、预注册 144 格、112 新格、单元指标独立复算、无代码漂移和仅研究 Volume 写入、L1-C 晋级条件、人工批准生产发布，全部保留。不能把已消费的历史期说成 OOS。
- **启动边界**：本合同修订和代码合并均不自行启动云端任务。操作者在确认离线测试通过、原研究证据路径齐全后执行一次有限 smoke；其后按本节无需逐批审批即可继续明确受控的 L1-B 批次。

## 0. 目标、成功定义和关键约束

1. 产品目标：确定一套完整的 CSI1000 日度选股策略，每个真实交易日生成 PIT 合规的股票评分和网页 Top20 榜单；为其独立 paper 账户逐日计算扣费 NAV，并在网站**分别**显示历史模拟回测与上线后逐日记录的模拟收益。
2. 决策目标：在有限、预先定义的历史搜索空间和成本内，寻找**符合净收益、风险、换手与跨时期稳健性标准**且实际优于已纠错 Baseline 的配置。找不到即停止，使用 Baseline + Top20/Drop2 作为产品历史优选版本，不继续无限扩搜。
3. 这次是**新产品策略搜索**，不同于之前的 320 格零重训报价纠错；不再把补查旧行情、重跑旧 320 格、重复验证旧 55 格当成独立工作目标。
4. 当前数据证据：Stage-A 80 组 LightGBM 参数 × 4 个约 20 日短折，修正报价后 320/320 PASS，80 个排名不变，但 69 格 fold4 受影响，LOFO 移除 fold1 后排名明显不稳；Stage-B 只把 Stage-A 前十加 Baseline 一共 11 套送入 2025-01-02～2026-09-30 的 424 日、五 phase 长期回测。Stage-B 修正后 Baseline 为 11 套中的第 1，原 Winner 第 10。它不是「所有 80 套长期比较已完成」。
5. 保留 CSI1000 / Alpha158 + LightGBM 作为本轮边界；不纳入 Alpha360、RNN、其他股票池和真实自动下单。若将来扩特征或模型家族，必须另立合同与独立预算。
6. **不使用“绝对最优”表述**：结果只能称「本预算、数据和评判标准下的历史优选」。样本内择优偏差、多重比较、现实流动性、独立 benchmark 与历史 ST/IPO 认证限制必须在决策和网页中揭示。

### 0.1 不可修改的既有事实与参照

- 冻结 Baseline：candidate_id = 23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785；生产可读模型配置参见 csi1000_production_config.py / BASELINE_PROFILE；model_config_sha256 = 793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3。
- 当前生产 Winner（只作参照及回滚）：candidate_id = 4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2；config_sha256 = 0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88；CANONICAL_PROFILE / stage_b_winner_canonical 不在本研究 PR 中改动。
- Baseline 的当前组合：TopK=20、n_drop=2、原始 20 日标签、每 20 交易日重训、Alpha158、T 收盘信号→下一可用 T+1 开盘模拟执行、1 亿元独立连续 paper 账户、买入 5bp/卖出 15bp/最低每单人民币 5 元（以冻结实际执行约定核验）、不使用 5% high-open 叠加规则。
- Stage-A 原搜索的**七个**维度：learning_rate、colsample_bytree、lambda_l1、lambda_l2、max_depth、num_leaves、min_data_in_leaf；TopK、DropN、训练频率、标签期限、成本和 Alpha158 均**没有参加**那轮搜索。
- 早期 results/p0/ndrop_grid.csv 曾提示 Drop3 可能好于 Drop2，但其运行于旧执行/指标环境，不可直接当作本次验证结果；仅用来提出候选。
- 重要原始入口：docs/experiments/16-*.md、35-*.md、42-*.md、47-*.md、50-*.md、51-*.md，Stage-B 源结果 results/csi1000_stage_b/stage_b_full_51756897fc752304.json、Stage-B 修正结果 audit/evidence/stage_b_corrected_rerank/batch_result.json、Stage-A 修正证据 commit d80725cce78058b2f67821982e0144ded0f459c1 与最终勘误 commit 925771975445198d4c30a7992b5f313577e98170；**所有源 SHA、快照 token、provider fingerprint 必须从当前可核验文件重新读出，不盲复制旧文字。**
- Stage-B 55 格修正信号分布在 operator 原 Modal Volume，并非 GitHub 仓库里的所有原始私有预测/交易文件；未获授权或源哈希不符时报告 SOURCE_UNAVAILABLE，绝不从新的预测补造旧信号。

## 1. 搜索空间、风险与统一统计口径

### 1.1 允许的搜索维度

| 类别 | 本轮可搜索的变量 | 计算性质 | 禁止混淆 |
| --- | --- | --- | --- |
| 模型 LightGBM | Stage-B 已有 11 套；其余 Stage-A 的有代表性已训练候选；最多 4 套新超参数提案 | 新超参数/新日历通常需 fit | 不把 80 个 Stage-A 短折视为 80 个完整长期回测 |
| 投资组合 | TopK ∈ {10,20,30,50}，DropN 候选见 §3 | 冻结相同 signal 时**零训练**、每组合重算交易/NAV | 网页展示 Top20 排名不等于 paper 实际持有 Top20；若 TopK≠20 页面必须明示 |
| 重训频率 | 20 日基准；只对晋级者比较 10/40 日 | 重训日历改变，通常**必须重新 fit** | phase 是日历偏移，不是新的预测期 |
| 预测标签期限 | raw 20 日基准；有条件比较 raw 10/40 日 | 必须重建 PIT label、标签成熟和 purge，**重新 fit** | 改 label ≠ 复用旧 20 日 checkpoint |
| 交易与风险 | 统一持仓/成本/价格限制，另做 +10bp/+20bp 固定成交额压力 | 审计/敏感性，不允许事后挑假设 | 禁止把更便宜的虚假成交“优化”为 alpha |

**不做完整笛卡尔积**；某些 TopK/DropN 会产生结构性不同的持仓/流动性风险，所有选中的组合必须能在同一信号、日期、行情、账本核算下公平比较。TopK 取值在用户认可的页面/持仓产品需求确定后才冻结；默认允许网页仍展示 Top20 排名，但 paper 实际持仓数量需要单列明确，不能伪装两者一致。

### 1.2 报酬、风险与对照组合同

- 基本单位为**同一执行日期**、初始本金各为人民币 1 亿元、同一 provider snapshot、同样的 PIT eligible candidate/universe、预测可用时间、费率、复权、成交/未成交、停牌/涨跌停、现金和 T+1、未平仓估值。对照组 Baseline+Top20/Drop2 必须以修正后的 **F1 静态 quote union** 路径和相同日期重新使用或严格 SHA 复用。
- 排名指标先比较 **五个 phase 中最差的扣费相对超额 CAGR**，再看中位数扣费相对超额 CAGR、扣费 Sharpe/IR、最大回撤、换手、成本。相对超额引用保存的同一 benchmark，只能称 saved-benchmark-relative，不称独立认证。额外报告净绝对 CAGR、累计收益、年度拆解、分阶段表现与成本压力。
- 对每个 challenger，按相同 phase、同日 NAV/return 与 Baseline 进行**逐日配对**；记录五 phase 各自盈亏差及四/五 phase 稳定性。不可把不同 TopK 或不同训练频率的自然差异当成数据损坏。
- **产品实质改善门槛（待 PR 审阅批准后冻结，任何运行后不得变更）**：五 phase 最差净相对超额 CAGR 较 Baseline 至少 **+1.0 个百分点**；五 phase 中位数至少 **+1.0 个百分点**；至少 **4/5 phase** 的净总收益高于同 phase Baseline；候选五 phase 中最深净 MDD 比 Baseline 最深净 MDD 恶化不超过 **3.0 个百分点**；中位成交换手 ≤ Baseline 的 **1.25 倍**；固定成交额附加 **+10bp** 成本后相对优势仍为正，**+20bp** 单独报告而不作为唯一否决；交易日有效率及未成交记录完整。任一关键数据或账目不合格即 NOT_ELIGIBLE，不能用收益补偿数据错误。
- 上述阈值是**产品决策规则**，不是统计显著性或未来收益承诺；阶段初筛门槛可放宽以避免早杀，但最终通过必须满足本条全部条件。
- 若多个候选都满足：先按最差 phase 改善幅度排序，其次中位改善、风险、换手与成本；严格稳定决胜顺序最后取 candidate/config 内容 SHA 排序，避免看完图后主观选。
- **不把历史尾部作为未见样本**。2025–2026 Stage-B 和其他已观察历史均标注 HISTORICALLY_CONSUMED；新搜索越多，选择偏差越大。固定搜索协议、报告全部失败候选、多重检验数量和排行榜，不提供凭同一历史证明“未来有显著 alpha”的措辞。

## 2. 总预算、停止规则和授权边界

### 2.1 原始全轮费用规划（保留为历史基线；L1-B 适用上文修订）

| 项目 | 初始硬上限 | 计数规则 |
| --- | ---: | --- |
| 新增 Modal **实际费用** | **USD 30.00** | 包括失败、镜像构建、预检、训练、执行、审计、helper/collector、重复任务；以当期 operator 账单 + 失败 App ID 对账，不扣减猜测的 Starter credits |
| 新的 LightGBM fit() 调用 | **1,200 次** | 含失败/重试/结构期/决赛期的真实 fit 调用，必须由 runner 原子化计数 |
| 第一层组合评估 | **最多 264 个候选×phase 单元** | 11 模型 × 12 portfolio 组合 × 2 phase；已严格 SHA 复用的单元仍计入评估规模，但不产生新计算费 |
| 决赛 | **≤6 套完整策略配置（含 Baseline）×5 phase，最多 30 单元** | 决赛名单和评判规则必须在运行决赛前冻结 |
| 并发研究 worker | 初始 2、逐级 4、最多 **8** | 提高超过 8 需单独事前审查，不可用更高并发绕过预算 |
| 累计占用式实验执行时间 | **72 小时** | 各批实际 queue/build/run/finalize 挂钟时间相加；达到上限立即停止新增批次 |
| 研究运行窗口 | 开始执行后 **7 个自然日**内决策或书面 STOP | 研发等待、人工评审和供应商故障导致的不可控延期写明原因，但不默许无限延期 |

**分层费用硬封顶**（累计总额不可超，默认不可层间挪用；未花完也不授权下一层超限）：P0/预检 USD 2，L1 USD 6，L2 USD 12，L3 USD 6，L4/决赛 USD 4；总计 **USD 30**。每层若实际费率显示不够做完预定范围，则**提前缩减**该层规模，保留审计证据，不默许提额。任何提高费用/fit/时间或改变搜索空间需新 PR 和显式授权；本草案本身不构成已批准云端消费。

### 2.2 原始费用门禁和保留的来源/数据 fail-closed（L1-B 费用例外见修订）

1. **每批开始前**从 Modal 当时实际 billing rates 和已完成同规格作业的真实耗时、CPU/GiB 秒、App/image startup 开销估算 P50/P90 单元费，测算这一批的 P90 新增金额；若预计超过**本层剩余额度或全局剩余额度**，不启动，先减少 batch size 或 STOP_BUDGET。
2. 总费用达到本层上限/全局上限，或真实 fit 次数/累计时间达上限，停止申请新任务；在飞任务由预设 batch 与剩余预算覆盖，不依赖杀掉正在提交结果的 worker 追求零超支。账单更新滞后时预留 **至少 20% 剩余预算**给已派发但未结算工作，费用未知即 STOP_COST_UNKNOWN。
3. 源 Volume 快照/文件 SHA/checkpoint/PIT universe/hash 漂移、legacy/corrected Baseline 控制重现失败、欠缺成熟标签、未来数据泄漏、Qlib 缺价/账务差额、单格结果冲突、无合法独立研究输出目录：STOP_P0，不得继续择优。
4. 单阶段没有候选进入晋级，**停止这一搜索分支**，不为救活结果追加指标、参数或任意相位；若全局研究在 L3 后仍没有一个最终满足所有门槛的候选，最终直接 KEEP_BASELINE，而非临时扩任务。
5. 即使早期有亮眼候选，也**不允许自动扩大** 264 格/1,200 fit/$30/72h/7 日上限；进入决赛也不自动允许上线。
6. 模型超参、label、频率仅允许本合同已列候选；改变搜索方向、淘汰标准、日期范围、训练窗口或实测费率明显不符时必须重新走协议评审，旧结果不覆盖。

## 3. P0 与 L1 — 用冻结信号搜索投资组合参数（零重训）

### P0：先证明新的搜索工具忠实复现已纠错基准

- 只读盘点可访问的 Stage-B 原始 signal.parquet、各自 candidate/phase/model_checkpoint/config/provider/feature SHA，保存 11×5 manifest；没有真实私有源文件就 STOP_SOURCE_UNAVAILABLE，不可使用新的模型预测替代旧信号。
- 将 **Baseline 和 Winner × phase00、phase10 × Top20/Drop2** 共 4 个单元做 SHA 复用或只读复演；legacy controls 与先前 55 格已纠错结果的订单/NAV/费用在预设容差内一致（金额 ≤¥0.01、回报/费率误差 ≤1e-10）；任何漂移阻断扩搜。
- 跑合成负控：改动 model config hash、偷看未来成分、预测 timestamp 晚于交易结果、缺执行 open/factor、持仓退指后缺报价、停牌假卖出、费用为 0、跨账户串账、重复 key/恢复、缺 baseline 参照与超预算，均必须拒绝。
- 标定真实 Modal CPU/内存峰值、每格耗时/费用、构建和队列占比、4/8 worker 的启动并发效率；冻结搜索 runner/source SHA、镜像与版本，产生 P0_RUN_CARD。**P0 通过不等于生产部署。**

### L1-A/B/C：投组组合的递增试验

本层仅改变 TopK / DropN；**现有 11 个模型 checkpoint/信号保持原样**。统一研究成本，不再把 2025–2026 宣称新 OOS。

| 子层 | 上限规模（累计） | 内容 | 增量计算及动作 |
| --- | ---: | --- | --- |
| L1-A 小试 | **32 格** | Baseline、Winner × 8 组合 × phase 0/10 | 先确认 Drop3 与替代 TopK 的真实增益，失败可停止该方向 |
| L1-B 扩大 | **144 格** | 6 个代表模型 ×12 组合 ×phase 0/10 | 补全 L1-A 未覆盖配置；6 模型含 Baseline、Winner、c98856b4、a419047a、e74b3bbe、59b93929 |
| L1-C 上限 | **264 格** | 11 个既有 Stage-B 模型 ×12 组合 ×phase 0/10 | **只有**前两层显示可复制的改善且预算允许才扩满；无实质进展则不扩 |

预注册 **12 个 (TopK,DropN)** 配置：Top10×{1,2,3}；Top20×{1,2,3,5}；Top30×{1,3,5}；Top50×{2,5}。L1-A 的 8 配置：Top20×{1,2,3,5}、Top10×{1,3}、Top30×{3}、Top50×{2}；DropN 必须严格小于等于 TopK。**已有修正 Top20/Drop2 单元只有在版本、报价、费用、日期、完整信号/输出 SHA 全部相同才可缓存**。

**L1 的粗筛晋级门槛**（仅供决定是否扩算，不是最终“优于 Baseline”）：在两个预定 phase 上，候选最差及中位净相对超额 CAGR 的二相位统计均比同 phase Baseline 改善至少 +0.5 个百分点、相对费用不异常、最差 MDD 不恶化超过 5 个百分点、未发生覆盖/账务错误。至少两个来自**不同模型或不同 (TopK,DropN) 配置**的候选满足才执行 L1-C；如果 L1-A/B 没有通过者，直接终止 L1，不自行为了找赢家增加第三个相位。最多挑 3 个 L1 组合进入最终候选池；每个候选保留每 phase 原始返回和成本，禁止只公布赢家。

## 4. L2 — 针对性 LightGBM 超参数扩展（有上限的新训练）

**与 L1 独立的已承诺探索方向**：即使 L1 没有改善，L2 仍可在其预算范围内进行一轮有限模型搜索；L1 的不成功不构成“模型也不可能改善”的证据。

1. 从 Stage-A 其余 69 套未进 Stage-B 的模型中，用固定种子 **20261012** 按参数多样性/Stage-A 多 fold 指标（不能只看由 fold1 主导的旧名次）选**至多 4 套**；它们的 Stage-A 四折预测不足以直接复用为长期 Stage-B 信号，所需长期 retrain 必须真实执行。
2. 最多提出 **4 套新 LightGBM 参数组合**，只在原七维、合法 bounded domain 中变更，固定 Alpha158/raw20/freq20；优先在已表现较好的区域和明显不同的正则化/树复杂度区域提出，不做 80×全网格。建议优先在本地 CPU 用固定 random/sobol proposal 做 4 套；**如选用 Optuna TPE / Bayesian proposer**，依赖/seed/version/trial history 必须锁定，先用已有 80 套真实 Stage-A fold_summary 做 warm-start，不能把 Stage-B 纠错指标注入本来宣称“未使用”的阶段；没有稳妥插件/方法即使用确定性分层抽样，严禁声称 Optuna 已预装或提升百分比有保证。
3. 对 4 套新模型先跑 **4 个有隔离 purge、成熟标签、as-of 锁定的 Stage-A 20 日折**，至多 16 次新 fit；统计与 Baseline 的最差折、Q25、median、LOFO，避免仅凭单次好表现晋级。至多 2 套新候选晋级长期验证（如无合格者可为 0）。
4. 既有 Stage-A 4 套 + 新晋级至多 2 套，**最多 6 个 challenger × phase0/10、完整 424 日**（12 条 retrain lineage）。每 20-session 重新 fit 的调用数按实际训练日历记录，**本层硬上限 320 次新 fit() 调用**，包含失败和重试；总费用不超过本层 USD 12。
5. L2 晋级门槛使用与 L1 相同的二 phase 粗筛门槛，并额外满足完整训练/验证/PIT/标签成熟与模型配置复现。最多 2 套新模型策略进入决赛候选池；如果无改善，不扩大超参数数量。

**重要：不同模型参数训练所得的 checkpoint/预测不允许跨组合复用。** 可共享 PIT 校验后的 Alpha158 特征缓存、训练数据切片/校验信息，但 label、as-of、模型 fit 版本变化时不能拿旧预测冒充新试验。所有 fit checkpoint/seed/库版本/模型配置 SHA 均应保存。

## 5. L3 — 重训频率与预测期限的定向结构实验

原则：**不要把不同标签期限 × 不同模型超参 × 全部 TopK/DropN 做笛卡尔积。**

- 模型锚点只用一套：Baseline（或在 L1/L2 已预定粗筛上真实领先的那一套；在**开始 L3 前**冻结，不因 L3 中途损益更改）；保持其 TopK/DropN 不变，不在结构层重新搜索组合。
- 最多 4 个结构变体：**(raw20,freq10)、(raw20,freq40)、(raw10,freq20)、(raw40,freq20)**；原 (raw20,freq20) Baseline 为对照。每个变体只使用两组事前固定的重训起点 phase，分别取周期的 0 和 half-frequency offset（freq10: 0/5；freq20: 0/10；freq40: 0/20），防止两相位重复或超出周期。
- 标签 10/40 日需重新生成正确的 future-return label、成熟日历及 purge/embargo；不能直接把 20 日标签的训练样本、checkpoint、标签成熟合同套过来。不同重训频率需要新的训练切片和真实 fit。**任何 PIT/标签成熟核验未实现时本变体直接 BLOCKED，不因收益好继续。**
- 本层总计最多 8 条新结构训练 lineage，至多 **250 次新 fit()**（含失败/重试），费用≤USD 6；先试 raw20 变更 freq10/40，只有至少一条达到 L1 粗筛且 P90 成本可覆盖，才继续 raw10/40 标签提案。若全部无改善，结束本层；最多 1 套结构候选进入决赛。
- 为相位可比，不把不同频率的 phase 整数直接当作同一个日历：记录各变体 phase schedule、首次预测日、交易覆盖与实际模型年龄；所有策略在**共同 424 日执行窗口**按同一天净收益对齐。决赛的五 phase 预定义为：freq10 → {0,2,3,5,7}；freq20 → {0,4,6,10,15}；freq40 → {0,8,12,20,30}。不同 phase 是各自日历的稳健性扰动，不能伪称同相位逐日模型训练对齐。

## 6. L4 — 统一历史决赛、STOP 与产品决策

- 决赛名额：**含原 Baseline 共最多 6 套**完整配置（L1≤3、L2≤2、L3≤1 会有 1 个名额竞争；按事先固定的二 phase 最差值→中位值→风险排序截断，决赛前一次性冻结名单/hash，禁止看完部分决赛再增补）。可以没有任何 challenger。
- 每套做其所属重训频率下的**五个预定义 phase**，2025-01-02～2026-09-30 同一 424 日实际执行区间；仅当 checkpoint/预测/报价/成本/策略/版本一致时复用二 phase 已完成单元。长历史新训练遵守全球 1,200 fit 上限及 L4 USD 4 子预算；不足时按固定名单顺序运行，不可只补算表现好者。**任何一套五相位未齐就不允许称该套「最终优于 Baseline」**，应记录 INCOMPLETE_BUDGET 并维持已验证 Baseline。
- L4 决策必须逐项执行 §1.2 的**七项性能/风险/成本/数据门槛**并出具裁决表（PASS / NOT_ELIGIBLE / INCOMPLETE / BLOCKED），不根据事后最漂亮的一年、单一 phase、复合收益截图更改目标或成本。
- 如果一个或多个 challenger PASS，全量指标排序选出**唯一历史优选**，产生 PROPOSE_PRODUCT_PROFILE_VERSION，不自动修改线上。若 **0 名 PASS**、任何全局上限先到、P0 源完整性 FAIL 或证据不足，则决策为 **KEEP_BASELINE_FOR_PRODUCT_SELECTION**（如果 Baseline 本身不通过数据/生产硬门禁，改为 STOP_PRODUCT_BLOCKED，不能硬上线）。
- 如果在 L1/L2/L3 就有强 challenger，仍应给它完整五相位比较；**不能**以 2-phase 初筛提早宣布生产胜利。若预算/时间不允许完成，保持 Baseline。
- 强制随选型交付：唯一 (model/candidate/checkpoint/seed/Alpha158/label horizon/freq/phase policy/TopK/DropN/fees/PIT/execution) 机器可读 config SHA；历史五相位图和风险表；对照组原始 report/decision hash；所有 candidate trial ledger（含失败）；选择偏差和历史 ST/成交限制说明；至少一种确定性负控；提交 reviewed decision PR。**产品上线的硬门槛是每日真实新数据、模型时点、网页、纸面结算的端到端 smoke，而不是等待 120/240 未来样本。**
- 当前 Winner 必须仍保持原生产版本，直到独立的 deployment/release PR 得到明确授权；发布新网站时历史回测收益与实际上线后按日积累的 paper NAV 两条曲线分开，不能拼接历史净值，也不能称 broker 实盘收益。

## 7. Modal 计算架构：充分利用 Modal / “Model” 提高效率

> 用户所说“Model”在本项目执行上下文按 **Modal 云执行平台**实施，同时显式利用 LightGBM 模型/预测复用。**本节是设计合同；以下 runner 文件尚未创建，命令示意在实现+review 前禁止直接运行。**

### 7.1 四层任务拆分

1. **数据/模型身份盘点（低成本）**：通过一个受控 operator-only read-only Modal 任务枚举已训练 checkpoint、signal SHA、training cutoff、provider snapshot/universe/feature SHA 和 Stage-B 原固定 phase；只下载小型 manifest，不下载整个私有 provider，更不能在 GitHub 公开原 signal/credentials。
2. **Fit / predict（昂贵）**：仅 L2/L3/L4 必要的新模型配置和新训练日历执行 LightGBM fit；每次把 (params, feature, label, train_asof, purge, calendar, provider, seed, env) 作为不可变 cache key，成功后保存 checkpoint SHA、完整排序预测与 status。使用相同 checkpoint 的多个 TopK/DropN **绝不重新 fit**。
3. **组合回放（相对廉价）**：大部分 L1 工作仅从已验证 frozen signal 与同一 provider 读取，生成不同 TopK/DropN 的独立 orders/NAV/费用。报价仅查询曾入指静态并集，PIT 当日选股依旧限于当时可选股票；禁止用静态并集偷渡未来入指者进入信号候选。
4. **审计/聚合（低成本）**：纯 Python 本地或低资源容器重新计算 ranking/fees/account、每个阶段统计/成本/stop 判定和 manifests。最后自动生成并验证独立的 promotion decision JSON，但必须人工批准 release。

建议独立新增审查通过后才存在的入口：audit/csi1000_layered_search_core.py（纯 Python 枚举、哈希和阶段决策）；audit/csi1000_layered_search_modal.py（Modal orchestration，支持只读 source / 独立 output Volume）；audit/csi1000_layered_search_reconcile.py（离线费用与结果验证）；tests/test_csi1000_layered_search_core.py（合成负控）。不得直接修改旧 Stage-A/B 主 runner 或 production cron 以获得「省事」的复用。

### 7.2 高效调度和成本模型

- 最小先行：本地 tests→P0 control→L1-A 小试。worker=2，确认 per-cell wall time、CPU 实际、内存、Cython 初始化/MLflow 多 worker 隔离；再提高至 4/8。并发只缩短挂钟时间，**不等于**节省总 CPU/GiB 秒。避免 8 个以上 worker 同时重复 image build/下载、Volume 争用。
- **两级缓存**：信号级缓存 key = (code_sha, provider SHA, PIT/feature/label SHA, candidate/params SHA, training cutoff, retrain calendar, phase, seed, version, prediction SHA)；回放级缓存再加入 (TopK,DropN,execution schema,quote-union SHA, fee/limit/factor rules, date interval, initial cash, engine version)。必须严格 SHA+版本重新验证，mtime 或文件名相同不能视为缓存命中。
- 训练提案层优先**多保真筛选**：4×20 日折只训练最少候选，再挑几套做双 phase 长期 rolling，最终五 phase 决赛；若用 Optuna TPE/Successive Halving，仅能在已预注册的阶段和指标上提出/剪枝，不把 Stage-B 的 2025–2026 已见成绩冒充 unseen validation，不在最终决赛依据部分结果剪掉落后候选。
- 为避免当前仓库此前的 Modal nested-remote/hydration 失败：需要新 app 调用其他 Modal apps 时用**可审计的 local entrypoint/CLI 分批边界**；不要在一个 remote function 内直接用未 hydration 的 sibling remote；编排避免重复启动既有 App。
- **非 L1-B 原始预算流程**：首批实测后成本估算公式：P90 预测本批费用 = Σ 每种任务的 P90 实际 (physical_cpu×elapsed_s×当时费率 + GiB×elapsed_s×当时费率 + 其他已显示资源费用) + 已测 build/start/collector 开销；必须调用 operator 工作区当前真实 modal billing rates/report，**不得使用旧示例定价当作现价**。保存每个 batch 的预估 vs 账单校准与日累计剩余额度。
- 不先承诺 16 格 / 264 格 / 1,200 fit 一定可在 USD30 内做完：预算是**硬止损，不是保证能跑满**。按真实成本自动缩小候选数量，停在合理的可核验阶段；若成本不足以得到最小五相位证据，则以 Baseline 决策收尾。

### 7.3 通过 OpenCode 的 Modal CLI 运行：强制前台，禁止 detach

**硬规则：只要通过 OpenCode 发起 Modal 作业，绝不对 modal run 使用 --detach。** 已观察到 OpenCode 工具超时导致 detached Modal App 被中断/取消；也不可用 nohup、shell & 或其他不受追踪后台进程规避。保持**前台连接**，设置 OpenCode 工具 timeout 长于最坏队列+镜像构建+任务执行+结果收集+日志上传耗时，无法保证时**不要启动**，将任务预先切成已 review 的有限批次。

下述示例只定义**未来实现后**应提供的 CLI 形状；文件、参数尚不存在，并非现在可运行的命令：

~~~bash
# 此处为历史 P0 示例；L1-B 不需要每批 P90/审批，改见本文顶部修订与 operator README。
# 先由操作员显式确认本次审批的预算，并设置充分长的 OpenCode tool timeout
export ESTIMATED_TOTAL_USD="2.00"
set -o pipefail
modal run audit/csi1000_layered_search_modal.py \
  --stage p0 \
  --max-workers 2 \
  --estimated-total-usd "$ESTIMATED_TOTAL_USD" \
  --run-id "<pre-registered-run-id>" \
  2>&1 | tee /tmp/csi1000-layered-p0.log
# 同时保存真实 CLI exit code、Modal App ID、UTC 起止、server-side log 与账单明细
~~~

原始 P0/L1-A CLI 需支持 --help、--stage、--run-id、--max-workers、--batch-limit、--estimated-total-usd、--resume-from-manifest 和 --dry-run / --inventory 等经测试接口；L1-B 不需要 --estimated-total-usd/--ledger/--cost-quote，但必须先使用 --smoke；**实际以新 runner 的真实 --help 为准，不得假定这些 flags 已有**。若单批 P90 超过工具 timeout：先减小单批任务数/训练日历区间且保留严格 checkpoint 依赖和排序，或停下来取得**受批准的长驻前台执行机制**；不可先派发大批，再由 CLI 强退并宣称作业成功。

### 7.4 Modal Volume、身份、作业隔离与恢复

- 研究读取原 qlib-cn-data Volume 必须 create_if_missing=False，逻辑只读（挂载本身不是不可写 ACL）；禁止源 Volume commit、prepare、数据下载/刷新、put/rm/rename/cp，以及训练或结果写入源路径。独立可版本化 research Volume / prefix：例如 csi1000_layered_search/<protocol_sha>/<run_id>/<stage>/<unique_cell_key>/。
- 每个单元不可覆盖，采用原子 publish：写新隔离临时目录→写全套 artifacts→记录 byte SHA256/content hash/来源→提交独立研究 Volume；同 key 已有结果仅在全部来源及输出 SHA 一致时 RESUME_VERIFIED，不一致 FAIL_CONFLICT，不覆盖旧版本。
- 并发不能共享可写的 MLflow tracking path/cache/临时工作目录；为每个 worker 使用唯一隔离 trial id，worker 失败时区分 Python MLflow import/Cython 环境事故、用户取消、真实数据 BLOCKED；失败 App 保留其真实日志/费用，不把其 BLOCKED 统计吞并为最终 PASS。
- 原始/成功 App ID、真实退出码、UTC 时间、队列/构建时间、P50/P90 单格耗时、CPU/GiB、成本、费用快照、恢复格数量、原始失败记录、代码 SHA、镜像/LightGBM/pyqlib 版本必须形成 JSON run card；**不得丢掉首个中断尝试的花费**。
- 禁止推送凭据、私有 workspace/profile 名、未经脱敏的本地目录、原始私有行情/训练文件。GitHub 发布的是 SHA 索引、参数、每格比较/日期级指标、小型经脱敏 derived outputs；保留完整研究文件在授权 Volume 内并提供审核所需的可追溯路径。

## 8. 阶段验收、任务拆分与必须提交的产物

### 8.1 新任务实施顺序（严格依赖）

**PR-S0 合同实现与负控**：本合同经过 review、确定搜索空间和人工预算授权→开发 core/Modal runner 和测试→静态 CI 与 P0 real workspace smoke→只有 P0 PASS 才能开始 L1。旧 PR #64/#65/#66 不作为可自动写生产的许可证。

**PR-S1 成本与组合搜索**：L1-A → 可选 L1-B → 仅合格才 L1-C；每步独立结果目录、真实账单/清单、初筛结论，必要时 STOP_L1。

**PR-S2 模型与结构搜索**：独立执行 L2 上限→L3 有条件结构变体，复用仅按 cache key 验证；不得用新模型改写既有 checkpoint。

**PR-S3 决赛与产品选择**：冻结 ≤6 配置名单和 sha→完成严格五 phase→输出全试验对照表/阈值/费用→人工 review 明确 PROPOSE_PRODUCT_PROFILE_VERSION 或 KEEP_BASELINE_FOR_PRODUCT_SELECTION 或 STOP_PRODUCT_BLOCKED。然后单独 PR 接每日选股/网站，不把实验 PR 直接变生产。

### 8.2 最小交付文件格式（新项目以机器可读为主）

- protocol.json：本合同 commit SHA、方案版本、批准人/时间、冻结参数集合/种子、phase schedule、账户费用、日期和风险门槛、总成本/fit 上限。
- source_manifest.json：研究源候选×phase×signal/checkpoint/provider/quote/PIT/feature/model sha，缺失明确 BLOCKED_SOURCE，不静默替换。
- trial_ledger.jsonl：每次 proposal、所有被拒及被剪枝候选、阶段、来源、预估/实费、fit 次数、App ID、start/end/exit、SHA、状态、失败原因，append-only。
- cell/<key>/：profile/config/params SHA、source SHA、固定 signal SHA、order_delta/fees、独立纸面每日 NAV、trade/no-fill、完整 benchmark-reference、metrics、账本和 sha；只上传允许公开的派生文件。
- stage_{p0,l1,l2,l3,l4}_summary.json：每层完整名次/未完成/晋级依据/成本、预算余额、决定是否继续和实际复用数量。
- decision.json + decision.md：唯一胜者或 Baseline 回退、五 phase 可比表、每条门槛 PASS/FAIL、所有尝试总数、真实花费、历史过拟合/交易认证限制、人工审阅链接、网站展示口径与回滚目标。
- SHA256SUMS.txt、真实 Modal CLI/server logs、billing reports、GitHub commit SHA、测试失败负控；旧证据保持原样。

### 8.3 审核门禁

- **工程 PASS**：P0 控制重现，合成负控逐项 PASS，完整候选账本/真实日志/来源 SHA，研究只读与生产零改动，预算和计数器 fail-closed。
- **研究 PASS**：按预注册阶段比较所有候选，不藏失败，决赛相同会计、至少 Baseline 五 phase 一致，胜者满足 §1.2；所有历史数据使用边界、成本压力和偏差已公开。
- **可产品化 PASS**：唯一完整策略 profile 及固定回滚配置、模型 checkpoint 来源、每日 PIT/label/as-of/真实价格数据新鲜度、Top20 排名/实际持仓差异说明、独立 paper 日度净值、网站历史回测与上线后净值分离，至少一次真实交易日端到端 smoke。无此门禁不能把策略选择结果宣传为已经部署。
- 不能因 CI 绿就推断真实 Modal workspace 已运行；没有实际私有源访问只交研究设计/合成模拟，不能补造 real run。

## 9. 新任务启动指令（复制到新会话）

> 请以 docs/experiments/53-csi1000-budgeted-layered-strategy-search-contract-20261012.md 作为唯一的本轮 **CSI1000 分层历史策略搜索**启动合同。先核对 PR/主线实际合并状态、冻结 Baseline/Stage-B 纠错 55 格与 Stage-A 80×4 证据、原 provider/signal SHA 和相关 AGENTS.md 约束；**不要声称已经运行新的搜索**。先提实现 PR-S0：开发纯 Python 搜索配置/预算/判定 core、Modal P0/L1 runner、逐格 SHA 缓存/负控/真实成本账本与 read-only 隔离，给出可审阅的真实 CLI --help、测试和 STOP gate；明确申请本轮新 USD30 预算批准后，操作员使用 OpenCode **前台 modal run（禁止 --detach）** 在授权 workspace 运行 4 格 P0 和小规模 L1-A，回传真实 App ID、日志、账单及结果。随后严格按合同 L1→L2→L3→L4 执行，绝不超过分层/全局预算与 1,200 fit，没出现全门槛优胜者即 KEEP_BASELINE_FOR_PRODUCT_SELECTION，禁止自动切换当前 Winner/历史文件。研究只需历史产品择优，不要求等待 120/240 天前向数据才能完成网站项目。最终交付可上线的完整策略版本、独立 paper 与网页显示合同，并以独立 release PR 决定生产变化。

---

**本文件只授权提出一项分层、有上限、可被审查的历史产品选型研究建议；不追认旧研究为 OOS、不给任何未来收益保证、不批准已经上线、也不给 Modal 自动开支许可。**
