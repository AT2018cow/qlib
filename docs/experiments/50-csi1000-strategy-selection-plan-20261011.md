# CSI1000 下一阶段策略择优工作计划（新对话执行版）

**制定日期：2026-10-11（周日，Asia/Singapore）**  
**仓库：** https://github.com/AT2018cow/qlib  
**本次核对的 `main`：** `43a1e55c144b163cee9682b7ed1f665fbfc51b3e`（PR #63 已合并）  
**目标：** 在不污染未来测试、不重复已完成训练的前提下，寻找当前研究协议下**证据最充分的 CSI1000 策略**，而不是声称数学上找到全局最优。

> **执行原则：** 历史纠错与未来择优分开；修正历史是诊断，不是新样本外实验。真实生产 canonical 暂不切换；仅研究模拟盘，不做实盘下单；研究阶段按用户要求不做委托簿深度/资金容量模拟，但 ST、IPO、涨跌停、停牌及价格/估值、因子和前视偏差检查仍不可跳过。

## 1. 现状与不能误解的事实

1. 原策略是 CSI1000、Alpha158、LightGBM、20 个交易日未来原始收益标签、2016 年起扩展训练、252 交易日验证、20 交易日重训、每日 T 收盘生成排名、T+1 开盘模拟执行、Top20/Drop2、1 亿元模拟资金。历史调参仅改变 7 项 LightGBM 超参数，没有搜特征、标签、持仓数或换手规则。
2. **Stage-A：** 12 套 smoke 后已完成 80 套参数 × 4 个短时窗（每窗约 20 执行日）共 320 个 candidate-fold 结果。原 Stage-A Baseline 排名 48/80，但**原排名不可直接用于目前策略择优**：`csi1000_tuner.py` 的 `_run_signal_backtest` 使用 `research_exchange(..., codes=MARKET)`，其中 `MARKET='csi1000'`。这是与 Stage-B 已确认 F1 缺陷同类的动态指数成分报价覆盖路径。具体各 Stage-A fold 的影响**尚未量化**，不应断言 320 组全部有误，也不应断言不受影响。
3. **已有可复用数据：** 原 Stage-A 的每个成功 candidate-fold 在 Modal Volume `qlib-cn-data` 的 `/vol/csi1000_tuner/stage_a/<Stage-A snapshot>/<candidate_id>/<fold_id>/` 中保存 `result.json`、`signal.parquet`、`report.parquet`、`decisions.json` 等；Stage-A snapshot token 为 `a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b`，与 Stage-B 快照**不同**。GitHub 已有汇总 JSON，原始大文件可能仅存于授权 Modal Volume；不能假定在公共 GitHub 内直接可读。先用 manifest/CLI 逐件盘点。
4. **Stage-B：** 原 10 套 Stage-A 晋级参数 + Baseline，在 2025-01-02～2026-09-30 的 5 个重训相位，共 55 格。该段数据已经被用过。零重训报价修正回放完成 55/55，诊断排名 Baseline 1、原 Winner 10；**旧 Stage-B 1→10、5→1 的修正结果不能成为新 OOS 证明**。不重跑已完成的 55 格历史审计。
5. **生产状态：** `CANONICAL_PROFILE=stage_b_winner`，paper lineage `stage_b_winner_canonical`，代码没有自动切换；原 Winner candidate `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`、model-config SHA256 `0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88`；Baseline candidate `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`、SHA256 `793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3`。
6. PR #62/#63 已合并，统一 paper 账本、期初/收盘 NAV 核对、日期化停牌证据、交易日连续性及收益验收脚本齐备。但 CI 不代表真实 Modal 部署或真实结算：首个可合格 usage day 是 2026-10-12；由于须等待 10 月 12 日收盘行情刷新，第一条 `execution_report.date >= 2026-10-12` 的有效收益记录正常可能于 2026-10-13 发布。周日无交易。**不要把历史旧 pending 订单误写成前向收益。**
7. 文件 `docs/experiments/47-csi1000-forward-shadow-strategy-decision-protocol-20261011.md` 已事先注册**恰好两条**前向候选：Winner 与 Baseline。它禁止在同一前向统计实验中临时加入其他参数或回填数据。扩大到更多参数必须另立独立、前瞻性版本协议，不得事后更改 47 号的比较对象。

