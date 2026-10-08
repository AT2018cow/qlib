# 每日信号自动运行（当前生产说明）

> 2026-10-08 更新：旧的双池 cron 说明已失效。当前 scheduled production 只发布 CSI1000 Stage-B winner；
> ChiNext 日更与网页展示已暂停。部署凭据与私有 workspace 信息不写入公开仓库。

## 当前架构

```text
工作日 07:00（北京时间）
  → 获取交易日历并验证当天是否为交易日
  → 下载最新日频数据
  → 运行 CSI1000 Stage-B winner
  → 要求数据日与 signal date 之间交易日 lag = 0
  → 生成 Top20 ranking / chart / paper artifact
  → 同日 artifact 若已存在且内容不同则拒绝覆盖
  → 推送仓库并触发静态网站更新
```

ChiNext 不在当前 scheduled cron 中。其研究代码与历史 artifacts 保留，未来只有在新模型完成独立验证后才重新启用。

## 生产协议

- 模型：冻结的 CSI1000 Stage-B winner。
- 组合：Top20 / Drop2。
- 信号：T 日收盘形成，供 T+1 开盘使用。
- 重训：固定 20-session 时钟，原点 2026-09-18。
- freshness：必须确认前一真实交易日数据可用；交易日历不可验证或数据滞后时 fail closed。
- paper：Stage-B winner 使用新的独立状态，不继承旧 baseline 的账户或 pending orders。
- 历史记录：已有同日 artifact 不做事后重写。

## 部署

生产代码更新后，在已授权的生产环境中执行：

```bash
git checkout main
git pull --ff-only
modal deploy modal_qlib_cn_a10g.py
```

访问凭据应通过托管平台的凭据管理功能在仓库外配置。公开文档不记录私有 workspace/profile、凭据对象名称、账户余额或访问值。

## 部署后验证

1. 确认 scheduled app 已加载最新 `main`。
2. 确认 cron schedule 仍为工作日 07:00 Asia/Shanghai。
3. 首次有效发布应满足 `data_date = 前一真实交易日`。
4. 检查当日只新增 CSI1000 canonical 文件，不应新增 `_chinext` daily artifacts。
5. 检查 paper artifact 属于当前 winner 版本且未继承旧 baseline 状态。
6. GitHub Pages 部署成功后，确认桌面只显示表格、手机只显示移动卡片，CSS 正常加载。

## 数据与 Volume

- daily production 每次使用最新下载的数据，不要求为了 cron 手工刷新旧研究数据目录。
- 历史模型缓存可以保留；缓存复用受 config/runtime/data-lineage 校验保护。
- 旧 baseline paper state 可以保留用于审计，但当前 winner 使用独立状态。
- 不要为了模型切换清空整个持久化 Volume。

## 失败语义

- 非交易日：正常跳过。
- 日历不可验证：CSI1000 不发布。
- 数据不是前一真实交易日：CSI1000 不发布。
- cache/config lineage 不匹配：拒绝不安全复用。
- 同日 artifact 内容变化：拒绝覆盖。

这份文档只描述当前生产行为；历史双池部署细节可通过 Git history 追溯，但不再是操作依据。
