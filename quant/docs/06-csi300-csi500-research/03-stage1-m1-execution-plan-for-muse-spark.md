# 阶段 1（M1）执行计划：沪深 300（`csi300`）与中证 500（`csi500`）候选模型与预测期限双相位滚动训练

> **目标读者**：执行模型（Muse Spark）  
> **计划制定与验收方**：主架构模型（Reviewer）  
> **当前阶段**：Stage 1（M1 — 每指数新增 **10 套**代表性超参数与标签期限模型 × `Phase 00 / Phase 10` 双相位滚动训练 + 固化 424 日 `signal.parquet` + 双锚点组合回放）  

---

## ⚠️ 极其重要：三门控分步执行铁律（Three-Gate Workflow）

**本阶段工作严禁一次性从写代码直接跑到全量实验！必须严格分成以下三个独立门控（Gate）分步推进，每完成一个 Gate 必须立即停下等待主架构模型（Reviewer）审查批准：**

- **【Gate 1：仅写代码与本地单元测试（当前仅授权执行此步！）】**
  - 完成 `stage1_m1_core.py`、`stage1_m1_modal.py` 与 `test_csi300_csi500_stage1_m1.py` 编写；
  - 运行本地 `pytest` 与 `modal run ... --help` 通过；
  - 在 Worktree 分支提交代码，并将汇报文档状态更新为 `GATE1_CODE_READY_FOR_REVIEW`，**立即停止，严禁在 Gate 1 发起任何付费 `modal run`！**
- **【Gate 2：仅执行 Smoke Test（待 Reviewer 审查代码通过并明确下令后执行）】**
  - 仅执行 `--mode smoke --max-workers 2`（跑 2 个真实训练切片：1 个 `raw20` + 1 个 `raw10`）；
  - 更新汇报文档中的 Smoke 日志、App ID、Receipt 校验结果，状态更新为 `GATE2_SMOKE_READY_FOR_REVIEW`，**立即停止，严禁直接跑 Complete！**
- **【Gate 3：执行 Complete 全量实验（待 Reviewer 审查 Smoke 报告通过并明确下令后执行）】**
  - 使用 `--mode complete --max-workers 64` 跑完剩余 878 个训练切片与 80 个回放单元，合并 Stage 0 的 2 套锚点模型生成 12 模型 × 2 组合 × 2 相位 = 96 格完整双指数排行榜，拉回冷备目录并更新状态为 `GATE3_COMPLETE_READY_FOR_REVIEW`。

---

## 一、工作区隔离（Git Worktree）与本地沟通文件

1. **你的专属代码工作目录（Worktree）**：
   - 路径：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
   - 分支名：`feat/csi300-csi500-stage1-m1`（已从包含 Stage 0 成果的最新 `main` 创建）
   - Python / Modal：`/home/ss/git_repos/qlib/.venv/bin/python` 与 `/home/ss/git_repos/qlib/.venv/bin/modal`
   - **严禁直接修改 `/home/ss/git_repos/qlib` 主目录代码。**
