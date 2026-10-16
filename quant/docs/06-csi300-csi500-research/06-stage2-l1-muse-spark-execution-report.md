# 阶段 2（L1）执行报告（由 Muse Spark 按三门控分步填写）

> **执行模型**：Muse Spark / DeepSeek 4.1 Flash  
> **当前门控状态**：`GATE3_COMPLETE_READY_FOR_REVIEW`（Gate 3 全量完成：384/384 新单元 + 480/480 合并排名 PASS、0 训练、EXIT=0、SHA256SUMS 全 OK；等待 Reviewer 验收）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/05-stage2-l1-execution-plan-for-muse-spark.md`

---

## Gate 1：代码实现与本地单元测试（写完代码后填此节并停下等待审查！）
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage2-l1`
- **Gate 1 Commit SHA（40 位 `code_sha`）**：`81d40e265d0fcdf980dcf3fd1b2673c6145af23f`（`git status --porcelain` 为空）
- **新增文件列表**：
  - `quant/code/research/csi300_csi500/stage2_l1_core.py`
  - `quant/code/research/csi300_csi500/stage2_l1_modal.py`
  - `quant/tests/test_csi300_csi500_stage2_l1.py`
- **本地 `pytest` 运行结果**：`16 passed`（`test_csi300_csi500_stage2_l1.py`，0.66s）；另加跑 Stage 0 + Stage 1 回归 27 passed，合计 `43 passed`（2.75s）。覆盖：12 模型合并与 `model_sha256` 逐一重算、10 组合矩阵 plan-verbatim、384 l1cell 名册唯一性与映射、`l1cell` receipt 往返与 `source_signal_sha256` 篡改拒绝、prior-96 加载器（字节漂移/收据漂移/摘要漂移三层拒绝）、120 行排名与 `select_top4_diverse_finalists(max_per_model=2)` 多样性约束、480 行 scoreboard 与 240 行 rankings CSV、Modal 零 `.fit(` 静态防坑检查。
- **`modal run ... --help` 离线加载检查**：`EXIT=0`（`modal run --profile infi quant/code/research/csi300_csi500/stage2_l1_modal.py --help`，仅本地 import，无任何云端/付费调用；完整列出 `--mode/--run-id/--max-workers/--stage0-run-id/--stage1-run-id/--pull-root`）
- **是否发起过任何付费 Modal 运行**：`否（严格遵守 Gate 1 门控）`
- **关键实现说明**：
  - `STAGE2_ARTIFACTS = {"l1cell": ("fixed_report.parquet", "decisions.json")}` 精确成立：384 个 l1cell 不落盘 `signal.parquet`，信号身份改以 `receipt.json` 的 `source_signal_sha256` + `source_signal_relpath` 钉死（`publish_receipt` 强制校验、`checked_receipt` 逐次比对）。
  - `stage2_l1_modal.py` 全文零 `.fit(` / 零 `model.fit` / 零 `--detach`（静态断言 + grep 双重确认），且不含任何训练数据集配置（无 `ds["handler"]`）。
  - `inventory_remote` 逐一校验 48 份源信号（2 市场 × 12 模型 × 2 相位）的 `receipt.json` 状态/血缘与该信号 `SHA256` 100% 匹配；`--pull-root` 走 `ThreadPoolExecutor(max_workers=16)` 并发拉取（含断点跳过），严禁串行循环。

---

