# 阶段 3（L4）执行计划：沪深 300（`csi300`）与中证 500（`csi500`）五相位完整决选与成本压力测试

> **目标读者**：执行模型（DeepSeek 4.1 Flash / Muse Spark）  
> **计划制定与验收方**：主架构模型（Reviewer）  
> **当前阶段**：Stage 3（L4 — 对 `csi300` 和 `csi500` 各自决选入围策略补齐剩余 3 个重训相位 `Phase 04 / 06 / 15` 的滚动训练与 F1 账本回放，合并前序已完成的 `Phase 00 / 10`，在完整 **5 个相位 `[0, 4, 6, 10, 15]`** 上进行最终稳健性排名与 +10bp/+20bp 成本压力决选，正式选出 `csi300` 与 `csi500` 各自的最优生产策略）

---

## ⚠️ 三门控分步执行铁律（Three-Gate Workflow）

**本阶段同样严格执行三门控流程，每完成一个 Gate 必须立即停下等待主架构模型（Reviewer）审查批准：**

- **【Gate 1：仅写代码与本地单元测试（当前仅授权执行此步！）】**
  - 在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`（分支 `feat/csi300-csi500-stage3-l4`）中编写 `stage3_l4_core.py`、`stage3_l4_modal.py` 与 `test_csi300_csi500_stage3_l4.py`；
  - 运行本地 `pytest` 与 `modal run ... --help` 通过；
  - 在 Worktree 分支 `git commit`，将汇报文档状态更新为 `GATE1_CODE_READY_FOR_REVIEW`，**立即停止，严禁在 Gate 1 发起任何付费 `modal run`！**
- **【Gate 2：仅执行 Smoke Test（待 Reviewer 审查代码通过并明确下令后执行）】**
  - 仅执行 `--mode smoke --max-workers 2`（跑 2 个剩余相位训练切片：1 个 `csi300 / m03_4e908173 / phase4` 首切片 + 1 个 `csi500 / m10_raw10d_c98856b4 / phase4` 首切片）；
  - 更新汇报文档 Gate 2 部分，状态更新为 `GATE2_SMOKE_READY_FOR_REVIEW`，**立即停止，严禁自行跑 Complete！**
- **【Gate 3：执行 Complete 全量五相位决选（待 Reviewer 审查 Smoke 报告通过并明确下令后执行）】**
  - 使用 `--mode complete --max-workers 66` 跑完剩余 658 个训练切片（共 660 个新 fit）+ 30 份新相位信号拼接 + 54 个新相位 F1 回放单元，合并 Phase 0/10 已有的 36 个回放单元，形成 **90 个五相位决选单元（每市场 9 套决选策略 × 5 相位 = 45 格）**，输出双市场五相位总榜！

---

## 一、工作区隔离与本地沟通文件

1. **专属代码工作目录（Worktree）**：
   - 路径：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
   - 分支名：`feat/csi300-csi500-stage3-l4`（已从包含 Stage 0+1+2 成果的最新 `main` 创建）
2. **本地沟通与产物路径**：
   - **本执行计划（只读）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/07-stage3-l4-execution-plan-for-muse-spark.md`
   - **你的三阶段执行报告（实时填写）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/08-stage3-l4-muse-spark-execution-report.md`
   - **本地冷备目录（Gate 2/3 使用）**：
     `/home/ss/git_repos/qlib/quant/local_backup/10_csi300_csi500_stage3_l4/`
   - **入库轻量汇总目录（Gate 3 完成后提交到 Worktree）**：
     `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/data/csi300_csi500_research/stage3_l4/`

---

## 二、阶段 3（L4）五相位决选名册（Frozen L4 Finalist Roster）

### 2.1 五个标准重训相位
- `ALL_5_PHASES = (0, 4, 6, 10, 15)`
- 其中 `PRIOR_PHASES = (0, 10)` 已在 Stage 0 / Stage 1 / Stage 2 完成训练与回放，**直接从 Volume 校验复用，零重复训练、零重复回放**！
- 本阶段仅需对 `NEW_PHASES = (4, 6, 15)` 执行滚动模型训练与 F1 组合回放（每个相位同样由 `build_phase_jobs(calendar, phase)` 生成 **22 个**重训切片 `retrain_asof`）。

### 2.2 需补齐 `Phase (4, 6, 15)` 训练的底层模型名册（每市场 5 套底模，共 10 个 `(market, model_key)` 对）
```python
STAGE3_MODELS_BY_MARKET = {
    "csi300": (
        "m03_4e908173",       # L1 Finalist #1 底模 (h=20)
        "m04_17dbfe28",       # L1 Finalist #2 底模 (h=20)
        "m06_b034ac46",       # L1 Finalist #3 底模 (h=20)
        "m01_59b93929",       # L1 Finalist #4 底模 (h=20)
        "baseline",           # 官方对照锚点底模 (h=20)
    ),
    "csi500": (
        "m10_raw10d_c98856b4",  # L1 Finalist #1/#2 底模 (h=10)
        "m09_raw10d_baseline",  # L1 Finalist #3/#4 底模 (h=10)
        "c98856b4",             # 20d 最佳挑战底模 & 线上对照 (h=20)
        "m03_4e908173",         # 20d 次佳挑战底模 (h=20)
        "baseline",             # 官方对照锚点底模 (h=20)
    ),
}
```
- **新增滚动模型训练数（`l4retrain` jobs）**：
  `10 个 (market, model_key) × 3 个新相位 (4, 6, 15) × 22 个重训切片 = 660` 次 LightGBM 训练。
- **新相位 424 日预测信号保存路径**：
  对于每个 `(market, model_key, phase in (4, 6, 15))`（共 `10 × 3 = 30` 条信号线），由该组第 1 个回放单元或独立拼接步骤将 22 个切片拼接为 424 日 `signal.parquet`，保存在 `/results/<prefix>/signals/<market>/<model_key>/phase<phase:02d>/signal.parquet`（注意单写者防并发冲突，或只在该 `(market, model_key, phase)` 的首个 `combo_key` 容器中写入 `signals/`！）。

### 2.3 每市场 9 套五相位决选策略名册（`STAGE3_STRATEGIES_BY_MARKET`）
同一底模下不同 `combo_key` 共享同一份预测信号，零额外训练成本：

```python
STAGE3_STRATEGIES_BY_MARKET = {
    "csi300": (
        # (model_key, combo_key, role)
        ("m03_4e908173", "top8_drop1", "challenger"),   # L1 Finalist #1 (双相位最差超额 +32.28%)
        ("m04_17dbfe28", "top5_drop1", "challenger"),   # L1 Finalist #2 (双相位最差超额 +30.88%)
        ("m06_b034ac46", "top8_drop1", "challenger"),   # L1 Finalist #3 (双相位最差超额 +29.17%)
        ("m01_59b93929", "top8_drop1", "challenger"),   # L1 Finalist #4 (双相位最差超额 +27.41%)
        ("m01_59b93929", "top10_drop1", "challenger"),  # L1 Rank #5 (低回撤挑战者 -11.20%)
        ("m06_b034ac46", "top10_drop1", "challenger"),  # L1 Rank #6 (均衡集中挑战者)
        ("m03_4e908173", "top10_drop1", "challenger"),  # L1 Rank #8 (高夏普挑战者 1.524)
        ("baseline", "top10_drop1", "control_top10"),   # 集中持仓基线对照
        ("baseline", "top20_drop2", "official_baseline"), # 传统官方基线对照
    ),
    "csi500": (
        # (model_key, combo_key, role)
        ("m10_raw10d_c98856b4", "top10_drop1", "challenger"),  # L1 Finalist #1 (双相位最差超额 +15.50%)
        ("m10_raw10d_c98856b4", "top15_drop2", "challenger"),  # L1 Finalist #2 (高夏普 1.457 / 高 IR 0.809)
        ("m10_raw10d_c98856b4", "top15_drop1", "challenger"),  # L1 Rank #3 (低换手 15 股挑战者)
        ("m10_raw10d_c98856b4", "top20_drop2", "challenger"),  # L1 Rank #9 (低回撤 -15.89% / 高夏普 1.459)
        ("m09_raw10d_baseline", "top30_drop2", "challenger"),  # L1 Finalist #3 (10d 基线分散组合)
        ("m09_raw10d_baseline", "top20_drop1", "challenger"),  # L1 Finalist #4 (10d 基线低换手组合)
        ("c98856b4", "top30_drop3", "challenger"),             # 20d 最佳挑战者 (Rank #11, 最深回撤 -16.80%)
        ("m03_4e908173", "top15_drop1", "challenger"),         # 20d 次佳挑战者 (Rank #13)
        ("baseline", "top20_drop2", "official_baseline"),      # 传统官方基线对照 (Stage 0 最佳基线)
    ),
}
```
- **五相位决选总单元数**：`2 市场 × 9 套策略 × 5 相位 = 90` 个五相位单元：
  - 其中 `Phase (0, 10)` 的 `2 × 9 × 2 = 36` 个单元直接从 Stage 0 / Stage 1 / Stage 2 已落盘的 `stage2_l1_summary.json` 及对应 `receipt.json` 校验加载；
  - 其中 `Phase (4, 6, 15)` 的 `2 × 9 × 3 = 54` 个单元为本阶段新增回放单元（`kind="l4cell"`，`STAGE3_ARTIFACTS["l4cell"] = ("fixed_report.parquet", "decisions.json")`）。

---

## 三、五相位稳健性汇总与最终排名规则（`rank_five_phase_strategies`）

对每个市场的 9 套决选策略，汇总其在全部 **5 个相位 `[0, 4, 6, 10, 15]`** 上的表现（使用 `statistics.median` 计算五相位中位数）：
1. `worst_relative_excess_cagr = min(rel_cagr across 5 phases)`（五相位最差超额年化）
2. `median_relative_excess_cagr = median(rel_cagr across 5 phases)`（五相位中位超额年化）
3. `mean_relative_excess_cagr = mean(rel_cagr across 5 phases)`（五相位平均超额年化）
4. `median_strategy_cagr = median(cagr across 5 phases)`（五相位中位年化净收益）
5. `mean_strategy_cagr = mean(cagr across 5 phases)`（五相位平均年化净收益）
6. `median_total_return = median(total_return across 5 phases)`（五相位中位累计净收益）
7. `median_sharpe = median(sharpe across 5 phases)`（五相位中位夏普比）
8. `mean_sharpe = mean(sharpe across 5 phases)`（五相位平均夏普比）
9. `median_ir = median(ir across 5 phases)`（五相位中位信息比率）
10. `mean_ir = mean(ir across 5 phases)`（五相位平均信息比率）
11. `worst_max_drawdown = min(mdd across 5 phases)`（五相位最深回撤）
12. `median_max_drawdown = median(mdd across 5 phases)`（五相位中位回撤）
13. `median_turnover = median(mean_turnover across 5 phases)`（五相位中位日均换手）
14. `positive_excess_phases = count(rel_cagr > 0 across 5 phases)`（跑赢基准的相位数，满分 5）
15. `beats_official_baseline_phases = count(total_return > same_phase_official_baseline_total_return across 5 phases)`（战胜同相位 `baseline + top20_drop2` 的相位数，满分 5）
16. `worst_stressed10bp_excess_vs_baseline = min(stressed10bp_total_return - same_phase_baseline_total_return across 5 phases)`

**排序准则（降序）**：
按 `(worst_relative_excess_cagr, median_relative_excess_cagr, mean_relative_excess_cagr, median_ir, median_sharpe, worst_max_drawdown)` 降序排列，排出每个市场的第 1 名至第 9 名！

---

## 四、关键工程细节与极速下载要求

1. **避免并发写 `signals/` 冲突**：
   - 在 `l4_cell_replay_remote(job)` 中，当拼接 22 个 `l4retrain` 切片生成 424 日 `signal` 时，**仅允许每个 `(market, model_key)` 在 `STAGE3_STRATEGIES_BY_MARKET[market]` 中出现的第一个 `combo_key`** 将 `signal.parquet` 写入 `/results/<prefix>/signals/<market>/<model_key>/phase<phase:02d>/signal.parquet`（或者每个 `l4cell` 直接从 22 个 `fits/` 读取拼接而不写共享文件，并在首个 `combo_key` 写入 `signals/`）。
2. **`--pull-root` 极速下载优化（必做！）**：
   - 在 `_pull_run_prefix` 中，**默认跳过 `fits/` 中间子切片目录**（即只下载 `stage3_l4_summary.json`、`smoke_pass.json`、`cells/` 下的 54×3=162 个文件、以及 `signals/` 下的 30 个完整 424 日信号文件，合计仅 **194 个文件，约 20MB**）！
   - 这样 16 线程并发拉取只需 **5~10 秒**即可完成，彻底避免拉取上千个中间 `fits/` 小文件耗时数分钟！
3. **支持 `--max-fit-waves` 分段安全续跑**：
   - 660 个 `l4retrain` 任务在 `--max-workers 66` 下共 **10 波**（每波 66 个容器）。支持 `--max-fit-waves` 参数（例如分 `5 波 + 5 波` 或一次性 `10 波` 运行）。

---

## 五、需要创建的文件清单（Gate 1 范围）

请在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`（分支 `feat/csi300-csi500-stage3-l4`）下创建：
1. `quant/code/research/csi300_csi500/stage3_l4_core.py`
2. `quant/code/research/csi300_csi500/stage3_l4_modal.py`
3. `quant/tests/test_csi300_csi500_stage3_l4.py`

**当前仅授权执行 Gate 1（编写代码 + 本地 pytest + commit 到 `feat/csi300-csi500-stage3-l4` + 填写 `08-stage3-l4-muse-spark-execution-report.md` Gate 1 节）。完成后立即停下报告 Reviewer 审查！**
