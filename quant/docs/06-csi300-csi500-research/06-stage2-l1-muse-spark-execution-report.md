# 阶段 2（L1）执行报告（由 Muse Spark 按三门控分步填写）

> **执行模型**：Muse Spark  
> **当前门控状态**：`PENDING_GATE1_CODE`（请按顺序更新为：`GATE1_CODE_READY_FOR_REVIEW` -> `GATE2_SMOKE_READY_FOR_REVIEW` -> `GATE3_COMPLETE_READY_FOR_REVIEW`）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/05-stage2-l1-execution-plan-for-muse-spark.md`

---

## Gate 1：代码实现与本地单元测试（写完代码后填此节并停下等待审查！）
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage2-l1`
- **Gate 1 Commit SHA（40 位 `code_sha`）**：`待填写`
- **新增文件列表**：
  - `quant/code/research/csi300_csi500/stage2_l1_core.py`
  - `quant/code/research/csi300_csi500/stage2_l1_modal.py`
  - `quant/tests/test_csi300_csi500_stage2_l1.py`
- **本地 `pytest` 运行结果**：`待填写`
- **`modal run ... --help` 离线加载检查**：`待填写`
- **是否发起过任何付费 Modal 运行**：`否（严格遵守 Gate 1 门控）`

---

## Gate 2：Smoke Test 报告（待 Reviewer 批准 Gate 1 后填写，跑完立即停下等待审查！）
- **执行时 40 位 `code_sha`**：`待填写`
- **Run ID**：`stage2_l1_20261016_a1`
- **Modal App ID (`ap-...`)**：`待填写`
- **退出码 (`EXIT`)**：`待填写`
- **48 份源 `signal.parquet` 远端 SHA 盘点结果**：`待填写`
- **Smoke 单元 1（`csi300 / m01_59b93929 / top8_drop1 / phase0`）结果**：`待填写`
- **Smoke 单元 2（`csi500 / m10_raw10d_c98856b4 / top15_drop2 / phase0`）结果**：`待填写`
- **Smoke 本地日志路径**：`quant/local_backup/09_csi300_csi500_stage2_l1/stage2_smoke.log`

---

## Gate 3：Complete 全量运行与 480 格双指数总榜（待 Reviewer 批准 Gate 2 后填写）
- **Modal Complete App ID (`ap-...`)**：`待填写`
- **完成新增回放单元数 (`l1cells`)**：`0/384`（合并 Stage 0+1 共 `0/480`，新增模型训练 `0` 次）
- **16 线程并发拉取耗时与 `SHA256SUMS.txt` 校验**：`待填写`

### 3.1 沪深 300（`csi300`）入选阶段 3（五相位终选）的 Top 4 候选策略（`max_per_model=2`）
| 晋级顺位 | 总榜排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|

### 3.2 中证 500（`csi500`）入选阶段 3（五相位终选）的 Top 4 候选策略（`max_per_model=2`）
| 晋级顺位 | 总榜排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|

### 3.3 沪深 300（`csi300`）120 套策略双相位排行榜 Top 20
| 排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | Phase0 超额年化 | Phase10 超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|

### 3.4 中证 500（`csi500`）120 套策略双相位排行榜 Top 20
| 排名 | 模型 (`model_key`) | 标签期限 | 组合 (`combo_key`) | 双相位最差超额年化 | 双相位平均超额年化 | Phase0 超额年化 | Phase10 超额年化 | 双相位平均 CAGR | 平均 Sharpe | 平均 IR | 最深回撤 |
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