## Gate 2：Smoke Test 报告（待 Reviewer 批准 Gate 1 后填写，跑完立即停下等待审查！）
- **执行时 40 位 `code_sha`**：`81d40e265d0fcdf980dcf3fd1b2673c6145af23f`（worktree `git status --porcelain` 为空，代码与 Gate 1 锁定版本完全一致）
- **Run ID**：`stage2_l1_20261016_a1`
- **Modal App ID (`ap-...`)**：`ap-Xo2Ktgn4wjvk8ayELYSnci`
- **退出码 (`EXIT`)**：`0`（前台附着运行 `set -o pipefail ... 2>&1 | tee`，无 `--detach`；日志零 `STOP_`、零 `Traceback`；`STAGE2_L1CELL_WAVE 1: new=2 workers=2` → `STAGE2_SMOKE_PASS 2/2 replay cells verified (0 fits)`）
- **48 份源 `signal.parquet` 远端 SHA 盘点结果**：`48/48 全部通过`。`inventory_remote._scan_source_signals()` 对 2 市场 × 12 模型 × 2 相位 = 48 份冻结源信号逐一比对：密封 `receipt.json` 状态 PASS/血缘（`run_id`+`code_sha` 精确匹配 Stage 0/Stage 1）→ 重算远端 `signal.parquet` SHA256 与 receipt `artifact_sha256["signal.parquet"]` 一致；任一不一致即 fail-closed 抛 `STOP_STAGE2_*`（本次零触发）。远端 quote/snapshot 身份与 Stage 1 冻结环境一致：`csi300 quote=9f3756ea8d2e0ff0...`、`csi500 quote=cfcbc21efaba1561...`、`snapshot=1d693911db026f08...`（与 `smoke_pass.json`、2 份 smoke receipt 三方一致）。
- **Smoke 单元 1（`csi300 / m01_59b93929 / top8_drop1 / phase0`）结果**：`PASS`。`job_sha256=d088dcc5ee3a2ead...`；`fit_performed=false / new_fits=0`；回放 424 天（2025-01-02 → 2026-09-30）；超额年化 `+27.41%`、IR `1.327`、Sharpe `1.337`、策略年化 `35.10%`、最大回撤 `-10.46%`、日均换手 `24.77%`；`source_signal_sha256=ea4487d04bc7101f...`（与本地 08 备份 Stage 1 源信号字节重算一致）。
- **Smoke 单元 2（`csi500 / m10_raw10d_c98856b4 / top15_drop2 / phase0`）结果**：`PASS`。`job_sha256=56d914b0f5aa110d...`；`fit_performed=false / new_fits=0`；回放 424 天；超额年化 `+12.51%`、IR `0.661`、Sharpe `1.321`、策略年化 `30.73%`、最大回撤 `-16.12%`、日均换手 `26.81%`；`source_signal_sha256=d1ddf535c4f6fe37...`（本地重算一致）。
- **Smoke 本地产物核验**：7 个远端文件（`smoke_pass.json` + 2 个 l1cell 目录的 `receipt.json`/`fixed_report.parquet`/`decisions.json`）已拉回 `quant/local_backup/09_csi300_csi500_stage2_l1/smoke_receipts/`；本地独立复算 `ALL_SMOKE_CHECKS_PASS`：artifact 字节 SHA 与 receipt 一致、`job_sha256` 用 Gate 1 planner + bind 重算一致、每格 parquet 恰 424 行、`smoke_pass.json` 的 `smoke_job_shas`/`source_signal_sha256s` 与 2 份 receipt 完全互相印证、`zero_fits=true`。
- **Smoke 本地日志路径**：`quant/local_backup/09_csi300_csi500_stage2_l1/stage2_smoke.log`

---

