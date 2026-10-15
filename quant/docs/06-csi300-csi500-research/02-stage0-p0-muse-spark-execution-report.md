# 阶段 0（P0）执行报告（由 Muse Spark 填写）

> **执行模型**：Muse Spark  
> **当前状态**：`IN_PROGRESS`（请随着执行进展更新为 `IN_PROGRESS` -> `READY_FOR_REVIEW`）  
> **对应计划文档**：`quant/docs/06-csi300-csi500-research/01-stage0-p0-execution-plan-for-muse-spark.md`
> **执行开始时间（UTC）**：`2026-10-15` — Step 1 完成（分支 `feat/csi300-csi500-stage0-p0` ✓，Volume v2 `qlib-csi300-csi500-research-v2` 已存在 ✓）；Step 2 研读参考实现中。

---

## 1. 环境与代码提交信息
- **Worktree 路径**：`/home/ss/git_repos/qlib/.worktrees/muse_spark_stage0`
- **分支名称**：`feat/csi300-csi500-stage0-p0`
- **实验执行时 40 位 `code_sha`**：`93bab198d812ad56fec1476e524eb80d25f9da4a`（`git status --porcelain` 为空；`modal run --help` 本地加载验证通过）
- **code_sha 变更记录**：首版 `86bd6e13…` 的 smoke 暴露两处远程布局 bug（入口文件被扁平化为 `/root/*.py` 致 `parents[4]` 越界；镜像排除 `quant/data` 致 frozen JSON 不可读）→ `436535e6…`；该版 2 个 fit 真实训练完成（LightGBM best_iter 28/54），但 `freeze_remote` 误用 job identity 致 marker 写入 `KeyError` → 本版 `93bab198…`。前两版均未留下有效 smoke marker，旧前缀残留 2 个 fit receipt 作废（新 SHA 前缀隔离），smoke/complete 统一用本 SHA 重跑。
- **Run ID**：`stage0_p0_20261015_a1`
- **Modal Workspace / Profile**：`infi`
- **Research Volume v2 名称**：`qlib-csi300-csi500-research-v2`（`modal volume list --profile infi` 确认已存在，v2）
- **本地单元测试结果（`pytest`）**：`12 passed`（`quant/tests/test_csi300_csi500_stage0_p0.py`，含 176/16 名册几何、receipt 防篡改、成本压力、Modal 五大陷阱静态检查；`modal run --help` 通过）
- **与计划文本的关键差异（已验证，不改锚点）**：计划 §2.3 `c98856b4` 参数字面值 `{lr 0.05, colsample 0.7, l1 50, l2 200, depth 6, leaves 31, min_data 50}` 经实测哈希为 `b3c4345e…`，与计划宣称及生产冻结的 `model_sha256=d49e893a…` 不符；代码采用能复现该 SHA 的冻结生产参数 `{lr 0.05, colsample 1.0, l1 205.6999, l2 200, depth 10, leaves 250, min_data 200}`（与 Stage-B rank-8 及 `csi1000_production_config.py` 一致）。`candidate_id` 与 `model_sha256` 严格按计划，未动。请 Reviewer 裁决。

---

## 2. Modal 运行记录与 App ID
| 运行阶段 | 命令 | Modal App ID (`ap-...`) | 退出码 (`EXIT`) | 完成训练数 (`fits`) | 完成回放数 (`cells`) | 本地日志路径 |
|---|---|---|---:|---:|---:|---|
| Smoke | `modal run --profile infi ... --mode smoke` | `ap-gawjhpnpIRMKKirfwnDK5T`（失败前序：`ap-4SJHJFGQ4ZunvN38V8WpmW` crash-loop 无产物、`ap-ZBTI7MU3sYY1reQGioNueG` 2 fit 成功但 marker KeyError） | `0` | `2/2` | `0/0` | `quant/local_backup/07_csi300_csi500_stage0_p0/stage0_smoke.log` |
| Complete | `modal run --profile infi ... --mode complete` | `ap-fpahApjk0X3VwJuRLzxMso` | `0` | `176/176` | `16/16` | `quant/local_backup/07_csi300_csi500_stage0_p0/stage0_complete.log` |

> Complete 备注：4 波 fit（43+44+43+44 new，2 个 smoke 已复用）+ 1 波 cell（16 new）+ export，`STAGE0_P0_COMPLETE 176/176 fits 16/16 cells PASS`，`EXIT=0`。日志中有 12 条 `ForkPoolWorker … TypeError: LocalDatasetProvider.dataset() got multiple values for argument 'inst_processors'` stderr（qlib 数据加载池噪声）+ 1 次 `Worker disappeared` 重调度；零 `STOP_`，全部 176/176 receipt 与 16/16 cell 经 SHA 门控复核后才打印 COMPLETE，故判定为良性噪声，不影响字节级产物证据。