## 2. 总体工作流和优先级

| 阶段 | 核心动作 | 性质 | 可开始时间 | 完成标准 |
|---|---|---|---|---|
| P0 | 检查 main、数据、Modal 权限与运行状态；冻结实验范围 | 输入/安全门禁 | 立即 | 快照/文件/权限/时间戳清单，关键路径可读 |
| P1 | **Stage-A 320 格零重训、两种执行口径对照回放** | 历史纠错与诊断 | 立即 | 320/320 或逐格有明确失败原因；新旧订单/NAV/指标/排名/哈希留证 |
| P2 | 修正后 Stage-A 80 参数稳定性审查，结合已完成 Stage-B 55 格 | 历史诊断 | P1 后 | 明确候选家族及不确定性；**不能**直接批准生产切换 |
| F1 | 建立 Winner vs Baseline 预先冻结的独立前向 Shadow | 真正新证据 | 立即准备，最早下一个可预先冻结的交易日 | 双模型信号在结果出现前固定、两套 1 亿元独立 paper 账本、完整审计链 |
| F2 | 按 47 号协议积累 120/240 个有效配对交易日 | 推断和决策 | F1 实际启动后 | 预注册统计门槛/风险门槛、95% 覆盖率、人工批准或维持现状 |
| N（可选） | 新模型结构/更多候选的下一代研究 | **新实验** | 只有在另行注册后 | 有真正未来未见测试区间、独立版本、预算和多重比较约束 |

**P1 与 F1 可以并行推进**；如果在下一个真实交易日到来前无法完成双轨时间戳、验证和保存，就从更晚的合格日期启动，不能回填早先的赢家/基线预测。

## 3. P0：准备与不可变输入盘点

1. 核实 PR #63 merge，`git checkout main && git pull --ff-only && git status --short && git rev-parse HEAD`；单独研究分支提交变更，禁止直接覆盖历史研究、production config、持仓状态。
2. 检查 `docs/experiments/{16,17,18,30,34,35,42,46,47,48,49}-*.md`（实际路径见第 10 节）；弄清 Stage-A 与 Stage-B 数据源指纹和范围，不能混淆两套 snapshot。
3. 在有授权的 Modal workspace 中盘点 Volume、数据日期、320 个源文件和 SHA：包括所有 candidate_id、fold_id、真实路径、文件大小、SHA、是否与汇总 manifest 一致、未更改的 mtime。因 Stage-A baseline double-fit，确认 canonical baseline 的 `repeat_b` 身份及原始目录，而不是误把 `repeat_a` 当正式输出。
4. 记录环境版本：Qlib、LightGBM、Python、numpy、pandas、原研究代码 commit、provider 指纹；无权限读取原 Volume 时标记 `BLOCKED_SOURCE_NOT_AVAILABLE`，不要下载新的 provider 冒充同一快照。
5. 研究主目标定义为“已有 80 个参数中哪个更有可验证的潜力”；**历史回测诊断、前向统计显著更优、最终生产批准是三种不同的状态**。还原已有 Stage-A 原排序只作为旧流程对照；不以原名次剪掉未修正候选。

## 4. P1：实现并运行 Stage-A 零重训纠错回放（首个工程 PR）

### 4.1 一次性、小范围实现

新增一个隔离脚本（**当前仓库尚不存在**，文件名建议 `audit/replay_stage_a_corrected.py`）及对应 Modal 运行包装器（建议 `audit/stage_a_corrected_replay_modal.py`），而不是修改原 `csi1000_tuner.py` 后直接重新运行完整调参。可以借用现有 `audit/replay_stage_b_f1.py`、`audit/run_stage_b_corrected_rerank.py`、`execution_quote_universe.py`，但务必适配 Stage-A **20 日 fold**及其冻结报告结构。

对每个 `(candidate_id,fold_id)`：

