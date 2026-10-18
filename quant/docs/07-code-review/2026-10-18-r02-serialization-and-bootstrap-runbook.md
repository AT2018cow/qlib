# R02 运行手册：平台单执行、跨日哈希链与一次性 bootstrap 锚定

> 适用提交：`fix/quant-extension-review-20261018`（PR #83）第三轮返工（B06）
> 对象：值班/发布操作员。代码侧已把可自动化的部分全部 fail-closed；本文件只列必须由人工在授权环境完成的核验与一次性操作。

## 1. 跨运行互斥契约（平台单执行）

- `daily_cron` 的 Modal 装饰器必须保持 `max_containers=1`：调度器与 07:45 补跑转发的
  调用都在同一个函数队列中排队，任一时刻至多一个容器执行。
- 仓库内守护测试 `quant/tests/test_modal_structure.py::test_daily_cron_single_execution_contract`
  会在该值被移除时立即失败；同时禁止任何 Volume 文件锁/租约语义回潮。
- **投产前人工核对（源码断言 ≠ 平台实测）**：
  1. 在目标 workspace 的 Modal dashboard / `modal app list` 确认即将部署的 app 版本中
     `daily_cron` 的 `max_containers` 为 1，且没有 autoscaler/平台侧覆盖项。
  2. 确认没有其它 app、旧部署版本、人工脚本会调用 `daily_standalone` / `daily_cron`
     或直接写 `/vol/paper_portfolio/*.json`（旧部署必须先停止调度）。
  3. 确认 `daily_cron_second_chance` 仍只通过 `daily_cron.remote()` 转发，没有独立直写路径。
  4. 如需提高并发：先引入外部互斥服务（带 fencing token），并停止自动发布，不得仅改该数值。

## 2. 跨日账本连续性（B06）

每天 `daily_cron` 在**任何训练/发布/纸面交易之前**执行：

1. `journal.verify_chain_integrity()`：同一状态路径的 journal 必须逐条连续
   （首条 prior = 锚点或“账户从未存在”；后继 `prior_sha256` 必须等于前一条 post）。
2. `journal.check_paper_state_entry()`：三市场 Volume 现况必须是
   最后一个 VOLUME_COMMITTED 的 post 状态，或崩溃窗口内未完成后继的 post 状态；
   三者任一丢失/篡改/回退 → `BLOCKED_MANUAL_RECOVERY` ≠ 交易。
3. `journal.prepare()` 双重校验：声明的 prior 必须等于链期望值，否则拒绝创建当日 journal。

常见 `BLOCKED_MANUAL_RECOVERY` 含义：

| 信息片段 | 含义 | 处置 |
| --- | --- | --- |
| `unanchored existing paper state` | Volume 中存在 journal 时代之前的账户文件，尚未锚定 | 执行下方第 3 节一次性锚定 |
| `paper state differs from its attested bootstrap anchor` | 已锚定状态被改写 | 从备份/审计链恢复原字节，或走人工事故流程，禁止直接重新锚定 |
| `chain break` | 后继 journal 的 prior 与前一条 post 不连续 | 人工审计两日之间发生了什么；禁止删除 journal 记录“修复” |
| `committed paper state file is missing` | 已提交的状态文件丢失 | 人工事故流程；禁止用旧备份静默顶替（journal 链会拒绝） |
| `committed record after an incomplete one` | 存在未完成记录却被后续记录越过 | 说明有未按恢复流程的操作；人工审计 |
| `incomplete publication must be recovered before a new record` | 未完成记录未被恢复就尝试新发布 | 重跑 `daily_cron` 让恢复路径完成，或按日志处置 |

## 3. 一次性 bootstrap 锚定（迁移到 journal 发布流程前）

前置：停止旧调度（确保无进程写 `/vol/paper_portfolio/*.json`），人工审阅当前 Volume 状态
（与上一份已发布 `*_paper_portfolio*.json`、GitHub 工件、审计记录比对）。

```bash
modal run quant/code/production/modal_qlib_cn_a10g.py::attest_paper_state_bootstrap \
  --approve --note "<审阅人/时间/比对来源>"
```

- 输出并打印三个市场状态的 SHA256，抄录进发布记录（PR/交接文档）。
- 锚点写入 `/vol/publication_journal/bootstrap_anchors.json`；仅对**尚无 journal 历史**
  的路径生效；已存在历史时该命令拒绝覆盖。
- 未加 `--approve` 时函数拒绝执行；状态不存在时输出 `NO_STATE`（首日按新建账户处理）。

## 4. 明确不在本仓库自动验证的范围

- Modal 平台**实际生效**的 `max_containers` 与是否存在额外输入并发（第 1 节人工核对）。
- 真实账户/成交数据与独立行情源认证（属于生产审计流程，不在 CI）。
- 真实 provider 大包的哈希固定回归：CI 默认 skip；需显式执行
  `QLIB_PROVIDER_ARCHIVE=<tarball> python -m pytest -q quant/tests/test_provider_archive_real.py`
  并把输出归档为独立证据。