## Gate 3：Complete 全量运行与 480 格双指数总榜（待 Reviewer 批准 Gate 2 后填写）
- **Modal Complete App ID (`ap-...`)**：`ap-LbhYIxifW1X2dJGApG4hPF`（续跑并正式完成；`STAGE2_L1_COMPLETE 384/384 new cells 480/480 total ranking PASS (0 fits)`，`MODAL_EXIT=0`）。中断取证：首次前台运行 App `ap-DamMhmKHQ3Obt7Zy5ucQd0` 于 2026-10-16 01:45:42Z 被客户端侧中止（无任何 `STOP_`/`Traceback`；6 个 wave 共 382 个新单元已正确派发并由远端容器全部算完），续跑 App 于 01:53:25Z→02:02:05Z 对全部 384 格逐一核验（`checked_receipt`：收据身份/工件字节/源信号 SHA），无新增 wave（0 重算）、0 训练，随后导出汇总并完成 16 线程拉取；两次 App 与全部日志均留档。
- **完成新增回放单元数 (`l1cells`)**：`384/384`（合并 Stage 0+1 共 `480/480`，新增模型训练 `0` 次；384 份 l1cell `receipt.json` 全部 `status=PASS`、`fit_performed=false`、`new_fits=0`，每格 424 天 2025-01-02→2026-09-30）
- **16 线程并发拉取耗时与 `SHA256SUMS.txt` 校验**：`STAGE2_PULL_OK 1154 files (1154 new, 0 existing)`（1152 = 384 l1cell × 3 工件 + `smoke_pass.json` + `stage2_l1_summary.json`）；续跑总墙钟 8 分 40 秒（拉取阶段占主导）；`quant/local_backup/09_csi300_csi500_stage2_l1/SHA256SUMS.txt` 共 1157 行（1154 个 `volume_snapshot` 文件 + 3 份日志），`sha256sum -c` 逐行通过（0 失败）。
- **本地独立复算（不信云端汇总）**：`ALL_STAGE2_VERIFY_RECOMPUTE_PASS` —— 384 格逐格重算 `job_sha256`（Gate 1 planner + 实测身份 bind）与工件字节 SHA、重算 48 份源信号 SHA 对本地 Stage 0/1 备份、重载 96 格 prior、拼接 480 格后用 `rank_market_120_strategies` / `select_top4_diverse_finalists` 独立重排名，与云端 `rankings`/`finalists` 完全一致。
- **数据提交**：`quant/data/csi300_csi500_research/stage2_l1/`（`stage2_l1_summary.json` + 480 行 `stage2_l1_scoreboard.csv` + 240 行 `stage2_l1_rankings.csv`）提交 `5677296b650a16288f3befc91a1d9741e82051f4`（代码仍锁定 `81d40e265d0fcdf980dcf3fd1b2673c6145af23f`，未改一行代码）。

### 3.1 沪深 300（`csi300`）入选阶段 3（五相位终选）的 Top 4 候选策略（`max_per_model=2`）
| 晋级顺位 | 总榜排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | m03_4e908173 | 20 | top8_drop1 | 32.28% | 33.42% | 41.47% | 1.555 | 1.572 | -12.29% |
| 2 | 2 | m04_17dbfe28 | 20 | top5_drop1 | 30.88% | 33.88% | 41.96% | 1.305 | 1.312 | -18.99% |
| 3 | 3 | m06_b034ac46 | 20 | top8_drop1 | 29.17% | 33.02% | 41.05% | 1.471 | 1.526 | -15.84% |
| 4 | 4 | m01_59b93929 | 20 | top8_drop1 | 27.41% | 31.50% | 39.43% | 1.479 | 1.488 | -11.75% |

### 3.2 中证 500（`csi500`）入选阶段 3（五相位终选）的 Top 4 候选策略（`max_per_model=2`）
| 晋级顺位 | 总榜排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | m10_raw10d_c98856b4 | 10 | top10_drop1 | 15.50% | 15.57% | 34.28% | 1.373 | 0.748 | -21.95% |
| 2 | 2 | m10_raw10d_c98856b4 | 10 | top15_drop2 | 12.51% | 15.60% | 34.31% | 1.457 | 0.809 | -18.69% |
| 3 | 7 | m09_raw10d_baseline | 10 | top30_drop2 | 9.42% | 9.54% | 27.28% | 1.273 | 0.535 | -18.63% |
| 4 | 10 | m09_raw10d_baseline | 10 | top20_drop1 | 8.00% | 9.94% | 27.74% | 1.283 | 0.524 | -18.51% |

