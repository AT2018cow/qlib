# 下一版本发布候选说明

状态：`DEPLOYED / COMPLETE`。代码 tag `quant-v1.3.0`、主应用 v31、网站应用 v6；本文件保留为发布候选与部署范围的记录。

已发布版本 `quant-v1.3.0`。从上一个 `quant-v1.2.0` 到当前待合并修复，包含新增的 10/08 连续网站模拟账户、统一调度、板块执行纠错与本轮发布/审计加固，不能将整个累计发布范围描述为纯 patch。本轮保持三市场的候选模型、TopK/DropN、标签期限和 1M 本金不变；此前累计范围中的执行规则修正仍须在正式说明中列明。

## 已具备的可审核内容

- 基础 main 提交：`33bef4b646612c5b6fe86587aaf49c1b4f641508`。本轮代码提交 `5ca4b892c9bd1b8656a6d3aac3f03db886ffd66e`；代码 release tag `quant-v1.3.0` 指向 `7184772f1093b39a5b2b3d9c59e39ae6d117f585`。
- 修复范围及 20 项结论：[逐项复核](2026-11-06-post-rollout-review-resolution.md)。
- 全仓 Python Black 25.1.0/120 列通过（534 files；ipynb 跳过）；canonical live 审计从 11/03 起算、0 issue。
- 本地验证：[validation.json](../../data/post_rollout_review/20261106/validation.json)。922 项 Python、50 子用例、30 项前端通过；519 份历史报告算术审计与 60 份独立网站账本对账通过。
- 源码身份：[source-manifest.json](../../data/post_rollout_review/20261106/source-manifest.json)，SHA 为 `1aa5400c7a670738b504ce09fa5437fa4ff343110eab4b3a008052fb67b8f00a`。
- 现有网站账户的累计收益未因本轮复核变化。独立账本验证保留 `PARTIAL_NOT_CERTIFIED`，历史 ST、官方除权参考价与开盘流动性不做认证声明。

## 正式发版尚缺的事实

1. 本轮代码已提交、发布 tag，并完成固定源码 cloud smoke/full 与实际部署回执。
2. 绑定该固定源码的新 cloud smoke/full 及输入、输出、实际退出码；在已授权研究环境、同一 workspace 的数据卷中执行，保持前台连接，先 smoke 后 full，不使用 Modal 的 detached 模式。
3. 部署提交标记、两应用相同源码清单、实际部署版本和 07:00/08:00 调度配置的核验。
4. 账户状态前后哈希及主跑/补跑真实回执；既有 override 恢复应明确为非正常完成，同时保留网站阶段结果。新鲜路径的检查不能靠捏造未来数据。
5. 之后才在符合政策的提交上创建 annotated tag 与对应 GitHub Release，注释包含提交 SHA、实际部署版本、范围和证据链接。

[版本策略](2026-10-19-quant-release-versioning-policy.md)要求 tag 打在“已合并 main 且完成独立复审”的提交上，并与部署提交标记一致；同时明确“发布与部署分离授权”。因此现在不把未经部署验收的新源码或旧的已知缺陷源码包装成已完成的新版本，也不移动既有历史 tag。