- **输入校验：** 元数据、原 `signal.parquet`、`report.parquet`、`decisions.json` 文件全部存在；逐项 SHA256、原 provider snapshot、候选参数、信号日期与四折时间边界匹配。非法/missing → 单格 `BLOCKED`；批量不能悄悄从 `80×4` 缩成更小样本。
- **Legacy 重放：** 原始日度信号绝不重训，先在旧 `codes='csi1000'` 路径重现原模拟报告、决策次序、费率、成交与 NAV。设置合理机器精度阈值并锁定；如果不能复现，不允许展示“修正改进多少”。
- **Fixed 重放：** 在**完全相同冻结信号**和日期、成本、Top20/Drop2、1 亿元初始账户下，仅将 Qlib Exchange 报价范围改为 `execution_quote_codes('csi1000', fold_start, fold_end)` 得到的固定历史成分**报价并集**；每日可买股票及模型预测范围仍严格使用原始 PIT 排名，不能用并集扩大历史买入池或未来入指股票。
- **独立核算与负控：** 检查资金守恒、现金/持仓/日 NAV、成交成本和份额；验证不缺报价/无捏造开收盘价；抽取一只退出指数后的旧持仓并模拟其报价必须继续可查、只修改行情订阅不改变信号；故意损坏文件 SHA、错日期、错 provider、缺因子、文件冲突、重复结果时均应 fail-closed。
- **导出：** 逐格 `legacy_reproduction.json`、`fixed_metrics.json`、`order_delta.json`、`daily_nav_delta.csv`、文件 SHA/来源；批量汇总 `stage_a_320_corrected_batch.json`、`stage_a_80_corrected_ranking.json` 和 `source_manifest.json`。同时保存原指标、固定指标、差值、模型参数、四折结果、rank 变动和不确定性说明。
- **重要限定：** 这一步只是用纠正报价口径做**诊断性 Qlib 重放**，不是 PR #62 PaperPortfolio 内核对真实历史 PIT/官方停牌的全覆盖重建，更不是实盘成交认证。阶段结束文件明确 `training_performed=false`、`promotion_authorized=false`、`historical_tail_consumed=true`。

### 4.2 先做最小烟雾，再全量

1. 本地纯 Python 小测试和 `python -m pytest -q <new test file>`：哈希负控、静态报价代码并集、日期对齐、收费/NAV、幂等、旧旧复现。
2. Modal 在同一原始 provider 快照上先测试 Baseline 的 4 folds + 原 Winner 的 4 folds；如 Stage-A 旧执行口径无法准确复现或关键报价不可得立即中止。
3. 再随机/固定地取若干高/低原名次候选看错误是否系统性偏置，而不是仅关注修正后可能成为 Winner 的候选。
4. 全量 `80×4=320`；运行期间只追踪完成率、异常、成本，不中途改变指标定义/筛选条件；恢复时仅验证并复用 hash 稳定的已完成格。
5. 每份汇总都经另一脚本重新计算 worst fold、Q25、median、正向 fold 占比、IR、Sharpe、MDD、费用及换手；排名仍使用**已冻结的 Stage-A 字典序指标**，不用纠错结果另定新排序指标。

### 4.3 缺失原始 signal 的处理

优先确认原本 320/320 完整性。若缺失原始 signal，记录确切缺口和影响；**不能**靠当前重新训练生成新的预测然后宣称“同一条旧信号”。必要时单独提一个最小补算的审批与隔离版本，使用原训练配置、原 provider、原训练截止时点和固定随机种子，并标注 `REFIT_RECONSTRUCTION_NOT_ORIGINAL`；不能覆盖原快照，且重训输出不自动混入“全部原样复用”的主结论。

## 5. Modal 具体操作手册（只读源数据，单独研究资源）

**OpenCode 前台执行硬规则（适用于后续所有实验指南）：** 由 OpenCode 工具发起的 Modal 实验命令不得传入 `--detach`，必须保持 `modal run` 前台附着直到真实退出。在该操作环境中，脱离运行可能受工具超时影响而中断。启动前配置 OpenCode 命令的超时，预留镜像构建、排队、工作单元、汇总及输出下载耗时；推荐 `set -o pipefail`、`modal run ... 2>&1 | tee /tmp/<run>.log`（注意真实命令中替换占位符）。若当前工具调用无法覆盖预计总时长，则停止启动，先安排可持续的前台会话或另外审核的分批方案；不能用 `--detach`、`nohup` 或 shell 后台符号 `&` 绕开。运行时保留 CLI 日志、退出码、Modal App ID、实际账单，超时/断线按 `RUN_STATUS_UNKNOWN` 处理，不得宣称成功。此限制**不影响**冻结 Git SHA 所用的 `git checkout --detach`。