> Smoke 备注：inventory 全部门控静默通过（provider 指纹/快照、6482 天日历、`csi300=338`/`csi500=643` 报价并集 SHA、`SH000300`/`SH000905` 各 424 天基准收益）；2 个 `baseline/phase0` 首切片真实 LightGBM 训练完成，落盘 receipt SHA 校验通过，`STAGE0_SMOKE_PASS 2/2 fits verified`，`EXIT=0`。

---

## 3. 静态报价并集与数据源校验结果
- `provider_fingerprint`: `cd2e68f571c6bd19fbb2b79089e891bb1c43b96ab1fcdb4d2b2a9b1fdd9ce5d5`（与计划一致，`inventory_remote` 门控通过；`stage0_p0_summary.json` 已绑定）
- `snapshot_token`: `51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`（门控通过）
- 交易日历：`6482` 天，末尾 `2026-09-30`（门控通过）；执行窗口 `2025-01-02..2026-09-30` 共 `424` 天（`D.calendar` 门控通过）
- `csi300` 静态报价股票数 / `quote_sha256`：`338` / `9f3756ea8d2e0ff0bb12c3dce6d81888fe7a5df978a5f5669a4ce07b1a043f5b`（门控通过；`summary.quote_sha256_by_market` 一致）
- `csi500` 静态报价股票数 / `quote_sha256`：`643` / `cfcbc21efaba156173ff3c0aa09afc4f1fda5e94d9046f32bf37aa8b32f5e4fb`（门控通过；同上）
- 基准覆盖：`SH000300` / `SH000905` 在 424 个交易日均有非空正收盘价（门控通过）

---

## 4. 沪深 300（`csi300`，基准 `SH000300`）双相位基线回放结果（16 格之 8）

| 模型 (`model_key`) | 组合 (`topk/dropn`) | 相位 (`phase`) | 累计净收益 (`total_return`) | 年化净收益 (`strategy_cagr`) | 基准年化 (`benchmark_cagr`) | 相对超额年化 (`relative_excess_cagr`) | 夏普比 (`sharpe`) | 信息比率 (`ir`) | 最大回撤 (`max_drawdown`) | 日均换手 (`mean_turnover`) | +10bp 净总收益 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | `Top20/Drop2` | 0 | 0.191229 | 0.105714 | 0.060349 | 0.042783 | 0.617836 | 0.308530 | -0.172482 | 0.202936 | 0.095882 |
| `baseline` | `Top20/Drop2` | 10 | 0.331495 | 0.178707 | 0.060349 | 0.111621 | 0.964499 | 0.725772 | -0.135392 | 0.202933 | 0.234646 |
| `baseline` | `Top10/Drop1` | 0 | 0.504838 | 0.264530 | 0.060349 | 0.192559 | 1.168805 | 1.040192 | -0.167125 | 0.200697 | 0.400127 |
| `baseline` | `Top10/Drop1` | 10 | 0.334942 | 0.180458 | 0.060349 | 0.113273 | 0.857860 | 0.648159 | -0.156258 | 0.202718 | 0.235565 |
| `c98856b4` | `Top20/Drop2` | 0 | 0.267290 | 0.145723 | 0.060349 | 0.080515 | 0.788197 | 0.592566 | -0.100951 | 0.202714 | 0.169713 |
| `c98856b4` | `Top20/Drop2` | 10 | 0.282122 | 0.153405 | 0.060349 | 0.087759 | 0.805206 | 0.647971 | -0.113675 | 0.202046 | 0.185152 |
| `c98856b4` | `Top10/Drop1` | 0 | 0.285051 | 0.154917 | 0.060349 | 0.089186 | 0.725452 | 0.565893 | -0.147874 | 0.202100 | 0.186736 |
| `c98856b4` | `Top10/Drop1` | 10 | 0.489079 | 0.256908 | 0.060349 | 0.185371 | 1.110233 | 1.056500 | -0.135962 | 0.201428 | 0.384705 |

> 数据源：`stage0_p0_scoreboard.csv`（6 位小数 round 显示；全精度见 CSV 与 `stage0_p0_summary.json`）。16 个 cell 的 `n_days` 均为 424，`account_return_max_error` 均为 `1.1e-16`。

---

## 5. 中证 500（`csi500`，基准 `SH000905`）双相位基线回放结果（16 格之 8）

