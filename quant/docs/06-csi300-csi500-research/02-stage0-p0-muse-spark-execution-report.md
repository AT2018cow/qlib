# 阶段 0（P0）执行报告（由 Muse Spark 填写）

> **执行模型**：Muse Spark  
> **当前状态**：`PENDING_EXECUTION`（请随着执行进展更新为 `IN_PROGRESS` -> `READY_FOR_REVIEW`）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/01-stage0-p0-execution-plan-for-muse-spark.md`

---

## 1. 环境与代码提交信息
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage0-p0`
- **实验执行时 40 位 `code_sha`**：`待填写`
- **Run ID**：`stage0_p0_20261015_a1`
- **Modal Workspace / Profile**：`infi`
- **Research Volume v2 名称**：`qlib-csi300-csi500-research-v2`
- **本地单元测试结果（`pytest`）**：`待填写`

---

## 2. Modal 运行记录与 App ID
| 运行阶段 | 命令 | Modal App ID (`ap-...`) | 退出码 (`EXIT`) | 完成训练数 (`fits`) | 完成回放数 (`cells`) | 本地日志路径 |
|---|---|---|---:|---:|---:|---|
| Smoke | `modal run --profile infi ... --mode smoke` | `待填写` | `待填写` | `0/2` | `0/0` | `quant/local_backup/07_csi300_csi500_stage0_p0/stage0_smoke.log` |
| Complete | `modal run --profile infi ... --mode complete` | `待填写` | `待填写` | `0/176` | `0/16` | `quant/local_backup/07_csi300_csi500_stage0_p0/stage0_complete.log` |

---

## 3. 静态报价并集与数据源校验结果
- `provider_fingerprint`: `待填写`
- `snapshot_token`: `待填写`
- `csi300` 静态报价股票数 / `quote_sha256`：`待填写`
- `csi500` 静态报价股票数 / `quote_sha256`：`待填写`

---

## 4. 沪深 300（`csi300`，基准 `SH000300`）双相位基线回放结果（16 格之 8）

| 模型 (`model_key`) | 组合 (`topk/dropn`) | 相位 (`phase`) | 累计净收益 (`total_return`) | 年化净收益 (`strategy_cagr`) | 基准年化 (`benchmark_cagr`) | 相对超额年化 (`relative_excess_cagr`) | 夏普比 (`sharpe`) | 信息比率 (`ir`) | 最大回撤 (`max_drawdown`) | 日均换手 (`mean_turnover`) | +10bp 净总收益 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | `Top20/Drop2` | 0 | | | | | | | | | |
| `baseline` | `Top20/Drop2` | 10 | | | | | | | | | |
| `baseline` | `Top10/Drop1` | 0 | | | | | | | | | |
| `baseline` | `Top10/Drop1` | 10 | | | | | | | | | |
| `c98856b4` | `Top20/Drop2` | 0 | | | | | | | | | |
| `c98856b4` | `Top20/Drop2` | 10 | | | | | | | | | |
| `c98856b4` | `Top10/Drop1` | 0 | | | | | | | | | |
| `c98856b4` | `Top10/Drop1` | 10 | | | | | | | | | |

---

## 5. 中证 500（`csi500`，基准 `SH000905`）双相位基线回放结果（16 格之 8）

| 模型 (`model_key`) | 组合 (`topk/dropn`) | 相位 (`phase`) | 累计净收益 (`total_return`) | 年化净收益 (`strategy_cagr`) | 基准年化 (`benchmark_cagr`) | 相对超额年化 (`relative_excess_cagr`) | 夏普比 (`sharpe`) | 信息比率 (`ir`) | 最大回撤 (`max_drawdown`) | 日均换手 (`mean_turnover`) | +10bp 净总收益 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | `Top20/Drop2` | 0 | | | | | | | | | |
| `baseline` | `Top20/Drop2` | 10 | | | | | | | | | |
| `baseline` | `Top10/Drop1` | 0 | | | | | | | | | |
| `baseline` | `Top10/Drop1` | 10 | | | | | | | | | |
| `c98856b4` | `Top20/Drop2` | 0 | | | | | | | | | |
| `c98856b4` | `Top20/Drop2` | 10 | | | | | | | | | |
| `c98856b4` | `Top10/Drop1` | 0 | | | | | | | | | |
| `c98856b4` | `Top10/Drop1` | 10 | | | | | | | | | |

---

## 6. 本地冷备与产物完整性清单
- 冷备目录：`/home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/`
- `SHA256SUMS.txt` 校验状态：`待填写`
- 异常或备注记录：`待填写`