### 3.3 沪深 300（`csi300`）120 套策略双相位排行榜 Top 20
| 排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | Phase0 超额年化 | Phase10 超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | m03_4e908173 | 20 | top8_drop1 | 32.28% | 33.42% | 32.28% | 34.56% | 41.47% | 1.555 | 1.572 | -12.29% |
| 2 | m04_17dbfe28 | 20 | top5_drop1 | 30.88% | 33.88% | 30.88% | 36.89% | 41.96% | 1.305 | 1.312 | -18.99% |
| 3 | m06_b034ac46 | 20 | top8_drop1 | 29.17% | 33.02% | 36.87% | 29.17% | 41.05% | 1.471 | 1.526 | -15.84% |
| 4 | m01_59b93929 | 20 | top8_drop1 | 27.41% | 31.50% | 27.41% | 35.59% | 39.43% | 1.479 | 1.488 | -11.75% |
| 5 | m01_59b93929 | 20 | top10_drop1 | 26.80% | 28.42% | 26.80% | 30.05% | 36.17% | 1.494 | 1.477 | -11.20% |
| 6 | m06_b034ac46 | 20 | top10_drop1 | 26.47% | 28.90% | 26.47% | 31.33% | 36.68% | 1.438 | 1.479 | -12.54% |
| 7 | m06_b034ac46 | 20 | top10_drop2 | 26.18% | 26.53% | 26.88% | 26.18% | 34.16% | 1.363 | 1.387 | -12.29% |
| 8 | m03_4e908173 | 20 | top10_drop1 | 25.65% | 29.75% | 33.85% | 25.65% | 37.58% | 1.524 | 1.518 | -13.26% |
| 9 | m01_59b93929 | 20 | top5_drop1 | 25.47% | 26.53% | 27.59% | 25.47% | 34.17% | 1.212 | 1.150 | -15.15% |
| 10 | m08_mid_leaf31_lr05 | 20 | top5_drop1 | 24.83% | 24.94% | 25.05% | 24.83% | 32.48% | 1.176 | 1.110 | -24.86% |
| 11 | m08_mid_leaf31_lr05 | 20 | top8_drop1 | 23.62% | 27.73% | 31.84% | 23.62% | 35.44% | 1.381 | 1.311 | -16.83% |
| 12 | m08_mid_leaf31_lr05 | 20 | top10_drop1 | 23.18% | 24.15% | 25.12% | 23.18% | 31.64% | 1.320 | 1.243 | -17.12% |
| 13 | m03_4e908173 | 20 | top5_drop1 | 21.06% | 24.46% | 27.85% | 21.06% | 31.97% | 1.150 | 1.048 | -19.89% |
| 14 | m06_b034ac46 | 20 | top15_drop1 | 20.26% | 21.02% | 21.78% | 20.26% | 28.32% | 1.317 | 1.264 | -12.91% |
| 15 | m08_mid_leaf31_lr05 | 20 | top15_drop1 | 19.78% | 24.27% | 28.76% | 19.78% | 31.77% | 1.507 | 1.382 | -16.12% |
| 16 | m06_b034ac46 | 20 | top15_drop2 | 19.47% | 20.85% | 22.22% | 19.47% | 28.14% | 1.251 | 1.231 | -12.70% |
| 17 | m04_17dbfe28 | 20 | top8_drop1 | 19.19% | 20.10% | 19.19% | 21.00% | 27.34% | 1.055 | 1.011 | -20.24% |
| 18 | m09_raw10d_baseline | 10 | top5_drop1 | 19.11% | 23.81% | 19.11% | 28.50% | 31.28% | 1.117 | 1.025 | -15.87% |
| 19 | m04_17dbfe28 | 20 | top10_drop1 | 18.73% | 19.33% | 19.92% | 18.73% | 26.53% | 1.100 | 1.057 | -17.18% |
| 20 | m03_4e908173 | 20 | top15_drop1 | 18.49% | 21.32% | 24.14% | 18.49% | 28.64% | 1.396 | 1.287 | -11.48% |

