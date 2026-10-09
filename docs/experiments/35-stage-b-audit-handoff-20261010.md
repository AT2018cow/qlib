# Qlib / CSI1000 Stage-B 审计工作交接

**交接日期：2026-10-10（Asia/Singapore）**  
**仓库：** [`AT2018cow/qlib`](https://github.com/AT2018cow/qlib)（`microsoft/qlib` 的 fork）  
**当前交接点：** 用户明确表示 **PR #50 已合并**，但要求在旧对话中**不要对 PR #50 合并做审核**；应在**新对话中首先审查 PR #50 合并后的主干代码及 CI**。此交接文档不代表对 PR #50 合并状态、合并提交或实际 55 组回放结果的再次核验。

## 一、最高优先级：快速给出策略与调参决策

用户明确希望：**尽快完成实质性审计、少开 PR、避免过度设计，尽快判定历史实验中是否存在系统性错误、是否需要重新选策略，以及是否需要重新调参**。

已可作出的决策：

- **暂停把旧 Stage-B winner 排名视为可用于生产晋级的有效依据。** 已确定历史执行逻辑中的 F1 缺陷会显著扭曲原 winner phase 0 的收益与风险指标。
- **不应立即重新调参或重训。** 优先在完全相同的冻结候选、冻结信号和原始 provider 上修正报价覆盖逻辑，复算并复核原候选的排名。若需要新调参，应使用新的、未参与挑选的验证区间，不能利用已经消费的 reserved tail 反复筛选。
- **目前并无被认证的“新 winner”。** 即便完成在已消费测试区间上的修复后重新排序，也只是诊断性的重评估，不是新的未污染样本验证或自动晋级许可。
- **真实市场可交易性仍 `BLOCKED`。** 历史 ST/停牌、IPO、涨跌停盘口/开盘流动性、点时数据及 20-session 标签成熟证据尚不足以批准生产推广。

## 二、最重要、已经核实的审计发现

### 1. F1：动态指数成分范围被误当成持仓报价范围

原 Stage-B 回测将 `codes='csi1000'`（动态指数成员）用作 Qlib Exchange 获取报价的范围。当持有股票退出 CSI1000 后，即使股票仍在市场正常交易，模拟交易所也会失去其行情，造成不能卖出、陈旧估值以及 Top20/Drop2 名额冻结等异常。

关键样本是 `SH603301` 和 `SH688066`，原 winner phase 0 的执行交易在 **2025-07-02 至 2026-06-29** 共 **240 个交易日为零订单**。修复方式是在**固定冻结执行区间**内使用历史曾入指股票的**静态并集作为报价订阅**，但仍必须只用冻结原始 `signal.parquet` 做选股，不得将这个静态并集作为买入候选或引入未来信息。

**Winner phase 0 已测得的原/修复诊断对比**（原 Qlib 信号冻结，无重训，不代表真实可交易）：

| 指标 | 原始错误执行路径 | 静态行情覆盖修复路径 |
|---|---:|---:|
| Sharpe | 1.276262 | **0.9988782409498526** |
| CAGR | 29.3178% | **23.4107%** |
| 最大回撤 | −12.1268% | **−27.6412%** |
| 全程执行交易日 | 424 | 424 |
| 订单数 | 482 | **1,696** |
| 上述冻结停摆区间订单数 | 0 | **950** |

Winner phase 0 的修复版另做过第二实现的账户/现金/持仓账本重建：424 日最大 CNY 账户数值差约 `7e-8`，日收益差约 `5e-16`，满足原审计规定的 **0.01 元 / 1e-10** 门槛。**这证明研究回放内部账务匹配，不证明真实交易所可执行性。**

### 2. PR #49：快速扫描十组历史报告，异常并非局限于 winner phase 0

十组冻结原始报告都经 GitHub 原始 Parquet 的 SHA256 核验，独立检查逐日换手（GitHub Actions 主干成功运行见下文）。**10/10** 组有不少于 **114 个连续交易日零换手**，**6/10** 组有不少于 **240 个连续交易日零换手**：

| Phase | Winner 最长连续零换手日 | Baseline 最长连续零换手日 |
|---|---:|---:|
| 0 | 240 | 306 |
| 4 | 114 | 180 |
| 6 | 114 | 306 |
| 10 | 114 | 306 |
| 15 | 307 | 240 |

**非常重要的因果边界：** 仅 winner phase 0 的 F1 机制已通过原始信号、订单和 provider 复核确认；其余九组存在真实的长时间零交易异常，但**尚未逐笔验证根因是否也是 F1**，不能用这个报告直接外推修复后 Sharpe。

### 3. 策略数量的关键澄清

原 Stage-B 不是仅比较 winner 和 baseline。冻结结果实际上包含 **11 个候选策略（Stage-A top 10 + baseline）× 5 个 phase = 55 个候选/阶段执行单元**。如果目的是真正决定是否需要**重新选择策略**，最终需公平核验这 **55 组**，不能仅比较 winner/baseline 10 组就宣布新的全局 winner。

原始排序遵循 `csi1000_stage_b_core.py` 内的 `phase_ranking_contract()` / `rank_stage_b_candidates()`；它综合五阶段最差相对超额 CAGR、分位数、中位数、正超额阶段比例、IR、Sharpe、最大回撤、换手、成本及 Stage-A 平局裁决。不要在已使用过的 reserved tail 上事后修改排序标准来挑新赢家。

## 三、PR 历史与当前进度

| PR | 作用 | 交接时已知状态 |
|---|---|---|
| [#45](https://github.com/AT2018cow/qlib/pull/45) | 修复/验证 F1 动态指数成员引发的报价缺失；winner phase0 冻结信号诊断回放 | 之前已完成/合并 |
| [#46](https://github.com/AT2018cow/qlib/pull/46) | F5 fail-closed 证据及独立账本验收门禁 | 之前已完成/合并 |
| [#47](https://github.com/AT2018cow/qlib/pull/47) | F5 手动 Actions 入口、离线证据校验、产物上传 | 已合并；后续 `main` F5 CI 成功 |
| [#48](https://github.com/AT2018cow/qlib/pull/48) | F6 十组 frozen original report SHA 及 signal/decisions 阻断检查 | 已合并；`main` CI 成功；只有 winner phase0 原始信号+决策一组完整 |
| [#49](https://github.com/AT2018cow/qlib/pull/49) | 十组原始报告长期零换手异常的快速 CPU 扫描 | **确认已合并**，主干 SHA `44de1e4072234d0d9c82e8b79c5462ad0fd7a513`，主干 CI 成功 |
| [#50](https://github.com/AT2018cow/qlib/pull/50) | **一次性批量重新回放全部冻结 55 组并作诊断性重新排序** | **用户报告已合并；按用户要求，本对话没有执行合并后审核。应在新对话第一时间核查。** |

可参考的真实 CI：

- PR #49 合并后 `main`：[Actions run 37958991928](https://github.com/AT2018cow/qlib/actions/runs/37958991928) — success，验证十组冻结报告及零换手统计。
- F5 `main`：[Actions run 37953654877](https://github.com/AT2018cow/qlib/actions/runs/37953654877) — success，证据负控与产物上传；不是完整 market replay。
- PR #50 合并前分支：[Actions run 37960167589](https://github.com/AT2018cow/qlib/actions/runs/37960167589) 与 [PR run 37960277147](https://github.com/AT2018cow/qlib/actions/runs/37960277147) — 最终分支代码的 CPU 负控/语法/样例排序检查 success。**不代表私有 provider 上完成真实 55 组回放。** 早期开发阶段两次失败已在提交 `851bf178152f58895004d570f810c25644b974f4` 前修正。

## 四、PR #50 已编写的内容（必须在新对话审查实际合并版本）

上一对话创建 [PR #50](https://github.com/AT2018cow/qlib/pull/50)，分支为 `audit/full-frozen-replay-rerank-20261010`，合并前最后见到的提交为 `851bf178152f58895004d570f810c25644b974f4`。共修改 **4 个文件**：

1. `audit/run_stage_b_corrected_rerank.py`：统一 CPU 研究重放器。`--scope controls` 对 winner 与 baseline 各五阶段（10 组）做快速对照；`--scope all` 对冻结 **11 候选 × 5 phase = 55 组**执行。**先**按完整结果 JSON 对原始 `report.parquet`、`signal.parquet`、`decisions.json` 检查字节 SHA，再初始化原始只读 provider 并核对快照身份、交易日历；每组先以旧动态报价完整复刻原账户报告和订单，再仅改用历史成员静态 quote union 做修复执行回放。输出原/修复指标、订单变化、NAV 差异、每组新报告和新订单；全部 55 组实际通过后才调用原有 `rank_stage_b_candidates()` 生成诊断性新排名。Winner phase0 的已知修复 Sharpe 和 1,696 单作为回归门禁。
2. `tests/test_stage_b_corrected_rerank.py`：CPU 合成原始快照（**165 个原始文件位置的测试替身**）验证 55 组路径、baseline phase0 的特殊 `repeat_b` 源目录、缺失文件、SHA 被篡改，以及不完整候选/阶段拒绝排序。测试成功**不代表真实私有数据已回放**。
3. `.github/workflows/csi1000-stage-b-corrected-rerank.yml`：CPU CI，仅做语法、十份已公开冻结报告身份预检和负控；**不读取授权 Modal provider**，不作 55 组真实 Qlib 执行。
4. `docs/experiments/34-stage-b-all-candidates-corrected-execution-rerank-20261010.md`：使用说明、两种 scope、失败停止/输出语义和严格研究权限边界。

**新对话审查重点（依优先级）：**

- 确认 PR #50 真正已合并、`main` HEAD 与最终 diff/提交一致，且合并后 CI 成功。过去分支成功**不能替代** `main` 合并后的检查。
- 找实际运行证据：**是否有在原始 read-only provider 上执行 `controls`/`all` 的记录和生成的 `batch_result.json`？** 没有就保持 `actual_corrected_replays=BLOCKED`；不能把合成单测解释成 55 组回测已完成。
- 审查代码的**旧报告账户/订单先复现再修复**门禁、原冻结指针及 SHA，provider 身份是否实质核对、calendar/信号日期是否一致、baseline phase0 特殊原始 `_preflight/.../repeat_b` 路径、独立输出目录只写且不覆盖、静态 quote union 仅扩展报价不扩展信号选股资格、费用/交易规则与原研究一致。
- 确保 55 组覆盖不遗漏、只有完整的 11 候选均通过才产生诊断排名；`--scope controls` 不得声称找到全局新 winner。
- 核查失败时是否留下**误导性部分输出**，和真实 provider 数据版本/库版本差异会否导致 legacy 重放错误；失败应显式报告首次差异，不应隐式调整容差。
- **不要自动选择/部署新模型**，也不要在已使用过的测试区间进行新参数搜索。若全量重跑得出新诊断性排序，还须第二套独立价格/订单/现金账和 PIT/市场执行证据再讨论策略推广。

## 五、下一个对话的最短实际执行路径

请按这个顺序，避免新的审计“框架 PR”：

**A. 首先审查 PR #50 合并**：GitHub 查看 `main`、PR diff、main Actions、必要的运行日志，以及审计脚本。只区分“合并后 CI 负控是否合格”和“是否做过真实 55 组 provider 回放”。如有明确 bug，优先在**一个小修复 PR**中集中修复，不再连续拆多个门禁 PR。

**B. 若真实数据未回放：** 最优先获取授权实验环境中**同一冻结 snapshot** 的原始文件与原始只读 provider。GitHub 仓库已公开的十组原 report 与一组 winner phase0 原信号/决策**不足以完成 55 组重跑**。需要 55 组每组各自原始 report/signal/decisions 的原字节，以及原 provider 快照。尽可能在同一授权 CPU 环境一次性执行，不要往公共 GitHub 上传大体积原始行情、可能受许可证约束的数据或凭据。

**C. 先控制组，再完整候选：**

```bash
# 用户需在有 ORIGINAL 只读快照与 provider 的授权实验环境执行；路径是占位符。
python -m audit.run_stage_b_corrected_rerank \
  --scope controls \
  --repo-root . \
  --snapshot-root /readonly/original-stage-b-snapshot-root \
  --provider-uri /readonly/original/cn_data \
  --provider-snapshot-file /readonly/original/provider_snapshot.json \
  --out-dir /tmp/stage-b-controls-f1-UNIQUE

python -m audit.run_stage_b_corrected_rerank \
  --scope all \
  --repo-root . \
  --snapshot-root /readonly/original-stage-b-snapshot-root \
  --provider-uri /readonly/original/cn_data \
  --provider-snapshot-file /readonly/original/provider_snapshot.json \
  --out-dir /tmp/stage-b-all55-f1-UNIQUE
```

**D. 审计结果最小交付物：** 真实 `batch_result.json` 的 SHA/来源、完成的真实组数（目标 55/55）、旧路径是否复现（55/55）、修复后每组 Sharpe/CAGR/MaxDD、订单变化与是否恢复换手、旧 winner 原/新排名、完整排序前列与 baseline 比较、严重异常根因归类。分清**Qlib 诊断重放**与**独立市场成交/账本验证**。仍拿不到真实输入时，明示 BLOCKED，给出最短获取方法，不另做大量与策略决策无关的设计。

**E. 及时提出决策**：

- 原有冻结候选修复后达标而历史排序改变：考虑**重新选择原候选**，但不自动生产部署，另做独立验证。
- 原 winner 修复后仍稳健且原 ranking 契约稳定：可考虑保留，仍须市场执行/PIT 门禁。
- 原候选普遍失效：仅在拟定不依赖已消费测试区间的**新验证方案**后，讨论是否重训/调参，明确必要性、成本和准入标准。
- 任何阶段**不得把已消费 reserved tail 上重新排序得到的“最佳值”当成新的无偏绩效证明**。

## 六、冻结身份和关键文件速查

- GitHub 仓库：<https://github.com/AT2018cow/qlib>
- 冻结 Stage-B 结果 JSON：`results/csi1000_stage_b/stage_b_full_51756897fc752304.json`
- 原始结果产生 commit：`7a2676397b0f8e6f69c0bffc98d1764647f644ac`
- 冻结 snapshot token：`51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`
- Winner candidate ID：`4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`
- Baseline candidate ID：`23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`
- 原始固定执行：`2025-01-02 .. 2026-09-30`，424 个交易日，初始资金 CNY 100,000,000；T-close 信号→T+1-open、Top20/Drop2、benchmark `SH000852`，买费率 5bp / 卖费率 15bp，最低 CNY 5。
- 原始十份已导出报告目录：`results/csi1000_stage_b/audit_reports/` 与 `manifest.json`；**仅 winner/baseline 各 5 份**。
- 公共仓库已存原 winner phase0 信号/决策：`audit/evidence/winner_phase0/raw/`。
- 特殊路径：baseline phase0 位于同一 snapshot 的 `_preflight/e56441cbfd5b4242/repeat_b/phases/<baseline-id>/phase00/`，**必须从完整冻结 JSON 读原路径，不推断**。
- F5 原有诊断与独立账：`audit/replay_stage_b_f1.py`、`audit/run_winner_phase0_execution_audit.py`、`audit/stage_b_f5_gates.py`。
- F6 输入身份：`audit/stage_b_f6_preflight.py`。
- PR #49 异常扫描：`audit/triage_stage_b_execution.py`。
- PR #50 批量纠正和新排名入口：`audit/run_stage_b_corrected_rerank.py`。
- 冻结原始结果包含 **11 candidates × 5 phases**；PR #48/#49 所说“十组”仅为 **winner/baseline** 控制组，不是全体 Stage-B 候选。

## 七、特别提醒

1. **当前旧对话遵守了用户要求，没有审查 PR #50 的合并结果。** 文档中“已合并”是用户通知，须由新对话实际核查；不得误写为当前对话已核验 `main`。
2. **PR #50 代码合并 ≠ 修复后的 55 组回测已经完成。** 新对话要主动寻找实际 `batch_result.json` 和有原始 provider 的执行证据。
3. 旧 F5/PR #49 Actions success 的含义是现有证据与测试跑通，不代表解决真实 ST/停牌/盘口、训练标签穿越或未来回报的审计问题。
4. 用户首要目标是**快速发现真问题并作出保留/重选/调参决策**；未来工作尽量合并、少 PR、只做直接有助于决策的工作。

---

## 建议发送给新对话的第一条消息

> 请接手我的 GitHub 量化研究项目 `AT2018cow/qlib` 的 CSI1000 Stage-B 审计。我已在这条消息中附上完整工作交接文档。**PR #50 已合并，但上一个对话按我的要求没有审核它；请第一步核查 PR #50 在 `main` 的合并代码、GitHub Actions 和是否实际执行了 55 组冻结信号的修复版回放，不要把 CPU 合成单测误认为完成真实回测。** 已发现原历史执行存在 F1 报价缺失缺陷，winner phase0 修复后 Sharpe 从 1.276 降到 0.999；PR #49 又确认 winner/baseline 共十组全部有至少 114 日连续零换手。原 Stage-B 实际是 11 个冻结候选 × 5 phase＝55 组。我的核心目标是**尽快判断原 winner 是否仍有效、是否需要重新选择现有策略、是否真的需要重新调参**。请少开 PR、避免过度设计，优先验证真实原始 provider 上的修复回放、形成策略决策；未取得原始数据要明确 `BLOCKED`。不要重训、不要擅自部署或改生产，不要在已消耗 reserved tail 上重新调参。