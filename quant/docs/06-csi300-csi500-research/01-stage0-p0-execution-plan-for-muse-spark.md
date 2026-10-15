# 阶段 0（P0）执行计划：沪深 300（`csi300`）与中证 500（`csi500`）数据底座验证与双锚点基线回放

> **目标读者**：执行模型（Muse Spark）  
> **计划制定与验收方**：主架构模型（Reviewer）  
> **当前阶段**：Stage 0（P0 — 静态报价并集核验 + `Baseline` 与 `c98856b4` 双模型 × `Top20/Drop2` 与 `Top10/Drop1` 双组合 × `Phase 00 / Phase 10` 双相位完整 424 日滚动训练与 F1 账本回放）  
> **重要提示**：请严格按本文档的每一步顺序执行，**不要跳步、不要修改参数、不要在主工作区直接改代码、绝对禁止对 `modal run` 使用 `--detach` 或后台 `&`**。

---

## 一、工作区隔离（Git Worktree）与本地沟通机制

为了避免与主目录（`/home/ss/git_repos/qlib`，位于 `main` 分支）产生任何文件冲突，你的所有代码编写、单元测试与实验启动都必须在专属 Git Worktree 中完成：

1. **你的专属代码工作目录（Worktree）**：
   - 路径：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
   - 分支名：`feat/csi300-csi500-stage0-p0`（已为你创建好，且已预置编译好的 Cython `.so` 扩展）
   - Python 解释器：统一使用 `/home/ss/git_repos/qlib/.venv/bin/python`
   - Modal CLI：统一使用 `/home/ss/git_repos/qlib/.venv/bin/modal`
   - **严禁事项**：严禁直接修改 `/home/ss/git_repos/qlib` 根目录下的代码文件；所有新增/修改的代码必须在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0` 内操作。

2. **本地沟通文档路径（直接读写以下本地文件汇报进度与结果，无需发 GitHub PR）**：
   - **本执行计划（只读）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/01-stage0-p0-execution-plan-for-muse-spark.md`
   - **你的执行状态与结果汇报文档（你必须实时更新此文件）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/02-stage0-p0-muse-spark-execution-report.md`
   - **实验原始大文件本地冷备目录（存放拉回的 Parquet / JSON / 日志 / SHA256SUMS）**：
     `/home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/`
   - **入库轻量汇总结果目录（写在你的 Worktree 内并 commit 到 `feat/csi300-csi500-stage0-p0` 分支）**：
     `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/data/csi300_csi500_research/stage0_p0/`

---

## 二、阶段 0（P0）科学口径与实验矩阵（严禁篡改）

### 2.1 数据源与时间窗口（与中证 1000 研究严格对齐）
- **Modal Workspace / Profile**：**必须使用 `--profile infi`**（即 `infi` workspace）。
- **只读源数据卷（Source Volume）**：`qlib-cn-data`（挂载到容器 `/source`，`read_only=True`）。
- **独立研究输出卷（Research Volume v2）**：`qlib-csi300-csi500-research-v2`（Volume v2，挂载到容器 `/results`）。
- **源快照校验（`/source/csi1000_stage_b/provider_snapshot.json`）**：
  - `provider_fingerprint`: `"cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"`
  - `snapshot_token`: `"51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"`
  - `provider_cutoff`: `"2026-09-30"`（交易日历共 `6482` 天，`2000-01-04` 至 `2026-09-30`）
- **回测与训练窗口**：
  - `TRAIN_START = "2016-01-01"`
  - `VALIDATION_SESSIONS = 252`
  - `LABEL_HORIZON = 20`（标签表达式：`Ref($close, -20)/$close - 1`）
  - `RETRAIN_FREQUENCY = 20`
  - `RESERVED_SIGNAL_ANCHOR = "2024-12-31"`（用于生成 `2025-01-02` 首个开盘调仓信号）
  - `RESERVED_EXECUTION_START = "2025-01-02"`
  - `RESERVED_EXECUTION_END = "2026-09-30"`（共 **424 个执行交易日**）
  - **测试相位**：`PHASES = (0, 10)`（每个相位通过 `build_phase_jobs(calendar, phase)` 生成 **22 个**滚动重训切片 `retrain_asof`）。

### 2.2 两个目标股票池及静态历史报价并集（F1 修复口径）
必须使用 `execution_quote_codes(market, "2025-01-02", "2026-09-30", provider=D)` 获取静态报价并集，并核对以下精确哈希值（不匹配立即 `STOP`）：

| 股票池 (`market`) | 对应基准指数 (`benchmark`) | 2025-01-02..2026-09-30 静态报价并集股票数 | 预期 `quote_sha256` (`"\n".join(codes) + "\n"` 的 SHA256) |
|---|---|---:|---|
| `csi300` | `SH000300` | **338** | `9f3756ea8d2e0ff0bb12c3dce6d81888fe7a5df978a5f5669a4ce07b1a043f5b` |
| `csi500` | `SH000905` | **643** | `cfcbc21efaba156173ff3c0aa09afc4f1fda5e94d9046f32bf37aa8b32f5e4fb` |

### 2.3 两套锚点模型（Anchor Models）
在每个股票池（`csi300`、`csi500`）和每个相位（`0`、`10`）下，训练以下两套模型：

1. **原始 Baseline 模型 (`baseline`)**：
   - `candidate_id`: `"23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785"`
   - `model_sha256`: `"793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3"`
   - `model_params`:
     ```python
     {
         "learning_rate": 0.1,
         "colsample_bytree": 0.9,
         "lambda_l1": 205.6999,
         "lambda_l2": 580.9768,
         "max_depth": 8,
         "num_leaves": 250,
         "min_data_in_leaf": 20,
     }
     ```
2. **现役临时生产模型 (`c98856b4`)**：
   - `candidate_id`: `"c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b"`
   - `model_sha256`: `"d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50"`
   - `model_params`:
     ```python
     {
         "learning_rate": 0.05,
         "colsample_bytree": 0.7,
         "lambda_l1": 50.0,
         "lambda_l2": 200.0,
         "max_depth": 6,
         "num_leaves": 31,
         "min_data_in_leaf": 50,
     }
     ```

### 2.4 两套锚点持仓组合（Portfolio Rules）
每条 `market × model × phase` 信号线（共 `2 × 2 × 2 = 8` 条 424 日 `signal.parquet`）生成后，**无需重复训练模型**，直接分别回放以下两种组合（共 `8 × 2 = 16` 个 F1 回放单元）：
1. **`Top20/Drop2`**：`topk = 20, n_drop = 2`（传统 Qlib 基线持仓规则）
2. **`Top10/Drop1`**：`topk = 10, n_drop = 1`（当前线上临时采用的集中持仓规则）

### 2.5 总计算量汇总
- **滚动模型训练任务（`retrain` jobs）**：`2 股票池 × 2 模型 × 2 相位 × 22 重训日 = 176` 次 LightGBM 训练。
- **阶段信号拼接与组合回放任务（`cell` jobs）**：`2 股票池 × 2 模型 × 2 组合 × 2 相位 = 16` 个 424 日 F1 独立账户回放单元。

---

## 三、容器规格、并行度与必防陷阱（极其重要！）

### 3.1 容器规格与并行度设置
1. **`fit_remote`（单次 LightGBM 滚动训练容器）**：
   - 容器规格：`cpu=8, memory=16384, timeout=3600, retries=0`（8 物理核 / 16 GiB 内存，绝不使用 GPU）。
   - LightGBM 内部参数：保持 `num_threads=20`（由 `apply_lgb_reproducibility(cfg)` 与基础 YAML 决定，确保 `model_sha256` 一致）。
   - 并行度（`--max-workers`）：
     - Smoke 阶段：`--max-workers 2`（跑 2 个真实训练切片：1 个 `csi300` + 1 个 `csi500`）。
     - Complete 阶段：`--max-workers 44`（176 个切片去掉 Smoke 已完成的 2 个，剩余 174 个切片分 4 波并行跑完，每波 44 个容器，约 8~12 分钟全部跑完）。
2. **`cell_replay_remote`（424 日信号拼接 + F1 账本回放容器）**：
   - 容器规格：`cpu=2, memory=4096, timeout=1800, retries=0`。
   - 并行度：16 个回放单元可一次性用 `--max-workers 16` 并发执行（约 2~3 分钟完成）。

### 3.2 五大必防工程陷阱（写代码时逐条核对！）
1. **【陷阱 1：Modal 镜像上传体积爆炸】**  
   仓库下有 `quant/local_backup/`（近 500MB）和 `quant/data/`。在 `modal.Image.add_local_dir(".", remote_path="/root/qlib", ...)` 中，**必须**在 `ignore` 函数里排除：
   `".git"`, `".worktrees"`, `".venv"`, `"mlruns"`, `"__pycache__"`, `"quant/local_backup"`, `"quant/data"`, 以及 `".so"`, `".cpp"` 后缀文件！否则每次构建镜像会上传几百 MB 数据。
2. **【陷阱 2：容器内 `PYTHONPATH` 路径变更】**  
   仓库已重构至 `quant/code/` 目录树下。Modal Image 的 `.env(...)` 中必须设置完整的 `PYTHONPATH`：
   ```python
   "PYTHONPATH": (
       "/root/qlib:"
       "/root/qlib/quant/code:"
       "/root/qlib/quant/code/execution:"
       "/root/qlib/quant/code/production:"
       "/root/qlib/quant/code/research/stage_ab:"
       "/root/qlib/quant/code/research/layered_search:"
       "/root/qlib/quant/code/research/csi300_csi500"
   )
   ```
3. **【陷阱 3：不要直接调用 `csi1000_layered_search_replay.backtest_signal`】**  
   `csi1000_layered_search_replay.py` 里的 `backtest_signal` 硬编码了 `benchmark=BENCHMARK`（即 `"SH000852"`）！你必须写一个支持传入 `benchmark` 参数的 `backtest_market_signal(signal, topk, dropn, codes, start, end, benchmark)`：
   - 当 `market == "csi300"` 时，必须传 `benchmark="SH000300"`；
   - 当 `market == "csi500"` 时，必须传 `benchmark="SH000905"`。
4. **【陷阱 4：不要在训练配置里硬编码 `instruments="csi1000"`】**  
   在构造 LightGBM 训练任务 `_task(model_params, market)` 时，必须设置：
   `ds["handler"]["kwargs"]["instruments"] = market`（即 `"csi300"` 或 `"csi500"`）。
5. **【陷阱 5：严禁使用 `--detach`、后台 `&` 或 `nohup`】**  
   在 OpenCode 环境中，`--detach` 会在命令结束时中断作业。必须使用前台 attached 模式运行：
   `set -o pipefail && /home/ss/git_repos/qlib/.venv/bin/modal run --profile infi ... 2>&1 | tee <logfile>`，并将 bash 工具的 `timeout` 设为足够大（例如 Smoke 设 `600000` ms 即 10 分钟，Complete 设 `1800000` ms 即 30 分钟）。

---

## 四、具体实施步骤（Step-by-Step）

### Step 1：检查你的专属 Worktree 与 Modal Volume v2
在 `workdir="/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0"` 下执行：
```bash
git status -s
git branch --show-current
# 确认分支为 feat/csi300-csi500-stage0-p0
```
然后检查 `infi` workspace 下是否已有 `qlib-csi300-csi500-research-v2`，若无则创建（Volume v2）：
```bash
/home/ss/git_repos/qlib/.venv/bin/modal volume list --profile infi
# 若列表中没有 qlib-csi300-csi500-research-v2，执行：
/home/ss/git_repos/qlib/.venv/bin/modal volume create --profile infi --version 2 qlib-csi300-csi500-research-v2
```

### Step 2：编写核心配置与离线计划模块
在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/code/research/csi300_csi500/` 下创建：
1. `__init__.py`
2. `stage0_p0_core.py`：
   - 定义常量：
     - `MARKETS = {"csi300": {"benchmark": "SH000300", "expected_quote_count": 338, "expected_quote_sha256": "9f3756ea8d2e0ff0bb12c3dce6d81888fe7a5df978a5f5669a4ce07b1a043f5b"}, "csi500": {"benchmark": "SH000905", "expected_quote_count": 643, "expected_quote_sha256": "cfcbc21efaba156173ff3c0aa09afc4f1fda5e94d9046f32bf37aa8b32f5e4fb"}}`
     - `MODELS = {"baseline": {"candidate_id": "23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785", "model_sha256": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3", "model_params": {"learning_rate": 0.1, "colsample_bytree": 0.9, "lambda_l1": 205.6999, "lambda_l2": 580.9768, "max_depth": 8, "num_leaves": 250, "min_data_in_leaf": 20}}, "c98856b4": {"candidate_id": "c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b", "model_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50", "model_params": {"learning_rate": 0.05, "colsample_bytree": 0.7, "lambda_l1": 50.0, "lambda_l2": 200.0, "max_depth": 6, "num_leaves": 31, "min_data_in_leaf": 50}}}`
     - `COMBOS = (("top20_drop2", 20, 2), ("top10_drop1", 10, 1))`
     - `PHASES = (0, 10)`
     - `EXPECTED_PROVIDER_FINGERPRINT = "cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5"`
     - `EXPECTED_SNAPSHOT_TOKEN = "51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1"`
   - 实现 `build_stage0_fit_jobs(calendar, run_id, code_sha)`：生成全部 `2 × 2 × 2 × 22 = 176` 个 `retrain` 任务字典（每个包含 `market`, `benchmark`, `model_key`, `candidate_id`, `model_params`, `model_sha256`, `phase`, `retrain_asof`, `signal_end`）。
   - 实现 `build_stage0_cell_jobs(run_id, code_sha)`：生成全部 `2 × 2 × 2 × 2 = 16` 个 `cell` 回放任务字典（每个包含 `market`, `benchmark`, `model_key`, `candidate_id`, `combo_key`, `topk`, `n_drop`, `phase`）。
   - 实现不可变 `receipt.json` 的写入与校验函数（参考 `quant/code/research/layered_search/csi1000_l2_execution.py` 的 `checked_receipt` 和 `publish_receipt`，写入临时目录 `.tmp` 后 `os.replace` 原子重命名，绑定 `artifact_sha256`）。
   - 组合回放指标计算直接调用 `quant/code/research/layered_search/csi1000_layered_search_replay.py` 中的 `report_metrics(report)`（它会同时返回标准净收益、CAGR、相对基准超额 `relative_excess_cagr`、Sharpe、IR、MaxDD、换手率以及 `+10bp` / `+20bp` 压力测试结果！）。

