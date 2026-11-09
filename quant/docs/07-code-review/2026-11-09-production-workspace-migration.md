# 生产工作区迁移与首轮 T-close 发布（2026-11-09）

状态：`CUTOVER_VERIFIED / MONITORING`

## 1. 命名与切换

| 类型 | 旧生产 | 新生产 |
|---|---|---|
| 主 App | `qlib-cn-daily-a10g` | `qlib-prod-daily` |
| 网站 App | `qlib-website-nav` | `qlib-prod-website` |
| 数据卷 | `qlib-cn-data` | `qlib-prod-data` |
| 网站模拟卷 | `qlib-website-simulation` | `qlib-prod-website-simulation` |

新生产使用 `QLIB_DEPLOY_PROFILE=infi-prod` 选择上述名称；默认值仍指向旧名称，便于回滚。旧生产 App 已于 2026-11-09 17:38 +08 停止，避免 18:00 双写。

## 2. 迁移内容

- 迁移包：`_migration_state.tar.gz`，443,339,508 bytes，SHA256 `59e062a2abd0460f47db2b840f97df02d519a7263b19081aa34361075b63352b`。
- 已迁移：`cn_data_overlay/2026-11-06`、`paper_portfolio`、`publication_journal`（含 11/09）、`live_models`、`signals`。
- 旧生产卷上的临时迁移包已删除。
- 基础 `cn_data` 只完成了部分复制；已移除 `cn_data/.chenditc` marker，下一次 `_ensure_data` 会强制重新下载完整基础数据。日常信号链路使用 overlay 快照，不依赖该基础目录。

## 3. 18:00 / 19:00 首轮发布

18:00 首轮失败于三个问题，均已修复并重新部署：

| 问题 | 修复 |
|---|---|
| 容器入口在 `/root`，`data_ingest` 不在 `sys.path` | 入口显式加入 `/root/qlib/quant/code` 并调用 `quant_bootstrap`（`32fdaa3`） |
| CSIndex OSS 偶发 60s 读超时 | 3 次重试 + 120s 读超时（`2d6767f`） |
| CSIndex 当日成分股文件尚未发布 | 仅当滞后 ≤3 天时复用 base PIT 成分股，仍 fail-closed（`f261706`） |
| 18:00/19:00 未调用网站 NAV 阶段 | `_run_evening_cycle` 改为 `run_daily_pipeline(signal, website)`（`9c42769`） |

19:00 重试成功：

- overlay `2026-11-09`：`PASS`，1802 rows，base PIT `2026-11-06`；
- 2026-11-10 三池 canonical 信号：12 个文件，GitHub commit `f9ce60049`；
- journal `2026-11-10`：`VOLUME_COMMITTED`；
- 2026-11-10 metadata：`CANONICAL_T1`，`lag_sessions=0`；
- paper state：pending `signal_data_date=2026-11-09` -> `execution_date=2026-11-10`。

## 4. 网站 NAV

冷启动回放临时把日预算上限从 $2 提高到 $10，回放完成后已恢复 $2。网站阶段结果：

- 状态：`PUBLISHED`；
- GitHub commit：`9992cec39`；
- 12 个文件，receipt `status=PUBLISHED`；
- source SHA256 `a8870427ee717891152fd21186dc718c9e43e677b604e45b9eced935ddc7d951`；
- asset digest `sha256:fda0921e96b557fbafb51b648a60416d1be959dd6cc062a69a647833d78e0164`。

公网 `https://qliba158.pages.dev` 验证：

- `available_dates` 已包含 `2026-11-09` 与 `2026-11-10`；
- CSI1000 / CSI500 / CSI300 的 `latest_date` 均为 `2026-11-09`；
- 三市场 NAV 分别为 `0.80324595`、`0.89409338`、`0.76021433`；
- 2026-11-10 信号 metadata 与 CSV 可访问。

## 5. 调度

| 时间（Asia/Shanghai） | 函数 | 说明 |
|---|---|---|
| 18:00 周一至周五 | `daily_evening` | 构建 T overlay、发布 T+1 信号、T 日 NAV |
| 19:00 周一至周五 | `daily_evening_retry` | 幂等重试 |
| 07:00 周一至周五 | `daily_cron` | 盘前轻量检查/补跑 |

08:00 全量备份已移除。18:00 与 19:00 已实际触发并完成；07:00 由 `test_cron_gate.py` 守护。

## 6. 已知问题

- `Quant extension checks` 自 2026-11-08 起失败：`pip install -e .` 把 NumPy 升级到 2.4.6，与按 1.26 编译的 Cython 扩展不兼容。已在该工作流中追加 `numpy==1.26.4` 约束。
- `cn_data` 基础数据需要在下一次研究/训练任务前重新下载；日常信号与网站不依赖它。
- 18:00 首轮的数据源 ST 快照遇到东方财富限流，按 `TRANSPORT` 语义降级为 reference-only，未阻塞信号发布。
- 名称映射更新遇到深交所接口超时，不影响信号发布。

## 7. 回滚

1. 停止 `qlib-prod-daily` 与 `qlib-prod-website`；
2. 取消 `QLIB_DEPLOY_PROFILE=infi-prod`（或设为默认值）后重新部署旧名称；
3. 确认旧 App 恢复 schedule 后，再重新启用新生产；
4. 严禁两个工作区同时运行 18:00/19:00/07:00 调度。
