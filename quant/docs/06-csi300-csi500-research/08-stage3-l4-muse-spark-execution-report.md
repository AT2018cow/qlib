# 阶段 3（L4）执行报告（由 DeepSeek 4.1 Flash / Muse Spark 按三门控分步填写）

> **执行模型**：DeepSeek 4.1 Flash / Muse Spark  
> **当前门控状态**：`GATE3_COMPLETE_READY_FOR_REVIEW`（Gate 3 全量完成：660/660 新相位训练切片 + 54/54 新回放格（合并 90/90 五相位单元）+ 194 文件拉回 + `SHA256SUMS` 198/198 OK、`EXIT=0`；等待 Reviewer 验收）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/07-stage3-l4-execution-plan-for-muse-spark.md`

---

## Gate 1：代码实现与本地单元测试（Reviewer 审查修复后通过 ✅）
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage3-l4`
- **Gate 1 最终锁定 Commit SHA（40 位 `code_sha`）**：`04ec819c444614ef900b8b9886a70d554d9b2aa6`（Reviewer 在 `8e68e06b` 基础上补入 `from stage3_l4_core import retrain_splits` 并在 `test_stage3_modal_imports_all_resolve_in_core` 中加入全文件 AST 未定义符号扫描门禁，`git status --porcelain` 为空）
- **新增文件列表**：
  - `quant/code/research/csi300_csi500/stage3_l4_core.py`
  - `quant/code/research/csi300_csi500/stage3_l4_modal.py`
  - `quant/tests/test_csi300_csi500_stage3_l4.py`
- **本地 `pytest` 运行结果**：新增文件 `16 passed`（`quant/tests/test_csi300_csi500_stage3_l4.py`，离线 3.4s）；全量 `quant/tests`：`626 passed, 45 subtests passed, 1 warning`（38.3s）
- **`modal run ... --help` 离线加载检查**：`EXIT=0`（`modal run quant/code/research/csi300_csi500/stage3_l4_modal.py --help` 正常列出 `--mode/--run-id/--max-workers/--max-fit-waves/--pull-root`，未触发任何远端执行）
- **是否发起过任何付费 Modal 运行**：`否（严格遵守 Gate 1 门控）`

---

## Gate 2：Smoke Test 报告（待 Reviewer 批准 Gate 1 后填写，跑完立即停下等待审查！）

### Gate 2 Attempt 1（FAILED，2026-10-16，未发起 Complete，未修改任何代码）
- **执行时 40 位 `code_sha`**：`8e68e06b02d9b82f49fc22eedde35c60e6986d2d`
- **Modal App ID (`ap-...`)**：`ap-nnA504dCYdfRbHZHybkr65`
- **命令**：`modal run --profile infi quant/code/research/csi300_csi500/stage3_l4_modal.py --mode smoke --run-id stage3_l4_20261016_a1 --max-workers 2`（前台附着，无 `--detach`）
- **退出码 (`EXIT`)**：`1`（`SMOKE_EXIT=1`；日志未出现 `STAGE3_SMOKE_PASS`）
- **Smoke 本地日志路径**：`quant/local_backup/10_csi300_csi500_stage3_l4/stage3_smoke.log`
- **根因**：`fit_remote` 在第 513 行调用 `retrain_splits`，但 `stage3_l4_modal.py` 未导入该名称（定义于 `stage1_m1_core.py:339`；`stage1_m1_modal.py:89` 由 `stage1_m1_core` 导入）。pyflakes 全文件扫描确认这是两个 Stage 3 文件中**唯一**的未定义名。
- **失败影响面**：2 个 worker 的 `fit_remote`（含 1 次重试）均在 `publish_receipt` 之前失败；此前仅执行只读 inventory 与 `marker_remote` 存在性检查（返回空）。`modal volume ls qlib-csi300-csi500-research-v2 csi300_csi500_research/stage3_l4` 确认远端该路径不存在：无 receipt、无 `smoke_pass` marker、无任何 Volume 写入。
- **结论**：非基础设施问题，属代码导入遗漏；`git status --porcelain` 保持为空（未自行修改任何代码），等待 Reviewer 修复该导入并重新授权 Gate 2。