### Step 3：编写 Modal 分布式执行器 `stage0_p0_modal.py`
在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/code/research/csi300_csi500/stage0_p0_modal.py` 中实现：
- 挂载：
  - `/source`: `modal.Volume.from_name("qlib-cn-data", create_if_missing=False).with_mount_options(read_only=True)`
  - `/results`: `modal.Volume.from_name("qlib-csi300-csi500-research-v2", create_if_missing=False, version=2)`
- 远程函数：
  1. `inventory_remote(meta)` (`cpu=2, memory=4096, timeout=900, retries=0`)：
     - 校验 `/source/csi1000_stage_b/provider_snapshot.json` 的 `provider_fingerprint` 与 `snapshot_token`；
     - 初始化 `qlib.init(provider_uri="/source/cn_data", region="cn")`；
     - 读取交易日历（验证长度为 6482，末尾为 `2026-09-30`）；
     - 对 `csi300` 和 `csi500` 调用 `static_quote_union(market, "2025-01-02", "2026-09-30", D)`，严格校验返回的股票数量（338 与 643）及 `quote_sha256` 与 `stage0_p0_core.py` 常量一致；
     - 验证 `SH000300` 和 `SH000905` 在 `2025-01-02..2026-09-30` 的 424 个交易日均有非空基准收益数据。
  2. `fit_remote(job)` (`cpu=8, memory=16384, timeout=3600, retries=0`)：
     - 若 `checked_receipt` 已存在且 SHA 校验通过，直接返回 `{**cached, "resumed": True}`；
     - 否则运行单次真实 LightGBM 训练（参考 `csi1000_l2_modal.py:fit_remote` 的 `"long"` 分支，`purge_cfg_splits(config, calendar, horizon=20)`），落盘 `prediction.parquet`、`checkpoint.txt` 和 `receipt.json`，并 `research.commit()`。
  3. `cell_replay_remote(job)` (`cpu=2, memory=4096, timeout=1800, retries=0`)：
     - 若 `checked_receipt` 已存在且 SHA 校验通过，直接返回 `{**cached, "resumed": True}`；
     - 否则读取该 `(market, candidate_id, phase)` 下全部 22 个已校验的 `prediction.parquet`，拼接并截取 `2024-12-31 <= date < 2026-09-30` 的 424 个信号交易日；
     - 若该 `(market, candidate_id, phase)` 的完整 424 日 `signal.parquet` 尚未在模型相位目录保存，保存一份；
     - 调用 `backtest_market_signal(signal, job["topk"], job["n_drop"], codes, "2025-01-02", "2026-09-30", job["benchmark"])` 运行真实 Qlib F1 回测；
     - 调用 `report_metrics(report)` 计算完整指标与 +10bp/+20bp 压力测试；校验 `len(report) == 424`、`len(decisions) == 424`、`max_account_return_delta <= 1e-10`；
     - 原子落盘 `fixed_report.parquet`、`decisions.json`、`signal.parquet` 与 `receipt.json`，并 `research.commit()`。
  4. `export_summary_remote(meta)` (`cpu=2, memory=4096, timeout=600, retries=0`)：
     - 汇总全部 16 个 `cell` 的 `receipt.json` 指标，生成结构化汇总 JSON `stage0_p0_summary.json` 写入 `/results/<prefix>/stage0_p0_summary.json` 并返回给本地协调器。
- 本地入口 `@app.local_entrypoint() def main(mode: str = "smoke", run_id: str = "stage0_p0_20261015_a1", max_workers: int = 2)`：
  - **`mode == "smoke"`**：
    1. 调用 `inventory_remote.remote(meta)`；
    2. 选取 2 个训练切片（`csi300` 的 `baseline/phase0` 第 1 个切片 + `csi500` 的 `baseline/phase0` 第 1 个切片），使用 `fit_remote.map`（`max_workers=2`）执行真实训练并校验产物 SHA；
    3. 打印 `STAGE0_SMOKE_PASS 2/2 fits verified` 并正常退出（`EXIT=0`）。
  - **`mode == "complete"`**：
    1. 调用 `inventory_remote.remote(meta)`；
    2. 扫描全部 176 个训练切片，已通过 SHA 校验的（含 Smoke 的 2 个）自动复用，剩余未完成切片按每波 `max_workers`（默认传 `44`）分波调用 `list(fit_remote.map(wave_jobs))`，每波完成后打印进度；
    3. 全部 176/176 个训练切片校验通过后，对 16 个组合回放单元调用 `list(cell_replay_remote.map(cell_jobs))`（16 并发）；
    4. 全部 16/16 个回放单元完成后，调用 `export_summary_remote.remote(meta)`，将汇总结果同时写入本地并打印 `STAGE0_P0_COMPLETE 176/176 fits 16/16 cells PASS`。

### Step 4：编写并运行本地单元测试（Preflight Gate）
在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/tests/test_csi300_csi500_stage0_p0.py` 编写离线测试，验证：
1. 176 个 `fit` 任务与 16 个 `cell` 任务的唯一性、股票池与 `benchmark` 映射（`csi300 -> SH000300`, `csi500 -> SH000905`）；
2. `receipt.json` 的写入、SHA256 校验与防篡改拒绝逻辑；
3. 代码格式与静态检查通过：
   ```bash
   PYTHONPATH=quant/code:quant/code/execution:quant/code/production:quant/code/research/stage_ab:quant/code/research/layered_search:quant/code/research/csi300_csi500 \
     /home/ss/git_repos/qlib/.venv/bin/python -m pytest -q quant/tests/test_csi300_csi500_stage0_p0.py
   ```
