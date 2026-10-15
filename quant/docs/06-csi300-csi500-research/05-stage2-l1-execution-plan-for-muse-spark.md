# 阶段 2（L1）执行计划：沪深 300（`csi300`）与中证 500（`csi500`）零训练成本 `TopK/DropN` 组合矩阵全量回放

> **目标读者**：执行模型（Muse Spark）  
> **计划制定与验收方**：主架构模型（Reviewer）  
> **当前阶段**：Stage 2（L1 — **0 新增模型训练**，复用 Stage 0 + Stage 1 已固化的 48 份 424 日预测信号，遍历 8 种新增 `TopK/DropN` 组合 = 384 个新 F1 回放单元，合并前序 96 个单元形成 **480 格（每指数 12 模型 × 10 组合 = 120 套策略）** 双相位完整排行榜，并为每个指数独立选出进入阶段 3（五相位终选）的 Top 4 候选策略）

---

## ⚠️ 三门控分步执行铁律（Three-Gate Workflow）

**本阶段同样严格执行三门控流程，每完成一个 Gate 必须立即停下等待主架构模型（Reviewer）审查批准：**

- **【Gate 1：仅写代码与本地单元测试（当前仅授权执行此步！）】**
  - 在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`（分支 `feat/csi300-csi500-stage2-l1`）中编写 `stage2_l1_core.py`、`stage2_l1_modal.py` 与 `test_csi300_csi500_stage2_l1.py`；
  - 运行本地 `pytest` 与 `modal run ... --help` 通过；
  - 在 Worktree 分支 `git commit`，将汇报文档状态更新为 `GATE1_CODE_READY_FOR_REVIEW`，**立即停止，严禁在 Gate 1 发起任何付费 `modal run`！**
- **【Gate 2：仅执行 Smoke Test（待 Reviewer 审查代码通过并明确下令后执行）】**
  - 仅执行 `--mode smoke --max-workers 2`（跑 2 个回放单元：1 个 `csi300` + 1 个 `csi500`，**0 次模型训练**）；
  - 更新汇报文档 Gate 2 部分，状态更新为 `GATE2_SMOKE_READY_FOR_REVIEW`，**立即停止，严禁自行跑 Complete！**
- **【Gate 3：执行 Complete 全量回放（待 Reviewer 审查 Smoke 报告通过并明确下令后执行）】**
  - 使用 `--mode complete --max-workers 64` 跑完剩余 382 个回放单元（预计云端仅需 2~3 分钟），合并 Stage 0（16 格）+ Stage 1（80 格）共 **480 格**，生成每指数 120 套策略的双相位总榜；
  - **使用多线程并发（`ThreadPoolExecutor(max_workers=16)`）快速拉回产物**，严禁再写单线程串行下载循环！

---

## 一、工作区隔离与本地沟通文件

1. **专属代码工作目录（Worktree）**：
   - 路径：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
   - 分支名：`feat/csi300-csi500-stage2-l1`（已从包含 Stage 0 + Stage 1 成果的最新 `main` 创建）
2. **本地沟通与产物路径**：
   - **本执行计划（只读）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/05-stage2-l1-execution-plan-for-muse-spark.md`
   - **你的三阶段执行报告（实时填写）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/06-stage2-l1-muse-spark-execution-report.md`
   - **本地冷备目录（Gate 2/3 使用）**：
     `/home/ss/git_repos/qlib/quant/local_backup/09_csi300_csi500_stage2_l1/`
   - **入库轻量汇总目录（Gate 3 完成后提交到 Worktree）**：
     `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/data/csi300_csi500_research/stage2_l1/`

---

## 二、阶段 2（L1）科学口径与组合矩阵定义

### 2.1 12 套已冻结模型（零新增训练！）
全部 12 套模型（`2 套 Stage 0 锚点 + 10 套 Stage 1 新模型`）在 `csi300` 和 `csi500` 的 `Phase 00` 与 `Phase 10` 下的 424 日预测信号 `signal.parquet` 均已存在于同一个 Volume v2 `qlib-csi300-csi500-research-v2` 上：
- **Stage 0 锚点模型（`baseline`, `c98856b4`）信号路径**：
  `/results/csi300_csi500_research/stage0_p0/stage0_p0_20261015_a1/93bab198d812/cells/cell/<market>/<model_key>/<candidate_id[:8]>/top20_drop2/phase<phase:02d>/signal.parquet`
  （且其精确 SHA256 记录在该目录下的 `receipt.json["artifact_sha256"]["signal.parquet"]` 中，读取时必须校验 SHA256！）
- **Stage 1 新增模型（`m01_59b93929` .. `m10_raw10d_c98856b4`）信号路径**：
  `/results/csi300_csi500_research/stage1_m1/stage1_m1_20261015_a1/f9365d3c6765/cells/cell/<market>/<model_key>/<candidate_id[:8]>/top20_drop2/phase<phase:02d>/signal.parquet`
  （同样必须读取并校验对应 `receipt.json["artifact_sha256"]["signal.parquet"]`！）

### 2.2 10 种持仓与换仓组合（2 种已跑直接复用 + 8 种本阶段新增回放）
- **已由 Stage 0 + Stage 1 完成并直接复用的 2 种组合（96 格）**：
  - `("top20_drop2", 20, 2)`
  - `("top10_drop1", 10, 1)`
- **本阶段（Stage 2）新增回放的 8 种组合（`STAGE2_NEW_COMBOS`，共 `2 市场 × 12 模型 × 8 组合 × 2 相位 = 384` 格）**：
  ```python
  STAGE2_NEW_COMBOS = (
      ("top5_drop1", 5, 1),
      ("top8_drop1", 8, 1),
      ("top10_drop2", 10, 2),
      ("top15_drop1", 15, 1),
      ("top15_drop2", 15, 2),
      ("top20_drop1", 20, 1),
      ("top30_drop2", 30, 2),
      ("top30_drop3", 30, 3),
  )
  ALL_10_COMBOS = (
      ("top5_drop1", 5, 1),
      ("top8_drop1", 8, 1),
      ("top10_drop1", 10, 1),
      ("top10_drop2", 10, 2),
      ("top15_drop1", 15, 1),
      ("top15_drop2", 15, 2),
      ("top20_drop1", 20, 1),
      ("top20_drop2", 20, 2),
      ("top30_drop2", 30, 2),
      ("top30_drop3", 30, 3),
  )
  ```

### 2.3 关键性能优化（避免不必要的大文件冗余与下载卡顿！）
1. **每个 `l1cell` 单元只落盘 `("fixed_report.parquet", "decisions.json")` + `receipt.json`**：
   - 不要在 384 个 `l1cell` 目录下重复拷贝写入 384 份相同的 `signal.parquet`！
   - 改为在 `receipt.json` 的 `details` 中记录 `"source_signal_sha256": <verified_signal_sha>` 与 `"source_signal_relpath": <relpath>`。这样每个 `l1cell` 只有几十 KB，384 个单元总大小仅 ~15MB！
2. **Gate 3 下载产物时必须使用 `ThreadPoolExecutor(max_workers=16)` 并发下载**：
   - 严禁再写单线程 `for` 循环逐个调用 `vol.read_file_into_fileobj`！使用 16 线程并发下载 384 个 cell 目录不到 15 秒即可完成。

---

## 三、具体模块要求（Gate 1 范围）

请在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`（分支 `feat/csi300-csi500-stage2-l1`）下创建：