### Gate 2 Attempt 2（PASS，2026-10-16）
- **执行时 40 位 `code_sha`**：`04ec819c444614ef900b8b9886a70d554d9b2aa6`
- **Run ID**：`stage3_l4_20261016_a1`
- **Modal App ID (`ap-...`)**：`ap-KC18dudLC8TiiwfAfEPCpr`
- **命令**：`modal run --profile infi quant/code/research/csi300_csi500/stage3_l4_modal.py --mode smoke --run-id stage3_l4_20261016_a1 --max-workers 2`（前台附着，无 `--detach`）
- **退出码 (`EXIT`)**：`0`（`SMOKE_EXIT=0`；日志第 223 行 `STAGE3_SMOKE_PASS 2/2 new-phase fits verified (phase4 first slices)`）
- **前序 36 个 Phase (0, 10) 单元远端盘点结果**：`PASS`（36/36 前序单元全部核验并可绑定：stage0_p0 6 + stage1_m1 10 + stage2_l1 20；`prior_units_digest = 02b0a5e73b2b1599f63394828251c41fe66e163788be1f60b200159b433249fb`，inventory 未触发任何 STOP）
- **Smoke 切片 1（`csi300 / m03_4e908173 / h=20 / phase4 / 2024-12-25`）结果**：`PASS`（`fit_performed=true`，`best_iteration=27`，`signal_end=2025-01-22`，`job_sha256 = 8cc2c2cd8fa64303ea68d58922645bccd433415c2e093d49bbc1251fe70d382c`；`prediction.parquet` / `checkpoint.txt` SHA256 与 Volume 远端字节逐一核验一致）
- **Smoke 切片 2（`csi500 / m10_raw10d_c98856b4 / h=10 / phase4 / 2024-12-25`）结果**：`PASS`（`fit_performed=true`，`best_iteration=44`，`signal_end=2025-01-22`，`job_sha256 = c7a47bb717636ba040ec70a821304dbb6838de0e7e2007e5c5c16f3a5cdcfff1`；两产物 SHA256 同样核验一致）
- **`smoke_pass.json` 锁定内容**：`status=PASS`，`smoke_fit_job_shas` 与两 receipt `job_sha256` 完全一致（上列 2 个 SHA），`provider_snapshot_sha256 = 1d693911db026f0828ab9922617e19c7a3cde7211320a5b69be8ea26077c7b88`，quote SHA：csi300 `9f3756ea…` / csi500 `cfcbc21e…`，`fit_performed=true`，`zero_cells=true`，`horizons=[20,10]`
- **Smoke 本地日志路径**：`quant/local_backup/10_csi300_csi500_stage3_l4/stage3_smoke_attempt2.log`
- **拉回核验文件**：`quant/local_backup/10_csi300_csi500_stage3_l4/smoke_receipts/`（`smoke_pass.json` + 2 个 `receipt.json`）

---

