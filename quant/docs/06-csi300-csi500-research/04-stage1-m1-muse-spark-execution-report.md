# 阶段 1（M1）执行报告（由 Muse Spark 按三门控分步填写）

> **执行模型**：Muse Spark  
> **当前门控状态**：`PENDING_GATE1_CODE`（请按顺序更新为：`GATE1_CODE_READY_FOR_REVIEW` -> `GATE2_SMOKE_READY_FOR_REVIEW` -> `GATE3_COMPLETE_READY_FOR_REVIEW`）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/03-stage1-m1-execution-plan-for-muse-spark.md`

---

## Gate 1：代码实现与本地单元测试（写完代码后填此节并停下等待审查！）
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage1-m1`
- **Gate 1 Commit SHA（40 位 `code_sha`）**：`待填写`
- **新增文件列表**：
  - `quant/code/research/csi300_csi500/stage1_m1_core.py`
  - `quant/code/research/csi300_csi500/stage1_m1_modal.py`
  - `quant/tests/test_csi300_csi500_stage1_m1.py`
- **10 套模型 `candidate_id` 与 `model_sha256` 本地复算校验**：`待填写`
- **本地 `pytest` 运行结果**：`待填写`
- **`modal run ... --help` 离线加载检查**：`待填写`
- **是否发起过任何付费 Modal 运行**：`否（严格遵守 Gate 1 门控）`

---

## Gate 2：Smoke Test 报告（待 Reviewer 批准 Gate 1 后填写，跑完立即停下等待审查！）
- **执行时 40 位 `code_sha`**：`待填写`
- **Run ID**：`stage1_m1_20261015_a1`
- **Modal App ID (`ap-...`)**：`待填写`
- **退出码 (`EXIT`)**：`待填写`
- **Smoke 切片 1（`csi300 / m01_59b93929 / raw20 / phase0 / 2024-12-31`）校验结果**：`待填写`
- **Smoke 切片 2（`csi500 / m09_raw10d_baseline / raw10 / phase0 / 2024-12-31`）校验结果**：`待填写`
- **Smoke 本地日志路径**：`quant/local_backup/08_csi300_csi500_stage1_m1/stage1_smoke.log`

---

## Gate 3：Complete 全量运行与双指数排行榜（待 Reviewer 批准 Gate 2 后填写）
- **Modal Complete App ID (`ap-...`)**：`待填写`
- **完成训练切片数 (`fits`)**：`0/880`
- **完成新增回放单元数 (`cells`)**：`0/80`（合并 Stage 0 共 `0/96`）
- **本地冷备目录与 `SHA256SUMS.txt` 校验**：`待填写`

### 3.1 沪深 300（`csi300`，基准 `SH000300`）12 模型 × 2 组合双相位汇总排名表（共 24 行，按双相位最差超额年化降序）
| 排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | Phase0 超额年化 | Phase10 超额年化 | 双相位平均 CAGR | 双相位平均 Sharpe | 双相位平均 IR | 双相位最深回撤 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|

### 3.2 中证 500（`csi500`，基准 `SH000905`）12 模型 × 2 组合双相位汇总排名表（共 24 行，按双相位最差超额年化降序）
| 排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | Phase0 超额年化 | Phase10 超额年化 | 双相位平均 CAGR | 双相位平均 Sharpe | 双相位平均 IR | 双相位最深回撤 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