4. 在 Worktree 中将代码 commit 到 `feat/csi300-csi500-stage0-p0` 分支，确保 `git status --porcelain` 为空，并记录 40 位 `git rev-parse HEAD` 作为 `code_sha`。

### Step 5：执行 2-Fit Smoke 实验（验证端到端通路）
在 Worktree 目录下前台运行 Smoke（设置工具 `timeout=600000`）：
```bash
set -o pipefail
/home/ss/git_repos/qlib/.venv/bin/modal run --profile infi \
  quant/code/research/csi300_csi500/stage0_p0_modal.py \
  --mode smoke --run-id stage0_p0_20261015_a1 --max-workers 2 \
  2>&1 | tee /home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/stage0_smoke.log
```
- 确认输出包含 `STAGE0_SMOKE_PASS` 且 `EXIT=0`。记录 Modal App ID（`ap-...`）。

### Step 6：执行 Complete 全量实验（176 Fits + 16 Replay Cells）
Smoke 通过后，使用**完全相同的 `run_id` 和 `code_sha`** 前台启动全量运行（设置工具 `timeout=1800000` 即 30 分钟，`--max-workers 44`）：
```bash
set -o pipefail
/home/ss/git_repos/qlib/.venv/bin/modal run --profile infi \
  quant/code/research/csi300_csi500/stage0_p0_modal.py \
  --mode complete --run-id stage0_p0_20261015_a1 --max-workers 44 \
  2>&1 | tee /home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/stage0_complete.log
```
- 若因任何原因中途暂停，只需用完全相同的命令重跑即可自动跳过已落盘校验的切片。