**运行前提：** 在有原 `qlib-cn-data` Volume 访问权限的 Modal workspace；`modal` CLI 已安装并完成用户自己授权的 token/profile；任何账户 token / Secret 均留在授权 workspace，不复制到 GitHub、日志或新对话。

### 5.1 现成、可直接执行的盘点命令

```bash
# 仓库根目录；先确认干净和源版本
git checkout main && git pull --ff-only
git status --short
git rev-parse HEAD

# Modal 当前授权身份、CLI、用量及费用
modal --version
modal profile current
modal volume list
modal volume ls qlib-cn-data csi1000_tuner/stage_a/
modal volume ls qlib-cn-data csi1000_tuner/stage_a/a2ecd8d1cadf404670762a81b1fd33a377f2ef2aaf27a0d9ac6787366defd65b

# 只下载小型、可公开审查的 manifest；不要下载整个 provider 到 Git
mkdir -p /tmp/csi1000-source-manifests
modal volume get qlib-cn-data \
  csi1000_tuner/stage_a/provider_snapshot.json \
  /tmp/csi1000-source-manifests/stage_a_provider_snapshot.json

# 真实计费价格、运行账单（某些计划可能不开放报告）
modal billing rates
modal billing report --for "this month" --show-resources
# 若 billing report 没有权限，以 Modal Dashboard 账单页面为准
```

`modal volume ls/get` 是官方只读查询/下载命令；严禁对原 Volume 使用 `put`/`rm`/`cp`（写入）/`rename`。若不能读取原路径，先诊断 workspace/profile 和路径，不创建一个名为 `qlib-cn-data` 的空 Volume 冒充原数据。

### 5.2 待首个工程 PR 实现后，才可运行的命令模板

> 以下文件名和 `--scope/--max-workers` **是计划中的新接口，不是现有可直接运行命令**。新对话需按这些契约实现并提供 `--help` 后再执行。不要运行原来的 `modal run csi1000_tuner.py --expanded --candidate-count 80`，它包含模型训练、可能更新 provider manifest，违背零重训与不可变历史要求。

```bash
# 独立存放纠错结果，绝不覆盖原始训练/论文结果
modal volume create qlib-csi1000-research-audit

# OpenCode 必须保留前台会话、预先延长工具超时；确保 pipefail 保留 Modal 真实退出码
set -o pipefail
# 先 smoke，核查 Baseline/Winner 各 4 折，成本和日志
modal run audit/stage_a_corrected_replay_modal.py \
  --scope controls --max-workers 2 2>&1 | tee /tmp/csi1000-stage-a-controls-modal.log

# 8 格和 16 格通过、报价/PIT 门禁批准且预算复核后，前台运行 320 格：
# 下面是工程实现后的计划用法；不要在未验收、工具超时不足时运行。
modal run audit/stage_a_corrected_replay_modal.py \
  --scope all --max-workers 8 \
  --estimated-total-usd <ACTUAL_TOTAL_ESTIMATE_LE_20> \
  2>&1 | tee /tmp/csi1000-stage-a-320-modal.log

# 如需查看进度/日志，先用 modal app list 找到独立研究 App 名称
modal app list
modal app logs <research-app-name-or-id>

# 完成后用只读方式查看和取回研究结果
modal volume ls qlib-csi1000-research-audit
modal volume get qlib-csi1000-research-audit \
  <new-versioned-result-dir>/stage_a_320_corrected_batch.json \
  ./stage_a_320_corrected_batch.json
```

