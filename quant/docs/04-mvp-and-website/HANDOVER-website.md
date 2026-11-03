# 网站交接文档（当前状态）

> 2026-11-03 更新：网站已从 CSI1000-only 切换为 **CSI300 / CSI500 / CSI1000 三市场**；每市场独立 100 万账户，分市场 TopK/DropN。
> 旧的 `23-web-ui-handoff-20261008.md` 只作历史背景；当前以本文件、`AGENTS.md` 和 `quant/website/methodology.html` 为准。

## 当前公开页面

- 同时展示沪深300、中证500、中证1000 三个股票池，默认沪深300；
- 每个市场独立榜单、独立 1M 模拟账户、独立累计收益；
- 当前生产规则：CSI300 10d Top10/Drop1、CSI500 20d Top20/Drop2、CSI1000 20d Top10/Drop1；
- 账户口径：2026-09-30 起算，2026-10-08–2026-11-02 为 point-in-time 回补，2026-11-03 起真实前向 paper；
- ChiNext 日更与公开入口继续暂停；历史 ChiNext artifacts 保留只读；
- `quant/website/index.html` + `app.js` + `styles.css` 为纯静态前端；
- `quant/website/methodology.html` 展示当前 1M 全周期表、2025–2026 扩展观察、执行规则与风险边界；
- 深色响应式布局：桌面表格，窄屏移动卡片。

## 数据与发布链路

- 公开信号：`quant/data/signals/`（CSV、chart、paper、metadata、forward/backfill JSON、日期索引）；
- 旧 100M 数据：`quant/data/signals/archive/legacy_100m_2026-10/`，不进入公开索引；
- Pages workflow：`.github/workflows/website-deploy.yml`，在 `quant/data/signals/**` 或 `quant/website/**` 变化时构建；
- 构建必须把 HTML、JS、CSS、图标和 signals 一起复制到 `_site`，并在上传前检查关键文件存在；
- forward performance 由回补前缀 + live ledger 合并生成，不能再退回“等待首个估值”。

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
6. Pages 打包与手机/桌面响应式测试通过。