## Gate 3：Complete 全量运行与五相位终选总榜（待 Reviewer 批准 Gate 2 后填写）
- **Modal Complete App ID (`ap-...`)**：`ap-Sh1fngyVVGUO1kkQqPC1Bi`（Part 1：前 5 波 330 fits，输出 `STAGE3_SAFE_PAUSE`，`EXIT=0`，墙钟 10 分 17 秒，03:52:26Z→04:02:43Z）+ `ap-jOnDoWjBUZhoawBUErsTaE`（Part 2：后 5 波 330 fits + 54 格回放 + 90 格导出 + 16 线程拉取，输出 `STAGE3_L4_COMPLETE`，`EXIT=0`，墙钟 21 分 05 秒，04:03:31Z→04:24:36Z）；全程前台附着、无 `--detach`，日志零 `STOP_`
- **完成新相位训练切片数 (`l4retrain`)**：`660/660`（10 个 (market, model) × 3 新相位 × 22 切片；660 份 receipt 全部 `status=PASS`、`fit_performed=true`，本阶段为全项目首次真实 LGB 训练轮次）
- **完成新相位回放单元数 (`l4cell`)**：`54/54`（合并 Phase 0/10 的 36 格 prior 共 `90/90` 格；54 份 receipt 逐一核验 22 个 fit 预测 SHA 与 canonical 共享信号写入审计）
- **16 线程并发拉取（跳过 `fits/`，仅拉 `cells/`+`signals/`+summary）耗时与 `SHA256SUMS.txt` 校验**：`STAGE3_PULL_OK 194 files (1980 fits ignored, 194 new, 0 existing)`（194 = 54 cells × 3 工件 + 30 共享 signals + `stage3_l4_summary.json` + `smoke_pass.json`，1980 个 `fits/` 文件按设计跳过）；`quant/local_backup/10_csi300_csi500_stage3_l4/SHA256SUMS.txt` 共 198 行（194 个 `volume_snapshot` 文件 + 4 份日志），`sha256sum -c` 198/198 通过（0 失败）
- **本地数据提交**：`quant/data/csi300_csi500_research/stage3_l4/`（`stage3_l4_summary.json` + 90 行 `stage3_l4_scoreboard.csv` + 18 行 `stage3_l4_rankings.csv`），提交 `834d9d0d`（执行代码锁定 `04ec819c444614ef900b8b9886a70d554d9b2aa6`，未改一行代码）

### 3.1 沪深 300（`csi300`，基准 `SH000300`）五相位 `[0, 4, 6, 10, 15]` 终选总榜（9 套策略）
| 终选排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 五相位最差超额年化 | 五相位中位超额年化 | 五相位平均超额年化 | 五相位中位 CAGR | 五相位平均 CAGR | 五相位中位 Sharpe | 五相位中位 IR | 五相位最深回撤 | 胜出基准相位数 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | m01_59b93929 | 20 | top8_drop1 | 24.19% | 25.79% | 27.58% | 33.39% | 35.28% | 1.321 | 1.261 | -14.46% | 5 |
| 2 | m03_4e908173 | 20 | top10_drop1 | 22.42% | 27.23% | 27.55% | 34.91% | 35.25% | 1.455 | 1.415 | -13.26% | 5 |
| 3 | m01_59b93929 | 20 | top10_drop1 | 21.66% | 27.84% | 27.12% | 35.55% | 34.79% | 1.455 | 1.409 | -12.92% | 5 |
| 4 | m06_b034ac46 | 20 | top10_drop1 | 18.51% | 24.15% | 24.22% | 31.65% | 31.72% | 1.306 | 1.289 | -16.49% | 5 |
| 5 | m06_b034ac46 | 20 | top8_drop1 | 17.66% | 29.17% | 27.98% | 36.97% | 35.70% | 1.358 | 1.380 | -19.38% | 5 |
| 6 | m03_4e908173 | 20 | top8_drop1 | 14.00% | 32.28% | 28.82% | 40.26% | 36.60% | 1.506 | 1.494 | -18.63% | 5 |
| 7 | baseline | 20 | top10_drop1 | 6.85% | 11.33% | 12.61% | 18.05% | 19.41% | 0.858 | 0.648 | -17.36% | 4 |
| 8 | m04_17dbfe28 | 20 | top5_drop1 | 6.46% | 18.16% | 21.26% | 25.29% | 28.58% | 0.907 | 0.827 | -33.42% | 4 |
| 9 | baseline | 20 | top20_drop2 | 4.28% | 6.91% | 8.58% | 13.36% | 15.13% | 0.750 | 0.475 | -18.31% | 0 |

