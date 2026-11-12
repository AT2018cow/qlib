# 网站交接文档（当前状态）

> 2026-11-12 更新：网站支持 **CSI300 / CSI500 / CSI1000 三市场**；每市场独立 100 万模拟账户，分市场 TopK/DropN。
> 已部署的两个生产 App 均为 v26：T 日 18:00 主跑，用 T 数据更新 T NAV、到期重训并发布下一交易日 U 榜单；23:00 只读预警、23:15 同一入口幂等恢复、U 日 07:00 最终只读核验。部署证据和未完成的自然调度验收见[生产状态记录](../07-code-review/2026-11-12-production-deployment-status.md)。公网为 Cloudflare Pages，GitHub Pages 暂停。
> 旧的 `23-web-ui-handoff-20261008.md` 只作历史背景；当前以本文件、`AGENTS.md` 和 `quant/website/methodology.html` 为准。

## 当前公开页面

- 同时展示沪深300、中证500、中证1000 三个股票池，默认沪深300；
- 每个市场独立榜单、独立 1M 模拟账户、独立累计收益；
- 榜单“N日模型评分”是模型原始回归输出，仅用于横截面排序；UI 不显示百分比，避免被误读为预期涨幅；
- 公开榜单合同 v2：CSV 等于完整模型排名前 TopK（CSI300/1000 前 10、CSI500 前 20），不因 T 日收盘涨跌停删榜或补位；涨跌停仅作信息提示，U 日开盘能否成交由执行规则判定；历史 v1 文件不重写；
- 当前生产规则：CSI300 10d Top10/Drop1、CSI500 20d Top20/Drop2、CSI1000 20d Top10/Drop1；
- 账户口径：网站收益为自 2026-10-08 开盘开始的独立因果重建，首日使用 2026-09-30 收盘评分；原生产 paper 账本的 2026-10-08–2026-11-02 是 point-in-time 回补、2026-11-03 起为前向 paper。两条账户链分开保存，均不是券商成交认证；
- 网站生产 `/data` 全部只读挂载；`/simulation` 可写。Provider archive 只物化到本地 scratch，不写回生产数据 Volume；
- ChiNext 日更与公开入口继续暂停；历史 ChiNext artifacts 保留只读；
- `quant/website/index.html` + `app.js` + `styles.css` 为纯静态前端；
- `quant/website/methodology.html` 展示当前 1M 全周期表、2025–2026 扩展观察、执行规则与风险边界；
- 深色响应式布局：桌面表格，窄屏移动卡片。

## 数据与发布链路

- 公开信号：`quant/data/signals/`（CSV、chart、paper、metadata、forward/backfill JSON、日期索引）；
- 旧 100M 数据：`quant/data/signals/archive/legacy_100m_2026-10/`，不进入公开索引；
- Cloudflare Pages workflow：`.github/workflows/website-deploy.yml`，在 `quant/data/signals/**` 或 `quant/website/**` 变化时构建并部署；GitHub Pages 暂停；
- 构建必须把 HTML、JS、CSS、图标、`_headers` 和 signals 一起复制到 `_site`，并在上传前检查关键文件存在；
- 网站安全头（CSP/HSTS/X-Frame/Permissions-Policy）由 `quant/website/_headers` 提供；详见 `quant/docs/07-code-review/2026-11-10-security-hardening.md`；
- 当前 schema v3 的 website performance 是独立连续账户；构建脚本验证后直接用于 forward/backfill 派生文件，不再把它拼接到旧 scheduled paper ledger。旧 schema 的合并路径只保留兼容用途。

## 不应回退的行为

- 不恢复 CSI1000-only 页面或旧 `?pool=chinext` 公共入口；
- 不把旧 100M 账户或旧 profile 数据重新拼接到当前 1M 曲线；
- 不把内部审计 hash、运行环境名或部署凭据展示在公共方法论页面；
- 不删除历史 artifacts；当前页面只控制展示范围；
- 不把 2025–2026 扩展观察写成选型依据；它只用于策略锁定后的近期表现说明。

## 后续启用新 pool 时

新增 STAR 或 ChiNext 公开展示必须和其 production activation 同一个版本完成：

1. 新模型验证合同先冻结；
2. production artifact 命名/lineage 明确；
3. cron publication gate 通过；
4. 网页 allowlist 才增加新 pool；
5. 方法论页同步说明证据与风险；
6. Cloudflare Pages 打包与手机/桌面响应式测试通过。
