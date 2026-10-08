# 网站交接文档（当前状态）

> 2026-10-08：旧的双池网站交接已被当前 CSI1000-only 页面取代。
> 下一阶段总体交接见 [21-next-conversation-handoff-star-chinext-20261008.md](21-next-conversation-handoff-star-chinext-20261008.md)。

## 当前公开页面

- 只展示 CSI1000 每日榜单。
- ChiNext 旧模型的每日更新和公开 tab 已暂停。
- 历史 signal artifacts 保留为只读记录。
- `website/index.html` + `website/app.js` + `website/styles.css` 为纯静态前端。
- `website/methodology.html` 只展示当前生产方法、Stage-B 证据和风险边界；不展示私有环境/凭据信息或不必要的内部运行标识。
- 深色响应式布局：桌面显示表格，窄屏显示移动卡片。

## Pages 发布链路

`.github/workflows/website-deploy.yml` 在 `website/**` 或 `results/signals/**` 变化时构建静态站点。
构建必须把 HTML、JavaScript 和 CSS 一起复制到 Pages artifact，并在上传前检查关键文件存在。
PR #31 修复了曾经遗漏 `styles.css`、导致线上退化为浏览器默认白底页面的问题。

## 不应回退的行为

- 不恢复旧 `?pool=chinext` 公共入口，除非 ChiNext 新模型已经通过明确的 production gate。
- 不把旧 ChiNext 历史榜单自动解释为当前有效信号。
- 不把内部审计 hash、运行环境名或部署凭据展示在公共方法论页面。
- 不删除历史 artifacts；当前页面只控制展示范围。

## 后续启用新 pool 时

新增 STAR 或 ChiNext 公开展示必须和其 production activation 同一个版本完成：

1. 新模型验证合同先冻结；
2. production artifact 命名/lineage 明确；
3. cron publication gate 通过；
4. 网页 allowlist 才增加新 pool；
5. 方法论页同步说明证据与风险；
6. Pages 打包与手机/桌面响应式测试通过。