1. `quant/code/research/csi300_csi500/stage2_l1_core.py`：
   - 定义 `ALL_12_MODELS`（合并 `stage0_p0_core.MODELS` 的 2 套模型与 `stage1_m1_core.STAGE1_NEW_MODELS` 的 10 套模型，为 `baseline` 和 `c98856b4` 补齐 `"label_horizon": 20, "source_stage": "stage0_p0"`，其余 10 套标注 `"source_stage": "stage1_m1"`）。
   - 定义 `STAGE2_NEW_COMBOS`（8 种新组合）与 `ALL_10_COMBOS`（全部 10 种组合）。
   - 实现 `build_stage2_cell_jobs(run_id, code_sha)`：生成 `2 × 12 × 8 × 2 = 384` 个 `l1cell` 任务字典（每个包含 `kind="l1cell"`, `market`, `benchmark`, `model_key`, `candidate_id`, `label_horizon`, `source_stage`, `combo_key`, `topk`, `n_drop`, `phase`）。
   - 实现 `STAGE2_ARTIFACTS = {"l1cell": ("fixed_report.parquet", "decisions.json")}` 的 `checked_receipt` 与 `publish_receipt`。
   - 实现 `load_prior_96_cells(volume_root, stage0_prefix, stage1_prefix)`：从 Volume 加载并校验 Stage 1 的 `stage1_m1_summary.json`（其中已包含 80 个 Stage 1 cells + 16 个 Stage 0 anchor cells = 96 个 cells），并逐一校验每个 cell 的 `receipt.json` 与 `fixed_report.parquet` 字节哈希。
   - 实现 `rank_market_120_strategies(cells_240)`：对单市场的 `12 模型 × 10 组合 × 2 相位 = 240` 个 cell 按 `(model_key, combo_key)` 配对（共 **120 套策略**），按 `(worst_relative_excess_cagr, mean_relative_excess_cagr, mean_ir, mean_sharpe, worst_max_drawdown)` 降序排列并返回 120 行排名。
   - 实现 `select_top4_diverse_finalists(ranked_120, max_per_model=2, top_n=4)`：为防止同一个底层模型的 4 种微调 `TopK` 占满全部 4 个决赛名额，按排名从高到低挑选 **Top 4 挑战策略**，规则为：**同一 `model_key` 最多入选 `max_per_model=2` 套组合**（确保至少有 2~4 个不同底层模型进入阶段 3 的五相位终选！）。
   - 实现 `render_stage2_scoreboard(summary, csv_path)`（输出 480 行完整记分牌 CSV）与 `render_stage2_rankings(summary, csv_path)`（输出 `2 × 120 = 240` 行双相位策略排名 CSV）。