### Step 7：拉取全量产物到本地冷备库并生成入库汇总表
1. 使用 `/home/ss/git_repos/qlib/.venv/bin/modal volume get --profile infi qlib-csi300-csi500-research-v2 ...` 将本次运行的：
   - `stage0_p0_summary.json`
   - 全部 16 个 `cells/` 目录下的 `fixed_report.parquet`、`decisions.json`、`signal.parquet`、`receipt.json`
   下载到 `/home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/`，并生成 `SHA256SUMS.txt`。
2. 将轻量的 `stage0_p0_summary.json` 和 `stage0_p0_scoreboard.csv` 复制到 Worktree 的 `quant/data/csi300_csi500_research/stage0_p0/` 目录并 `git commit`。
3. 填写完成 `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/02-stage0-p0-muse-spark-execution-report.md`，通知主架构模型（Reviewer）验收。

---

## 五、主架构模型（Reviewer）验收标准（Checklist）

当你提交报告后，主架构模型将逐项执行以下独立验收（任一项不满足将被打回重做）：
1. **Worktree 与分支整洁度**：主目录 `/home/ss/git_repos/qlib` 未被污染；你的代码与测试位于 `.worktrees/muse_spark_stage0` 的 `feat/csi300-csi500-stage0-p0` 分支且 `pytest` 全绿。
2. **数据源与报价并集精确匹配**：
   - `csi300` 静态报价并集 = `338` 只，`quote_sha256 == 9f3756ea8d2e0ff0bb12c3dce6d81888fe7a5df978a5f5669a4ce07b1a043f5b`，基准为 `SH000300`；
   - `csi500` 静态报价并集 = `643` 只，`quote_sha256 == cfcbc21efaba156173ff3c0aa09afc4f1fda5e94d9046f32bf37aa8b32f5e4fb`，基准为 `SH000905`。
3. **176/176 模型训练与 16/16 回放账本完整性**：
   - 16 个 `fixed_report.parquet` 均包含完整的 424 个交易日（`2025-01-02` 至 `2026-09-30`）；
   - 独立用 `portfolio_performance.py` 复算 16 个 `fixed_report.parquet` 的净收益、CAGR、超额 CAGR、Sharpe、IR、MaxDD，与 `stage0_p0_summary.json` 误差 $\le 10^{-10}$，且账户资金平衡误差 `account_return_max_error <= 1e-10`。
4. **本地冷备与 SHA256SUMS 完整性**：
   - `/home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/SHA256SUMS.txt` 校验 100% `OK`。
