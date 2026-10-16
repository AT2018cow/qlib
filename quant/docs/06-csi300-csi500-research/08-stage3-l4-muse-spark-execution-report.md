# 阶段 3（L4）执行报告（由 DeepSeek 4.1 Flash / Muse Spark 按三门控分步填写）

> **执行模型**：DeepSeek 4.1 Flash / Muse Spark  
> **当前门控状态**：`PENDING_GATE1_CODE`（请按顺序更新为：`GATE1_CODE_READY_FOR_REVIEW` -> `GATE2_SMOKE_READY_FOR_REVIEW` -> `GATE3_COMPLETE_READY_FOR_REVIEW`）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/07-stage3-l4-execution-plan-for-muse-spark.md`

---

## Gate 1：代码实现与本地单元测试（写完代码后填此节并停下等待审查！）
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage3-l4`
- **Gate 1 Commit SHA（40 位 `code_sha`）**：`待填写`
- **新增文件列表**：
  - `quant/code/research/csi300_csi500/stage3_l4_core.py`
  - `quant/code/research/csi300_csi500/stage3_l4_modal.py`
  - `quant/tests/test_csi300_csi500_stage3_l4.py`
- **本地 `pytest` 运行结果**：`待填写`
- **`modal run ... --help` 离线加载检查**：`待填写`
- **是否发起过任何付费 Modal 运行**：`否（严格遵守 Gate 1 门控）`

---

## Gate 2：Smoke Test 报告（待 Reviewer 批准 Gate 1 后填写，跑完立即停下等待审查！）
- **执行时 40 位 `code_sha`**：`待填写`
- **Run ID**：`stage3_l4_20261016_a1`
- **Modal App ID (`ap-...`)**：`待填写`
- **退出码 (`EXIT`)**：`待填写`
- **前序 36 个 Phase (0, 10) 单元远端盘点结果**：`待填写`
- **Smoke 切片 1（`csi300 / m03_4e908173 / h=20 / phase4 / 2024-12-25`）结果**：`待填写`
- **Smoke 切片 2（`csi500 / m10_raw10d_c98856b4 / h=10 / phase4 / 2024-12-25`）结果**：`待填写`
- **Smoke 本地日志路径**：`quant/local_backup/10_csi300_csi500_stage3_l4/stage3_smoke.log`

---

## Gate 3：Complete 全量运行与五相位终选总榜（待 Reviewer 批准 Gate 2 后填写）
- **Modal Complete App ID (`ap-...`)**：`待填写`
- **完成新相位训练切片数 (`l4retrain`)**：`0/660`
- **完成新相位回放单元数 (`l4cell`)**：`0/54`（合并 Phase 0/10 的 36 格共 `0/90` 格）
- **16 线程并发拉取（跳过 `fits/`，仅拉 `cells/`+`signals/`+summary）耗时与 `SHA256SUMS.txt` 校验**：`待填写`

### 3.1 沪深 300（`csi300`，基准 `SH000300`）五相位 `[0, 4, 6, 10, 15]` 终选总榜（9 套策略）
| 终选排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 五相位最差超额年化 | 五相位中位超额年化 | 五相位平均超额年化 | 五相位中位 CAGR | 五相位平均 CAGR | 五相位中位 Sharpe | 五相位中位 IR | 五相位最深回撤 | 胜出基准相位数 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|

### 3.2 中证 500（`csi500`，基准 `SH000905`）五相位 `[0, 4, 6, 10, 15]` 终选总榜（9 套策略）
| 终选排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 五相位最差超额年化 | 五相位中位超额年化 | 五相位平均超额年化 | 五相位中位 CAGR | 五相位平均 CAGR | 五相位中位 Sharpe | 五相位中位 IR | 五相位最深回撤 | 胜出基准相位数 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|

### 3.3 沪深 300 与中证 500 冠军策略五相位逐相明细表（Phase 00 / 04 / 06 / 10 / 15）
（请在此列出两个指数排名第 1 的策略在全部 5 个相位的逐相累计净收益、CAGR、超额年化、Sharpe、IR、MaxDD 明细）