2. `quant/code/research/csi300_csi500/stage2_l1_modal.py`：
   - **绝对零训练（Zero Fits）**：文件中不得包含任何 `model.fit(` 调用！
   - `inventory_remote(meta)`：除了校验数据源、日历、静态报价并集和基准外，**必须逐一校验全部 48 份源 `signal.parquet`（`2 市场 × 12 模型 × 2 相位`）及其 `receipt.json` 在 Volume 上存在且 SHA256 100% 匹配**！
   - `l1_cell_replay_remote(job)` (`cpu=2, memory=4096, timeout=1800, retries=1`)：
     - 读取并校验对应 `(market, model_key, phase)` 的源 `signal.parquet`；
     - 调用 `backtest_market_signal(signal, job["topk"], job["n_drop"], codes, "2025-01-02", "2026-09-30", job["benchmark"])`；
     - 计算 `report_metrics` 与 `+10bp/+20bp` 压力测试，原子落盘 `fixed_report.parquet`、`decisions.json` 与 `receipt.json`（含 `source_signal_sha256`）。
   - `export_summary_remote(meta)`：合并 384 个新 `l1cell` + 96 个前序 `cell` = **480 个 cell**，调用 `rank_market_120_strategies` 和 `select_top4_diverse_finalists` 生成 `csi300` 与 `csi500` 各自的 120 行总榜与 Top 4 晋级名单，原子写入 `stage2_l1_summary.json`。
   - `@app.local_entrypoint() def main(mode: str = "smoke", run_id: str = "stage2_l1_20261016_a1", max_workers: int = 64)`：
     - `mode == "smoke"`（要求 `max_workers == 2`）：跑 2 个 `l1cell`（1 个 `csi300 / m01_59b93929 / top8_drop1 / phase0` + 1 个 `csi500 / m10_raw10d_c98856b4 / top15_drop2 / phase0`），写入 `smoke_pass.json` 并打印 `STAGE2_SMOKE_PASS 2/2 replay cells verified (0 fits)`。
     - `mode == "complete"`：校验 `smoke_pass.json`，以 `max_workers=64` 分波跑完剩余 382 个 `l1cell`，调用 `export_summary_remote` 并打印 `STAGE2_L1_COMPLETE 384/384 new cells 480/480 total ranking PASS (0 fits)`。

3. `quant/tests/test_csi300_csi500_stage2_l1.py`：
   - 完整测试 384 个任务名册唯一性、12 模型 × 8 新组合映射、receipt 校验、120 行排名与 `select_top4_diverse_finalists(max_per_model=2)` 多样性约束、480 行 CSV 导出、`stage2_l1_modal.py` 零 `.fit(` 调用及五大防坑静态检查。

**当前仅授权执行 Gate 1（编写代码 + 本地 pytest + commit 到 `feat/csi300-csi500-stage2-l1` + 填写 `06-stage2-l1-muse-spark-execution-report.md` Gate 1 节）。完成后立即停下报告 Reviewer 审查！**