**Modal 包装器的设计要求：** 读取原 Volume `modal.Volume.from_name('qlib-cn-data', create_if_missing=False)` 并挂到只读用途的源目录；把所有新输出提交到**另一个**专用研究 Volume 或外部只读可核对文件；不调用源 Volume 的 `commit()`，不调用其数据刷新 `prepare.remote`，不触发训练 `.fit()`，不对原 Volume 创建任何临时文件。源 Volume 物理上可能支持写入，所以必须由程序硬性限制路径和操作；别以为挂载等同于存储层只读 ACL。新结果目录必须包含 git SHA、原 provider SHA 和实验版本，冲突时拒绝覆盖；`vol.reload()` 前确保没有未提交修改，避免不小心提交。

**并行度：** 先 2→8→16→最多 24 个 2–4 CPU 小 worker，随着实测吞吐再调。Starter 100 容器是 workspace 上限，不代表原本 8 核/24GiB worker 能同时开 100 个；高并发可能带来大量 I/O/Volume 竞争。回放是**零训练**，以总 worker-秒核算成本，不应套用“80 组重新训练”的价钱。

**定量止损：** 先记录 8～16 格真实耗时，计算 `预测全量计算费 ≈ 单格 worker 秒数的均值 × 320 × (cpu×0.0000131 + GiB×0.00000222)`（美元；不含其他资源/开销），同步核对 Modal 当时实际费用；建议本轮诊断预算**初始上限 $20**，如需超过则先说明原因和请求批准，不能直接最大并发跑完整轮。当前 Modal 官方公布的基础费率 CPU $0.0000131/物理核/秒、内存 $0.00000222/GiB/秒；Starter 月计算额度 $30，是否剩余取决于实际工作区账单。不要宣称已验证当前 workspace 的剩余额度。

**重试和恢复：** 按 `(snapshot,candidate,fold,run-version)` 设计不可覆盖的幂等输出；异常记录而不是跳过；先重验已完成结果的来源哈希，再恢复缺格。不要同时启动两个相同批量作业，更不要为重启覆盖 result.json 或原始信号。记录 `modal app` 运行 URL、时间、CPU/内存配置、异常与费用。

### 5.3 费用管理参考

- 只读盘点和少数回放的成本通常远低于重训，但**先实测**，不事先保证具体完成分钟数。
- 如果研究范围转为真正 80×4 重新训练，则约 324 次 fit，属于新预算；目前**不批准**。
- Modal 官方 CLI 参见 https://modal.com/docs/cli/latest/run 、https://modal.com/docs/cli/latest/volume 、https://modal.com/docs/cli/latest/billing；定价 https://modal.com/pricing 。

## 6. P2：纠错后如何分析 80 套模型

1. 对每格输出 `candidate_id/fold_id/model_sha/legacy_vs_fixed/return/CAGR/relative_CAGR/Sharpe/IR/MaxDD/turnover/cost/no_fill/coverage/orders/account_reconcile`；比较原排名与修正排名及重大差异日。
2. **保持原 Stage-A lexicographic 目标不变**：最差 fold 相对超额 CAGR、Q25、median、盈利 fold 比例、IR、Sharpe、最差 MDD、换手/费用，按冻结排序规则排名；运行 LOFO（每次删一个 fold）稳定性评估。Stage-A 的四个短窗口不是正式长期投资收益证明。
3. 对 80 套修正结果全部汇报，而不是仅输出前 10。单独列 Baseline 处于哪一个分位、历史利润是否只来自某一段、模型复杂度、样本外时间段是否重叠、是否集中于类似参数家族。
4. 将已经存在的**11 套 Stage-B 55 格修正诊断**按相同候选 ID 对齐，标注已消费历史且两种实验不完全可比。**不要**因 Stage-A 修正排名改变，就在 2025–2026 已消费尾部新增大规模筛选、再挑当期最佳；这会产生新的择优偏差。
5. 形成三类标注：`HISTORICAL_DIAGNOSTIC_PROMISING`（仅历史信号）、`INSUFFICIENT_EVIDENCE`（缺数据/不稳健）、`CURRENT_CANONICAL_UNCHANGED`（任何历史指标都不得直接改生产）。输出候选与完整排名，不输出误导性“已认证最优”。
6. 历史独立成交验证不重开冻结 55 格的陈旧问题；除非发现当前生产 P0，或准备真实实盘执行，历史 ST 真实四笔 SELL 和 benchmark 外部价格核验继续按残余登记表处置。用户已明确不做委托簿深度/容量压测；这一简化不能扩大为放弃日历、停牌、因子、价格与账本准确性。