### 3.2 中证 500（`csi500`，基准 `SH000905`）五相位 `[0, 4, 6, 10, 15]` 终选总榜（9 套策略）
| 终选排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 五相位最差超额年化 | 五相位中位超额年化 | 五相位平均超额年化 | 五相位中位 CAGR | 五相位平均 CAGR | 五相位中位 Sharpe | 五相位中位 IR | 五相位最深回撤 | 胜出基准相位数 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | m10_raw10d_c98856b4 | 10 | top10_drop1 | 15.50% | 22.13% | 22.69% | 41.90% | 42.55% | 1.669 | 1.061 | -21.95% | 5 |
| 2 | m10_raw10d_c98856b4 | 10 | top15_drop2 | 12.51% | 14.92% | 17.25% | 33.52% | 36.23% | 1.435 | 0.802 | -21.21% | 5 |
| 3 | m10_raw10d_c98856b4 | 10 | top15_drop1 | 11.76% | 13.51% | 13.22% | 31.88% | 31.55% | 1.443 | 0.680 | -20.63% | 5 |
| 4 | m09_raw10d_baseline | 10 | top30_drop2 | 9.42% | 10.33% | 10.97% | 28.19% | 28.93% | 1.336 | 0.583 | -18.63% | 4 |
| 5 | m10_raw10d_c98856b4 | 10 | top20_drop2 | 8.12% | 14.25% | 13.53% | 32.75% | 31.90% | 1.479 | 0.801 | -19.22% | 5 |
| 6 | m09_raw10d_baseline | 10 | top20_drop1 | 8.00% | 12.25% | 11.80% | 30.42% | 29.89% | 1.441 | 0.644 | -18.51% | 5 |
| 7 | c98856b4 | 20 | top30_drop3 | 1.91% | 7.50% | 5.90% | 24.90% | 23.04% | 1.297 | 0.416 | -16.80% | 2 |
| 8 | baseline | 20 | top20_drop2 | 1.59% | 4.61% | 5.94% | 21.54% | 23.09% | 1.135 | 0.216 | -20.26% | 0 |
| 9 | m03_4e908173 | 20 | top15_drop1 | 0.52% | 8.88% | 7.21% | 26.51% | 24.57% | 1.236 | 0.467 | -20.47% | 3 |

### 3.3 沪深 300 与中证 500 冠军策略五相位逐相明细表（Phase 00 / 04 / 06 / 10 / 15）

**沪深 300 冠军：`m01_59b93929 / h=20 / top8_drop1`**（Phase 4/6/15 为本次新训练切片，Phase 0/10 为此前冻结单元合并）

| 相位 | 累计净收益 | CAGR | 超额年化 | Sharpe | IR | MaxDD |
|---|---:|---:|---:|---:|---:|---:|
| Phase00 | 68.85% | 35.10% | 27.41% | 1.337 | 1.327 | -10.46% |
| Phase04 | 61.49% | 31.68% | 24.19% | 1.249 | 1.208 | -11.92% |
| Phase06 | 65.14% | 33.39% | 25.79% | 1.321 | 1.261 | -14.46% |
| Phase10 | 88.17% | 43.77% | 35.59% | 1.621 | 1.649 | -11.75% |
| Phase15 | 63.18% | 32.47% | 24.93% | 1.279 | 1.236 | -12.55% |

**中证 500 冠军：`m10_raw10d_c98856b4 / h=10 / top10_drop1`**（Phase 4/6/15 为本次新训练切片，Phase 0/10 为此前冻结单元合并）

| 相位 | 累计净收益 | CAGR | 超额年化 | Sharpe | IR | MaxDD |
|---|---:|---:|---:|---:|---:|---:|
| Phase00 | 67.27% | 34.37% | 15.65% | 1.348 | 0.777 | -16.80% |
| Phase04 | 83.94% | 41.90% | 22.13% | 1.669 | 1.061 | -19.84% |
| Phase06 | 108.04% | 52.30% | 31.08% | 1.958 | 1.322 | -17.06% |
| Phase10 | 66.89% | 34.19% | 15.50% | 1.398 | 0.719 | -21.95% |
| Phase15 | 102.59% | 50.00% | 29.10% | 1.902 | 1.283 | -16.72% |

**成本压力附加（终选总榜字段）**：csi300 冠军五相位最差压力超额（相对官方基准）10bp `+18.24%` / 20bp `+14.74%`；csi500 冠军对应 10bp `+12.80%` / 20bp `+12.75%`——两冠军在加码 10bp/20bp 交易成本后五相位仍全部跑赢官方基准。