2. **本地沟通与产物路径**：
   - **本执行计划（只读）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/03-stage1-m1-execution-plan-for-muse-spark.md`
   - **你的三阶段执行报告（实时填写）**：
     `/home/ss/git_repos/qlib/quant/docs/06-csi300-csi500-research/04-stage1-m1-muse-spark-execution-report.md`
   - **本地冷备目录（Gate 2/3 使用）**：
     `/home/ss/git_repos/qlib/quant/local_backup/08_csi300_csi500_stage1_m1/`
   - **入库轻量汇总目录（Gate 3 完成后提交到 Worktree）**：
     `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0/quant/data/csi300_csi500_research/stage1_m1/`

---

## 二、阶段 1（M1）候选模型名册（10 套新增模型 + 复用 Stage 0 的 2 套锚点模型）

所有哈希均已预先通过 `stable_sha(cfg["task"]["model"])` 与 `build_candidate_spec` 严格验算，请在 `stage1_m1_core.py` 中原样写入以下 `STAGE1_NEW_MODELS` 字典（**不得改动任何参数或哈希**）：

```python
STAGE1_NEW_MODELS = {
    # Group A: Stage-A/B 验证过的 6 套多元树深/正则化代表簇 (label_horizon=20)
    "m01_59b93929": {
        "candidate_id": "59b93929bff1e736228d31403a167781aff433d27da1870bcf9fe34e04b6bbb9",
        "model_sha256": "b97bc44d1c02acb4d85c344d2d570e00ef9ff365228c8a9550fa45393859ae67",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 100,
        },
    },
    "m02_a419047a": {
        "candidate_id": "a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677",
        "model_sha256": "28596be4fbe4f24b20fe419a3cfc2016192169b81b07eec50a7181119f9726d8",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 1.0,
            "lambda_l1": 50.0,
            "lambda_l2": 200.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 200,
        },
    },
    "m03_4e908173": {
        "candidate_id": "4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2",
        "model_sha256": "0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.08,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 63,
            "min_data_in_leaf": 50,
        },
    },
    "m04_17dbfe28": {
        "candidate_id": "17dbfe28fea8d7a782918ee426e01c7f7b23a08b44c00cc7e2d8391974efd5f2",
        "model_sha256": "5a311bfc78b40b2758d77c1ba55c092c1953447f133a111d4ed6b28bae39cbc2",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.08,
            "colsample_bytree": 0.8,
            "lambda_l1": 10.0,
            "lambda_l2": 100.0,
            "max_depth": 10,
            "num_leaves": 127,
            "min_data_in_leaf": 100,
        },
    },
    "m05_e74b3bbe": {
        "candidate_id": "e74b3bbe2278aa89b33375fe21bc308bc5cf0b2e5ee512ab1b05469b52a4825a",
        "model_sha256": "e4d33fa7175a1e01068b6d4afcd8525c706079d2f9fc8d668ef41a1b8ee6eae4",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.9,
            "lambda_l1": 100.0,
            "lambda_l2": 400.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 200,
        },
    },
    "m06_b034ac46": {
        "candidate_id": "b034ac4645fb45bba35bc185693b7776344c9117f594e4c972671062cdcf9457",
        "model_sha256": "d095b0fe976e8f438f086a2b3a31bbb70d8d8560aa18e1594c3b2d299329a901",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.8,
            "lambda_l1": 50.0,
            "lambda_l2": 200.0,
            "max_depth": 8,
            "num_leaves": 31,
            "min_data_in_leaf": 200,
        },
    },
    # Group B: 针对 300/500 较小截面定制的浅树抗过拟合超参数簇 (label_horizon=20)
    "m07_shallow_leaf15": {
        "candidate_id": "364c2d58afb727d7051f0910e0ebe476caec55a271cd314faeda240e3c8b8ced",
        "model_sha256": "5c4543183558ada6b58ccb0833e7422d9643d643914d3d8f69dd766018deb1b9",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 0.7,
            "lambda_l1": 20.0,
            "lambda_l2": 100.0,
            "max_depth": 4,
            "num_leaves": 15,
            "min_data_in_leaf": 50,
        },
    },
    "m08_mid_leaf31_lr05": {
        "candidate_id": "0bb48b85dc56b19683ad77c061e4b44df8ec4ea603a63da516b745eac65c657b",
        "model_sha256": "b3c4345e07f1bd5f75bb7aeb505f40d8a20e28648459678ddb0efce0efe3fdf6",
        "label_horizon": 20,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 0.7,
            "lambda_l1": 50.0,
            "lambda_l2": 200.0,
            "max_depth": 6,
            "num_leaves": 31,
            "min_data_in_leaf": 50,
        },
    },
    # Group C: 10日预测标签期限簇 (label_horizon=10, 表达式 "Ref($close, -10)/$close - 1")
    "m09_raw10d_baseline": {
        "candidate_id": "4f11110de555e962750048aaa68f3c5d636ffe59e835ee37b51ce991dc79e5ce",
        "model_sha256": "793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3",
        "label_horizon": 10,
        "model_params": {
            "learning_rate": 0.1,
            "colsample_bytree": 0.9,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 250,
            "min_data_in_leaf": 20,
        },
    },
    "m10_raw10d_c98856b4": {
        "candidate_id": "99627a9d20842f5ac43874593b71b175cc533fe2f09666a9417a589f35c7eb1f",
        "model_sha256": "d49e893ad37c821a6a092fb17baf7ee29c9ff71289bbeb1205132e34e3854c50",
        "label_horizon": 10,
        "model_params": {
            "learning_rate": 0.05,
            "colsample_bytree": 1.0,
            "lambda_l1": 205.6999,
            "lambda_l2": 200.0,
            "max_depth": 10,
            "num_leaves": 250,
            "min_data_in_leaf": 200,
        },
    },
}
```

---

## 三、架构与实现细节要求（必看！）

### 3.1 预测标签期限（`label_horizon` = 20 vs 10）的正确处理
在 Stage 1 中，`m01`~`m08` 的 `label_horizon = 20`，而 `m09`、`m10` 的 `label_horizon = 10`！
你在 `stage1_m1_modal.py` 的 `_task(model_params, market, label_horizon)` 和 `fit_remote(job)` 中**必须根据 `job["label_horizon"]` 动态处理标签表达式、样本成熟边界与 purge**：
1. 在 `_task(model_params, market, label_horizon)` 中：
   - 校验 `require(label_horizon in (10, 20), "STAGE1_M1 invalid label_horizon")`
   - 设置 `ds["handler"]["kwargs"]["label"] = [f"Ref($close, -{int(label_horizon)})/$close - 1"]`
2. 在 `fit_remote(job)` 中计算验证集/训练集切分与 purge 时，**必须使用 `horizon = int(job["label_horizon"])`**（切勿硬编码 `20`！）：
   ```python
   horizon = int(job["label_horizon"])
   require(horizon in (10, 20), "STOP_STAGE1_HORIZON")
   valid_end = asof - horizon - 1
   valid_start = valid_end - VALIDATION_SESSIONS + 1
   require(valid_start > 0, "STOP_STAGE1_LABEL_MATURITY")
   train_end = last_matured_sample(calendar, calendar[valid_start], horizon)
   ...
   purge_cfg_splits(config, calendar, horizon=horizon)
   ```

### 3.2 任务规模与 Stage 0 锚点复用
1. **新增滚动训练任务（`retrain` jobs）**：
   - `2 股票池 (csi300, csi500) × 10 新增模型 (m01..m10) × 2 相位 (0, 10) × 22 重训日 = 880` 个训练切片。
2. **新增组合回放任务（`cell` jobs）**：
   - 每个 `(market, model_key, phase)` 生成 424 日 `signal.parquet` 后，分别在 `Top20/Drop2` 与 `Top10/Drop1` 下回放：`2 股票池 × 10 新增模型 × 2 组合 × 2 相位 = 80` 个新增回放单元。
   - **重要设计（为阶段 2 L1 铺路）**：每个 `cell` 的 `receipt` 目录除了保存 `fixed_report.parquet`、`decisions.json`、`signal.parquet` 外，还应在 `/results/<prefix>/signals/<market>/<model_key>/phase<phase:02d>/signal.parquet`（或直接从 `top20_drop2` cell 目录）可确定性读取，方便阶段 2 零训练成本遍历 10 种 `TopK/DropN`！
3. **汇总与独立排名（`export_summary_remote` & 本地排行榜生成）**：
   - 汇总本次 80 个新增 `cell` + 读取 Stage 0 已验算的 16 个锚点 `cell`（共 **96 个 cell**，每指数 48 个 cell = 12 模型 × 2 组合 × 2 相位）。
   - **针对 `csi300` 和 `csi500` 各自独立排名**：
     - 对每个 `(market, model_key, combo_key)` 计算双相位（Phase 0 与 Phase 10）汇总指标：
       - `worst_relative_excess_cagr = min(rel_cagr_p0, rel_cagr_p10)`
       - `mean_relative_excess_cagr = 0.5 * (rel_cagr_p0 + rel_cagr_p10)`
       - `worst_strategy_cagr = min(cagr_p0, cagr_p10)`
       - `mean_strategy_cagr = 0.5 * (cagr_p0 + cagr_p10)`
       - `mean_sharpe = 0.5 * (sharpe_p0 + sharpe_p10)`
       - `mean_ir = 0.5 * (ir_p0 + ir_p10)`
       - `worst_max_drawdown = min(mdd_p0, mdd_p10)`
       - `worst_stressed10bp_total_return = min(stress10_p0, stress10_p10)`
     - 按 `(worst_relative_excess_cagr, mean_relative_excess_cagr, mean_ir, mean_sharpe, worst_max_drawdown)` 降序排列，分别输出 `csi300` 的 24 套策略排名表与 `csi500` 的 24 套策略排名表！

### 3.3 Smoke 模式（`--mode smoke`）的具体定义
在 `stage1_m1_modal.py` 的 `--mode smoke` 中：
- 必须覆盖**两种不同 `label_horizon`（20 和 10）的真实训练路径**：
  1. 切片 1：`csi300` + `m01_59b93929` (`label_horizon=20`) + `phase=0` 第 1 个重训切片（`2024-12-31`）；
  2. 切片 2：`csi500` + `m09_raw10d_baseline` (`label_horizon=10`) + `phase=0` 第 1 个重训切片（`2024-12-31`）。
- 两个切片跑完并通过 receipt SHA 校验后，写入 `smoke_pass.json`（注意直接写 `{"run_id": ..., "code_sha": ..., "status": "PASS", ...}`，复用你在 Stage 0 修复后的 `freeze_remote` 逻辑），打印 `STAGE1_SMOKE_PASS 2/2 fits verified (raw20 + raw10)`。

### 3.4 Complete 模式（`--mode complete`）的并发与容错
- 默认 `--max-workers 64`（`fit_remote` 容器规格保持 `cpu=8, memory=16384, timeout=3600, retries=0`；为防偶发 `Worker disappeared` 导致单波中断，允许对 `fit_remote` 设置 `retries=1` 或在本地协调器中若单波出现瞬态容器消失可自动重扫未完成 receipt 续跑一波）。
- 支持可选参数 `--max-fit-waves <int>`（默认为 `0` 表示不限制波数直到全部 880 个 fit 完成；若设置 `>0`，跑满指定波数后打印 `STAGE1_SAFE_PAUSE` 安全退出，再次运行相同命令即可无缝续跑）。

---

## 四、需要创建的文件清单（Gate 1 范围）

请在 `/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`（分支 `feat/csi300-csi500-stage1-m1`）中创建：
1. `quant/code/research/csi300_csi500/stage1_m1_core.py`
2. `quant/code/research/csi300_csi500/stage1_m1_modal.py`
3. `quant/tests/test_csi300_csi500_stage1_m1.py`（必须测试：10 套新模型的 `candidate_id` 与 `model_sha256` 精确复算一致、880 个 `retrain` 任务与 80 个 `cell` 任务几何唯一性、`label_horizon=10` 与 `20` 的切分/purge 正确性、双指数各自独立排名汇总函数、五大工程陷阱静态检查）。

**再次提醒：当前你只被授权执行 Gate 1（编写代码 + 运行本地 pytest + git commit 到 `feat/csi300-csi500-stage1-m1` + 更新 `04-stage1-m1-muse-spark-execution-report.md` 的 Gate 1 部分）。完成 Gate 1 后立即停下，等待 Reviewer 审查代码！**