## 7. F1/F2：真正能决定换策略的前向实验（第二个独立 PR）

现行 47 号协议是**只比较已冻结的 Baseline 和现役 Winner**，必须先完成受控集成：

- 使用 Winner `stage_b_winner` / `stage_b_winner_canonical` 保持现役生产 Top20、网站和持仓状态不变；另建 `baseline_control` / `stage_b_baseline_shadow_v1` 研究信号与**独立** 1 亿元 paper 账户，不能从 winner 或历史 baseline 借用现金、持仓、净值。
- 两腿完全相同的 T-close→T+1-open、Top20/Drop2、已知交易日历、PIT 股票池、20 日标签成熟、前一交易日数据截至时点、成本（买 5bp、卖 15bp、最低 ¥5）、ST/涨跌停/IPO/停牌规则；同一 provider 版本和时间戳。记录每条腿独立的模型训练截止、checkpoint SHA、配置 SHA、随机种子、features/universe 哈希。
- 每个 `usage_date` **在当天收益和成交结果可得之前**固定两条完整排名、信号、paper pending order 的内容哈希、UTC 创建时间和前一个日报哈希，防双写/补写；对缺腿、补发太晚、超前标签、错代码身份、数据/日历漂移、重复结算做负控。晚启动就晚启动，不允许 10 月 12 日结果可见后补一个“10 月 12 日事先预测”。
- 真正结算仍走 PR #62/#63 统一 paper engine；按已发布的日历对齐每日两腿 cash/positions/NAV/费用，缺交易日就缺，不能丢掉差表现日。Shadow 漏报不能污染 Winner 生产，且 47 号文要求的实验对可比性必须严格判定。
- 正常运营仅监控错误、异常成交和数据完整性，**禁止**提前计算用于选择模型的任意子样本胜负；第一次正式统计在 120 个合格配对交易日，第二次仅在需要时 240 日；全区间覆盖率 ≥95%。
- 统计按 47 号协议：`delta_t = baseline净日收益 - winner净日收益`；效应量 = `238 × mean(delta_t)` 年化算术百分点，不是 CAGR。固定 5 日 moving-block bootstrap、10,000 抽样、固定 RNG seed `20261011`，120 日第一次单侧 99% 下界、240 日第二次单侧 96% 下界。下界 >0 且点估计至少 +3.0 年化百分点；模拟交易成本额外 +10bp 压力后仍为正；+20bp 报告但不作为唯一否决；Baseline 最大回撤不能比 Winner 深超过 5 个百分点、median turnover 不高于 Winner 的 1.25 倍；实际结果满足所有硬门禁后**仍需单独审议和人工批准 PR**。
- 上述对比只对**Baseline 相对 Winner**形成显著性结论，不允许据此证明 80 个候选中“最佳”，也不允许把事后修正的历史 Stage-A 排名补入统计比较中。

## 8. N：若目标扩大到“80 套中最优”，怎样合法开展下一代实验

只有完成 P1/P2 并确认科研价值后，新建一个与 47 号协议**统计隔离**的新研究注册文档：冻结候选全集、评价指标、模型家族、数据使用边界、相位/重训时钟、费用、算力预算、统计检验与多重比较计划，保存 PR 时间戳。旧 Stage-A（2021–2024）和已消费 Stage-B（2025–2026）只能用于开发/诊断，**不能再称未见 OOS**；新模型需要真正未来未使用的预测/回报时间区间，或事先未看过且经证明的独立数据。对不稳定候选不凭旧表现加权“挑中一个”，不为补救不达标结果追加新参数和反复选择截止日期。

