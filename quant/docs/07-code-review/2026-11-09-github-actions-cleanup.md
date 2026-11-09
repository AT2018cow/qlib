# GitHub Actions 清理与自动关闭排查（2026-11-09）

状态：`CLEANED / MONITORING`

## 结论

- 本仓库是 `microsoft/qlib` 的 public fork，但 **没有运行 upstream qlib 的 test/lint/docs/release workflows**；本地 `.github/workflows` 只有 18 个自建 quant/CSI1000/website workflow。
- Actions 注册表里原有 **30 个 active workflow**，其中 16 个是已经从 `main` 删除的一次性/临时/verify workflow；它们仍留在 Actions 注册表中并显示 active。
- 已禁用这 16 个 stale workflow，active 降到 **18**。它们不影响当前生产。
- GitHub **不会仅因 workflow 文件数量多就自动关闭 Actions**。具体关闭原因当前 fine-grained token 无 admin 权限读取（`actions/permissions` 返回 403），需要在 GitHub `Settings → Actions → General`、Billing 或 GitHub 邮件中确认。
- 可能的触发因素：仓库级 Actions 设置、fork/组织策略、账单/额度保护、短时间高频运行触发的滥用保护。11 月前 8 天有 124 次 workflow run，确实偏高。

## 最近 200 次 run 的分布

| Workflow | 次数 |
|---|---:|
| Deploy signal website to GitHub Pages | 73 |
| Quant extension checks | 63 |
| CSI1000 MVP publication contract | 40 |
| Website visual review | 10 |
| 其他历史/审计 workflow | 14 |

事件分布：push 175、pull_request 14、workflow_dispatch 11。  
月份分布：2026-11 124、2026-10 76。

## 已禁用

16 个已从 main 删除的 stale workflow：

- `qlib-audit-apply`、`qlib-live-apply`
- `review-regression`
- `verify-execution-attribution`、`verify-phase-orchestration`、`verify-phase-reuse`
- `verify-portfolio-metrics`、`verify-pr8-repro`
- `verify-pre-tuner-audit`、`verify-pre-tuner-audit-extended`、`verify-pre-tuner-multipool`
- `verify-r24-r27-r28`、`verify-satellite-gate-screen`、`verify-zero-fit-phase-attribution`
- `wrapper-bugfix-check`、`verify-zero-fit-regime-age`

## 建议保留

- `website-deploy.yml`：公开网站发布，必需。
- `csi1000-tuner-core.yml`：主 quant 测试 CI，必需。
- `csi1000-mvp.yml`：生产合同测试；与 tuner 有重叠，但保留为快速专项检查。
- `website-visual-review.yml`、`unified-paper-nav.yml`、`paper-nav-independent-audit.yml`：仅 PR 触发，平时不消耗。

## 可选第二波

如果进一步减少历史噪音，可禁用：

`csi1000-stage-a-corrected-replay`、`csi1000-stage-b-*`、`csi1000-f1-quote-fix`、`csi1000-forward-shadow-pair-preflight`、`csi1000-l2-fastlane`、`csi1000-l3-offline`、`csi1000-l4-offline`。

这些是已完成研究阶段的审计/回放 workflow；禁用不影响生产。是否禁用需单独确认。

## 备注

清理是仓库 Actions 设置层的禁用，不修改代码、不删除历史记录；可随时重新启用。
