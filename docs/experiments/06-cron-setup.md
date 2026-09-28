# 每日信号自动运行（Modal Cron 部署指南）

> 2026-09-19 起生效。`--best --daily` 已配置为云端定时任务：每个交易日次日早上自动执行并推送 GitHub，无需本地机器。

## 架构

```
Modal Cron（每个 A 股交易日 07:00 北京时间）
  → daily_standalone：下载 chenditc 最新数据（565MB，必然最新）
  → 训练终审候选（LGB + Alpha158 + 20日标签 + csi1000 + top20 + nd2）
  → 涨跌停过滤（当日涨幅口径）
  → GitHub API 直接写入 results/signals/（无需 git 客户端）
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
| **Modal 告警邮件可能延迟/重复** | 看似"再次失败"，实际日志无新失败记录 | 收到告警先 `modal app logs` 核对时间戳，再下结论 |
| **假日后首个交易日"数据滞后"** | 09-28（周一）榜单数据止于 09-24（周四）——中秋 09-25 休市，chart 与假日前那份逐字节相同 | **属预期**：数据止于最近交易日 = 正确的前一交易日窗口；模型 20 日缓存未到期同样复用。勿当故障处理。gate 上线后（2026-09-28）假日不再产生榜单，历史残留的 09-25 假日条目已按新语义删除（内容留存于 09-28 与 git 历史 c1ac6396） |