在新的前瞻性测试中，模型继续使用共享的 PIT/标签/交易/费用和统一记账口径，所有预言式的日度排名在结果揭晓前固定存证。若有 K 个候选，须事先处理 K 次比较引起的选择偏差（独立锁定测试/多重比较约束）；不要把对一个 Winner 的 47 号检验偷换成 K 个模型大擂台。评审维度同时有年化净收益、风险调整表现、最差市场段、MDD、换手、成本压力、现金/持仓真实性和稳定性。**无法用有限历史样本保证数学意义的全局最优，只能确认当前证据下的最优可用版本。**

## 9. PR、产物、责任和验收标准

**PR-A：Stage-A corrected execution research replay**（优先，最小可执行工作包）
- 源快照 hash 盘点、仅读实现、单元/负控、controls/320 回放、差异与排名汇总；CI 使用合成数据，无 private provider；Modal 授权环境的 320 格结果作为独立证据接入，保留原始报告不改写。
- 合并门禁：CI 绿 + 原环境 legacy 完全复现 + 修正版差异可审计 + 320/320（或明确数据阻断）+ 不改 canonical + 不做训练。

**PR-B：Winner/Baseline prospective dual shadow**（可以和 PR-A 并行，但不得混淆研究数据）
- 47 号预注册合约、双候选冻结信号、独立账户和账本、不可回填哈希链/时间证明、负控、实行情境 smoke、运行与失败恢复手册。
- 合并门禁：重复运行幂等、PIT/标签成熟/相位配置一致、两腿隔离、错漏拒绝、生产 Winner 原文件名/净值/状态未修改；合并代码不等于 Modal 部署，须授权实际操作员验证。

**PR-C（条件性）：New research protocol and prospective multi-candidate evaluation**
- 只有已经提交计划并明确真的新样本、预算后再启动；绝不从旧历史回放自动调整 canonical。

最终交付：一张**所有 80 组完整对照表**、四折及 LOFO 稳健性报告、每组源 SHA/执行差异、已完成 Stage-B 55 格交叉解读、prospective Shadow 的可信日报链和审核里程碑、正式的 KEEP/PROPOSE_SWITCH/APPROVED 决策文书。操作上以 `KEEP_CANONICAL_PENDING_FORWARD` 为默认。任何真实交易日数据缺失/PIT 漂移，先 fail-closed，不能假装已通过。

## 10. 需要优先读的原始仓库资料

- `docs/experiments/16-csi1000-stage-a-tuning-contract-20261007.md`：80 参数、四折、原排序；
- `docs/experiments/17-csi1000-stage-a-expanded-screen-runbook-20261007.md`：Stage-A 数据复用、结果审查；
- `docs/experiments/18-next-conversation-handoff-stage-b-20261007.md`：Stage-A 已完成 320/320 及研究谱系；
- `docs/experiments/30-csi1000-f1-quote-coverage-fix-validation-20261009.md`：F1 报价缺失的实证；
- `docs/experiments/34-stage-b-all-candidates-corrected-execution-rerank-20261010.md`：Stage-B 修正回放的做法；
- `docs/experiments/35-stage-b-corrected-rerank-results-20261010.md`：55 格修正后的诊断性排序；
- `docs/experiments/46-csi1000-audit-residual-register-20261011.md`：已经关闭/转移/延后的审计范围；
- `docs/experiments/47-csi1000-forward-shadow-strategy-decision-protocol-20261011.md`：唯一正式登记的 Baseline vs Winner 前向决策规则；
- `docs/experiments/48-csi1000-unified-paper-nav-20261011.md`：统一 paper engine；
- `docs/experiments/49-csi1000-forward-nav-acceptance-20261011.md`：真实 NAV 独立验收；
- 代码：`csi1000_tuner.py`、`csi1000_tuner_core.py`、`csi1000_stage_b.py`、`execution_quote_universe.py`、`board_execution.py`、`audit/replay_stage_b_f1.py`、`audit/run_stage_b_corrected_rerank.py`、`paper_portfolio.py`、`paper_accounting.py`、`forward_performance.py`、`csi1000_production_config.py`；
- GitHub Stage-A 汇总 `results/csi1000_tuner/stage_a_expanded_a2ecd8d1cadf4046.json` 与 `stage_a_expanded_audit_a2ecd8d1cadf4046.json`，具体原始 candidate-fold 信号以 Modal Volume 存证为准。
