# 每日信号自动运行（Modal Cron 部署指南）

> 2026-09-19 起生效；2026-10-02 扩展为双池信号发布。该 cron 是 paper/research 信号基础设施，不代表所有发布池都已通过当前投资研究审计。当前池状态见 `14-pre-tuner-stage-summary-20261007.md`。

## 架构

```
Modal Cron（每个 A 股交易日 07:00 北京时间）
  → daily_standalone：下载 chenditc 最新数据
  → csi1000（top20/nd2）与 chinext（top20/nd3）分别训练/复用缓存并生成 ranking-only 信号
  → 板块/日期感知涨跌停过滤
  → GitHub API 单次 commit 写入两池 CSV + chart
  → 网站展示 / paper-trading 留痕

注意：当前 corrected research audit 只把 CSI1000 视为 primary validated baseline；
ChiNext 继续发布仅表示基础设施保留，不表示其 frozen baseline 已获生产资金批准。
```

## 一次性部署步骤

```bash
# ① 生成 GitHub PAT（Fine-grained，只给 fork 的 Contents 读写权限，其余全不选）
#    https://github.com/settings/personal-access-tokens/new
#    Repository access: Only select repositories → <你的fork>/qlib
#    Permissions: Contents → Read and write

# ② 创建 Modal Secret（token 绑定 workspace，换 workspace 需重建）
modal secret create github-push GITHUB_TOKEN=<粘贴PAT>

# ③ 部署（部署后 cron 持久生效，改代码后需重新 deploy）
modal deploy modal_qlib_cn_a10g.py
```

## 费用与生命周期

| 项 | 说明 |
|---|---|
| 每次运行成本 | ~$0.2（容器只在触发的那 ~25 分钟计费；deployed 待命状态免费） |
| 节假日/周末 | 周末不在 cron；工作日触发但经 akshare 交易日历 gate 判定为假日 → 不发布榜单（日历拉取失败 fail-open 照常发布） |
| 数据新鲜度 | 交易日口径 lag（数据日→今天之间的交易日数）：0=正常，1=数据源漏发一轮仍发布（警告），≥2=疑似数据源故障 → raise 触发 Modal 告警。日历不可用时 fail-open 降级（>12 自然日兜底）。旧 ">4 自然日跳过" 规则已移除——它会在长假后复市日（如 10-08 数据止于 09-30，差 8 自然日）错杀正常发布 |
| 暂停 | `modal app stop <app名>` |
| 删除 | Modal dashboard 或 CLI（`modal app stop` 为永久停止） |
| 更新代码后生效 | 重新 `modal deploy modal_qlib_cn_a10g.py` |
| PAT 过期 | 覆盖创建：`modal secret create github-push GITHUB_TOKEN=<新PAT>` |

## 验证方法

```bash
# 手动触发一次（不影响 cron 计划，同日重复自动去重）
modal run modal_qlib_cn_a10g.py::daily_cron

# 查看运行日志
modal app logs <app名>

# 确认 GitHub 上自动出现当日文件
# results/signals/<日期>_top20_lgb158.csv
# results/signals/<日期>_top20_lgb158_chinext.csv
# results/signals/<日期>_chart.json
# results/signals/<日期>_chart_chinext.json
```

## 多余依赖说明（已知且接受）

镜像为全功能共享（含 torch/CUDA 库 ~2GB，为 GPU 路径预留）。Modal 仅对运行容器计费、镜像存储免费，对 cron 费用无影响，故不做拆分。如需精简可拆 cron 专用 slim 镜像（无 torch），当前收益为零。

## 陷阱备忘（实战踩坑记录）

| 陷阱 | 后果 | 规则 |
|---|---|---|
| **新增 helper .py 模块** | 容器内 `import` ModuleNotFoundError——函数运行在 `/root/`，仓库在镜像的 `/root/qlib/` 下 | 必须加进镜像 cp 步骤（`modal_qlib_cn_a10g.py` 中 `.run_commands("cp ... /root/")`），当前已有 `qlib_audit_fixes.py` / `qlib_live_retrain.py` / `github_commit.py` |
| **Secret 环境变量名** | `os.environ` KeyError → 当天信号丢失 | 只有 `GITHUB_TOKEN` 一个变量（见 Secret 定义） |
| **多次 Contents API 逐个推文件** | 多个 commit 几乎同时触发 Actions → concurrency `cancel-in-progress` 取消后到的 run → 部署产物缺文件（09-22 迷你走势 404 事故） | 多文件必须走 `github_commit.push_files`（Git Data API 单 commit 原子推送） |
| **chenditc 发布时间** | 晚间 cron 拿到旧包 → 严格日期检查失败（09-21 20:30 事故） | cron 必须在次日早上（07:00）跑，等包发布后再取 |
| **装饰器编辑意外吞掉 schedule 行** | 09-29 静默无调度：gate 重构编辑在 `@app.function` 前插入辅助函数，oldString 含 `schedule=modal.Cron(...)` 行而 newString 未带 → 调度被删，后续两次 deploy 均为无调度版本；py_compile 无法发现 | 回归守护测试 `tests/test_cron_gate.py::test_cron_schedule_registered`；触碰装饰器的编辑后必跑测试。注意："no changes detected" 的跳过式 deploy **不会**修复调度缺失（内容哈希比对，不做核对） |
| **Modal 告警邮件可能延迟/重复** | 看似"再次失败"，实际日志无新失败记录 | 收到告警先 `modal app logs` 核对时间戳，再下结论 |
| **假日后首个交易日"数据滞后"** | 09-28（周一）榜单数据止于 09-24（周四）——中秋 09-25 休市，chart 与假日前那份逐字节相同 | **属预期**：数据止于最近交易日 = 正确的前一交易日窗口；模型 20 日缓存未到期同样复用。勿当故障处理。gate 上线后（2026-09-28）假日不再产生榜单，历史残留的 09-25 假日条目已按新语义删除（内容留存于 09-28 与 git 历史 c1ac6396） |