| 模型 (`model_key`) | 组合 (`topk/dropn`) | 相位 (`phase`) | 累计净收益 (`total_return`) | 年化净收益 (`strategy_cagr`) | 基准年化 (`benchmark_cagr`) | 相对超额年化 (`relative_excess_cagr`) | 夏普比 (`sharpe`) | 信息比率 (`ir`) | 最大回撤 (`max_drawdown`) | 日均换手 (`mean_turnover`) | +10bp 净总收益 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline` | `Top20/Drop2` | 0 | 0.404518 | 0.215408 | 0.161871 | 0.046078 | 1.134942 | 0.216330 | -0.202611 | 0.202314 | 0.299734 |
| `baseline` | `Top20/Drop2` | 10 | 0.540392 | 0.281601 | 0.161871 | 0.103050 | 1.366160 | 0.557312 | -0.185932 | 0.202768 | 0.428618 |
| `baseline` | `Top10/Drop1` | 0 | 0.204615 | 0.112832 | 0.161871 | -0.042207 | 0.613841 | -0.281870 | -0.252493 | 0.201151 | 0.101043 |
| `baseline` | `Top10/Drop1` | 10 | 0.222464 | 0.122272 | 0.161871 | -0.034082 | 0.644220 | -0.223197 | -0.277091 | 0.204822 | 0.119684 |
| `c98856b4` | `Top20/Drop2` | 0 | 0.376702 | 0.201526 | 0.161871 | 0.034130 | 1.079045 | 0.150775 | -0.170750 | 0.200690 | 0.271562 |
| `c98856b4` | `Top20/Drop2` | 10 | 0.491757 | 0.258205 | 0.161871 | 0.082913 | 1.277679 | 0.457161 | -0.182116 | 0.202573 | 0.384104 |
| `c98856b4` | `Top10/Drop1` | 0 | 0.150747 | 0.083975 | 0.161871 | -0.067043 | 0.489476 | -0.458179 | -0.201339 | 0.201670 | 0.049579 |
| `c98856b4` | `Top10/Drop1` | 10 | 0.324283 | 0.175036 | 0.161871 | 0.011331 | 0.876047 | 0.026205 | -0.232877 | 0.193189 | 0.222459 |

> 数据源同上。注意：`csi500` 上 `Top10/Drop1` 在 phase0 双模型均为负超额（baseline -0.0422 / c98856b4 -0.0670），属基线回放事实陈述，不做生产推论。

---

## 6. 本地冷备与产物完整性清单
- 冷备目录：`/home/ss/git_repos/qlib/quant/local_backup/07_csi300_csi500_stage0_p0/`
  - `volume_snapshot/csi300_csi500_research/stage0_p0/stage0_p0_20261015_a1/93bab198d812/`：`stage0_p0_summary.json` + `smoke_pass.json` + `fits/`（176×3）+ `cells/`（16×4），共 594 文件，约 71MB
  - `stage0_smoke.log` / `stage0_complete.log` / `stage0_complete_rerun.log`（`ap-gawjhpnpIRMKKirfwnDK5T` / `ap-fpahApjk0X3VwJuRLzxMso` / `ap-0vddP9JlMk0R8H2rVXMK3J`）
- `SHA256SUMS.txt` 校验状态：`598/598 OK`（`sha256sum -c SHA256SUMS.txt` 全绿，含 594 产物 + 4 日志/记录文件）
- 独立复算（`/tmp/opencode/stage0_p0_recheck.py`，已归档逻辑于报告本节）：16 个 `fixed_report.parquet` 均为 424 行（2025-01-02..2026-09-30）、`decisions` 均为 424、`report_metrics` 重算与 summary 误差 `0.00e+00`、`portfolio_performance` 复算最大误差 `5.0e-07`（其 6 位小数 round 所致，0 超差项 >1e-6）、账本 `max_account_return_delta/max_cost_rate_delta ≤ 1e-10`
- 异常或备注记录：
  1. `c98856b4` 参数字面值与 SHA 不符（见 §1），代码采用 SHA 权威的生产冻结参数，`candidate_id`/`model_sha256` 未动，请 Reviewer 裁决。
  2. `freeze_remote` 的 `stage0_p0_summary` marker 两次正常返回但在 Volume 上不可见（同函数的 `smoke_pass.json` 正常落盘 609B）。经核对 marker 字节与 `stage0_p0_summary.json` 在 `sort_keys+indent2` 下完全等价（marker = `{run_id, code_sha, **payload}`，而 summary 已含 `run_id/code_sha`），故零信息丢失；属开放 infra 疑问，不影响 176/176 + 16/16 证据链。
  3. 旧 SHA 前缀（`86bd6e13…`/`436535e6…`）在 Volume 上各残留 ≤2 个 fit receipt（失败/中断运行），与本次 `93bab198…` 前缀隔离，不计入 176。
  4. 数据入库 commit（`49000ef8`）仅含 summary/CSV，不改变实验 `code_sha=93bab198…`。
- **当前状态**：`READY_FOR_REVIEW`
