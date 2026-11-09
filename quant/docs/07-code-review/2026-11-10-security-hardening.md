# 安全加固记录（2026-11-10）

状态：`IMPLEMENTED / VERIFIED / ACCEPTED_RESIDUAL_RISK`

本文记录 2026-11-09–10 完成的凭证、CI/CD、网站和镜像加固，以及仍未关闭的残余风险。本文不包含任何 secret 值。

## 1. GitHub 生产 PAT

- 生产 `github-push` Modal Secret 已改为专用 fine-grained PAT：
  - 仅授权 `AT2018cow/qlib`；
  - Repository permissions：**Contents: Read and write**、**Metadata: Read-only**；
  - 不含 Administration、Actions、Secrets、Workflows 等写权限。
- 验证结果：
  - 仓库读取：`200`；
  - Contents 写入：临时分支 `qlib-token-probe-f850afbc` 创建 `201`、删除 `204`，已确认无残留；
  - Actions workflow dispatch：`403`，证明当前 Secret 不是旧的 Actions 写权限 token。
- 旧 workspace 的 `github-push` 仍保留，这是操作者明确决定，用于旧环境回滚；旧 App 必须保持 stopped，禁止重新启用 18:00/19:00/07:00 schedule，避免与生产双写。

## 2. Cloudflare Pages

- `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` 只注入 `website-deploy.yml` 的 `Deploy mirror to Cloudflare Pages` step；
- build/test 步骤只能看到布尔值 `CLOUDFLARE_ENABLED`，不再持有 Cloudflare token；
- token 权限由操作者确认为 **Account → Cloudflare Pages → Edit**，仅限对应账户；
- 验证：手动部署 run `42644661500` 与网站安全头部署 run `42652948704` 均成功，Cloudflare step 实际执行；
- GitHub Pages 暂停期间，`pages: write` / `id-token: write` 只授予 `deploy` job；build job 只有 `contents: read`。若未来重新启用 GitHub Pages，build job 的 Pages 步骤可能需要补充 `pages: write`（workflow 内已留注释）。

## 3. 仓库与 Modal 镜像

### `.gitignore`

新增：

```text
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
.netrc
.npmrc
.pypirc
secrets/
credentials*
node_modules/
```

### Modal 镜像 `add_local_dir` ignore

新增 `.env`、`.pem`、`.key`、`.p12`、`.pfx`、`.netrc`、`.npmrc`、`.pypirc`、`secrets`、`credentials`、`.ssh`、`.docker`、`node_modules`；原有 `.aws` 保留。

当前仓库不包含上述文件；该改动是防止本地 gitignored 文件被意外打进镜像。

## 4. 网站安全头

新增 `quant/website/_headers`，部署时复制到 `_site` 并在上传前检查存在：

- `Content-Security-Policy`：仅允许 self、Cloudflare Insights beacon 和本站数据请求；
- `Strict-Transport-Security`；
- `X-Frame-Options: DENY` + `frame-ancestors 'none'`；
- `X-Content-Type-Options: nosniff`；
- `Referrer-Policy: strict-origin-when-cross-origin`；
- `Permissions-Policy` 禁用 geolocation/microphone/camera。

公网 `https://qliba158.pages.dev` 已实测返回上述 headers。网站无登录/Cookie/敏感操作，CSP 主要防御第三方脚本或未来数据注入。

## 5. CI/CD 安全现状

- 未发现 `pull_request_target`、`workflow_run`、`issue_comment` 等高风险触发器；
- 未发现 PR 可控表达式直接拼接进 `run`；
- 只有 `website-deploy` 使用 repository secrets；其他 workflow 均为 `contents: read`；
- GitHub secret scanning 与 push protection 已由操作者开启；
- GitHub Actions 日志、Modal 生产/旧生产日志、公网静态资源扫描未发现项目自有 token/私钥。

## 6. 验证

| 项目 | 结果 |
|---|---|
| Python 回归 | `935 passed, 1 skipped, 50 subtests` |
| 网站 Node 测试 | 15 passed |
| CI `Quant extension checks` | `42652949167` / `42652957058` success |
| 网站部署 | `42652948704` success |
| 生产 App | `qlib-prod-daily` / `qlib-prod-website` v8，commit `35f28a9` |
| 网站 headers | CSP/HSTS/X-Frame/Permissions-Policy 已生效 |

## 7. 残余风险与接受项

1. **Modal 工作区隔离**：生产与实验仍共用同一个 Modal workspace。App/Volume 命名区分不等于 IAM 隔离；有 deploy/invoke 权限的成员可以引用 `github-push` Secret、挂载 `qlib-prod-data`，或调用 `daily_cron_manual` / `backfill_signals` 等生产修复函数。若 workspace 只有操作者本人，风险可控；若有其他成员，建议迁到独立生产 workspace/环境并限制成员。
2. **分支保护与 2FA**：GitHub API 当前无法读取 `main` protection 和账号 2FA 状态（403/空）。生产 PAT 是 Contents write，能修改 `.github/workflows/**` 和 `quant/website/**`；若 PAT 或账号被攻破，可能修改部署脚本并读取 Cloudflare Pages token（仅 Pages:Edit）。建议确认 2FA，并给 workflow/website 路径加 ruleset/PR review。
3. **供应链**：Modal 镜像仍有未锁版本的 `torch`、`mlflow`、`akshare`、`baostock`、`cvxpy`、`jupyter` 等；Actions 使用 `@vN` tag 而非 commit SHA。建议固定版本/清理生产镜像依赖，并把生产 workflow 的 actions 固定到 SHA。
4. **公开仓库信息**：`quant/data` 日志和部分研究文档包含本地路径与 workspace 名称，不包含凭证；若需要降低公开信息暴露，可清理历史日志或移到私有存储。
5. **旧 Secret**：旧 workspace 的 `github-push` 仍有效但未使用；保留是操作者决定。只要旧 App 不重新启动，当前生产不受影响。
6. **GitHub Pages**：当前暂停；若重新启用，需按 workflow 注释给 build job 补 Pages 权限并重新验收。

## 8. 相关提交

- `1dbcc610d`：Cloudflare token step 隔离、`.env`/key ignore；
- `4e8f48a2e`：Cloudflare token 权限注释；
- `1834ce3df`：网站 `_headers`、Pages 权限收敛；
- `35f28a946`：Modal 镜像 `node_modules` ignore；
- 生产 App v8：`35f28a9`。