### 3.4 中证 500（`csi500`）120 套策略双相位排行榜 Top 20
| 排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | Phase0 超额年化 | Phase10 超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | m10_raw10d_c98856b4 | 10 | top10_drop1 | 15.50% | 15.57% | 15.65% | 15.50% | 34.28% | 1.373 | 0.748 | -21.95% |
| 2 | m10_raw10d_c98856b4 | 10 | top15_drop2 | 12.51% | 15.60% | 12.51% | 18.69% | 34.31% | 1.457 | 0.809 | -18.69% |
| 3 | m10_raw10d_c98856b4 | 10 | top15_drop1 | 11.98% | 13.17% | 14.36% | 11.98% | 31.49% | 1.410 | 0.686 | -20.15% |
| 4 | m10_raw10d_c98856b4 | 10 | top8_drop1 | 11.79% | 16.00% | 11.79% | 20.22% | 34.78% | 1.342 | 0.734 | -23.61% |
| 5 | m10_raw10d_c98856b4 | 10 | top5_drop1 | 10.22% | 10.67% | 11.12% | 10.22% | 28.59% | 1.032 | 0.473 | -30.58% |
| 6 | m10_raw10d_c98856b4 | 10 | top30_drop2 | 9.54% | 11.13% | 9.54% | 12.72% | 29.12% | 1.398 | 0.637 | -17.59% |
| 7 | m09_raw10d_baseline | 10 | top30_drop2 | 9.42% | 9.54% | 9.42% | 9.67% | 27.28% | 1.273 | 0.535 | -18.63% |
| 8 | m10_raw10d_c98856b4 | 10 | top30_drop3 | 8.19% | 11.97% | 8.19% | 15.76% | 30.10% | 1.371 | 0.685 | -18.38% |
| 9 | m10_raw10d_c98856b4 | 10 | top20_drop2 | 8.12% | 14.32% | 8.12% | 20.52% | 32.82% | 1.459 | 0.763 | -15.89% |
| 10 | m09_raw10d_baseline | 10 | top20_drop1 | 8.00% | 9.94% | 8.00% | 11.88% | 27.74% | 1.283 | 0.524 | -18.51% |
| 11 | c98856b4 | 20 | top30_drop3 | 7.50% | 7.67% | 7.85% | 7.50% | 25.10% | 1.297 | 0.435 | -16.80% |
| 12 | m09_raw10d_baseline | 10 | top30_drop3 | 7.01% | 7.77% | 7.01% | 8.53% | 25.22% | 1.153 | 0.431 | -23.62% |
| 13 | m03_4e908173 | 20 | top15_drop1 | 6.62% | 7.94% | 9.27% | 6.62% | 25.42% | 1.215 | 0.407 | -20.47% |
| 14 | m03_4e908173 | 20 | top20_drop1 | 6.23% | 8.79% | 11.34% | 6.23% | 26.39% | 1.396 | 0.435 | -15.75% |
| 15 | baseline | 20 | top15_drop2 | 5.81% | 7.49% | 5.81% | 9.17% | 24.89% | 1.203 | 0.384 | -25.22% |
| 16 | m06_b034ac46 | 20 | top8_drop1 | 5.45% | 10.03% | 5.45% | 14.61% | 27.84% | 1.170 | 0.485 | -24.83% |
| 17 | baseline | 20 | top30_drop2 | 5.01% | 6.28% | 5.01% | 7.55% | 23.49% | 1.248 | 0.322 | -18.32% |
| 18 | m03_4e908173 | 20 | top20_drop2 | 4.76% | 5.52% | 4.76% | 6.27% | 22.60% | 1.089 | 0.282 | -21.49% |
| 19 | m10_raw10d_c98856b4 | 10 | top20_drop1 | 4.72% | 7.03% | 9.35% | 4.72% | 24.36% | 1.171 | 0.376 | -20.19% |
| 20 | baseline | 20 | top20_drop2 | 4.61% | 7.46% | 4.61% | 10.30% | 24.85% | 1.251 | 0.387 | -20.26% |
