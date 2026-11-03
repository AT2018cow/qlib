# 每日信号自动运行（当前生产说明）

> 2026-11-03 更新：生产已切换为 CSI300 / CSI500 / CSI1000 三市场、每市场独立 100 万账户、分市场 TopK/DropN。
> ChiNext 日更与网页展示继续暂停。部署凭据与私有 workspace 信息不写入公开仓库。

## 当前架构

```text
工作日 07:00（北京时间）
  → 获取交易日历并验证当天是否为交易日
  → 下载最新日频数据
  → 分别运行 CSI300 / CSI500 / CSI1000 的冻结 profile
  → 要求数据日与 signal date 之间交易日 lag = 0
  → 生成各市场 TopK ranking / chart / paper artifact
  → journal 冻结 payloads，推送仓库，提交三市场 paper state
  → 触发静态网站更新
```

07:45 的 `daily_cron_second_chance` 是同一 journal 状态机下的幂等补跑，不另建账户。

## 生产协议

| 市场 | Profile | 标签 | 组合 | 1M lineage |
|---|---|---|---|---|
| CSI300 | `59b93929_top10_drop1_10d` | 10d | Top10/Drop1 | `59b93929_top10_drop1_10d_canonical_1m` |
| CSI500 | `raw10d_c98856b4_top20_drop2_20d` | 20d | Top20/Drop2 | `raw10d_c98856b4_top20_drop2_20d_canonical_1m` |
| CSI1000 | `c98856b4_top10_drop1` | 20d | Top10/Drop1 | `c98856b4_top10_drop1_canonical_1m` |

- 信号：T 日收盘形成，供 T+1 开盘使用；买入 5bp / 卖出 15bp，最低 5 元，100 股整手；
- 重训：固定 20-session 时钟，原点 `2026-09-18`；新谱系首次运行或缓存缺失时先 bootstrap；
- 账户：三市场各自独立 100 万；`2026-09-30` 收盘起算，`2026-10-08`–`2026-11-02` 为 point-in-time 回补，`2026-11-03` 起为真实前向 paper；
- freshness：必须确认前一真实交易日数据可用；交易日历不可验证或数据滞后时 fail closed；
- paper：三市场状态分别按 journal 链提交，不继承旧 100M 账户，不跨市场共享现金/持仓；
- 历史记录：同日 artifact 不做事后重写；旧 100M 账户只读归档。

当前 1M 全周期与 2025–2026 扩展指标见：

- `quant/docs/08-current-window-backtest/19-g2-1m-account-replay-20261103.md`
- `quant/docs/08-current-window-backtest/20-g2-1m-2026-holdout-20261103.md`

## 部署

生产代码更新后，在已授权的生产环境中执行：

```bash
git checkout main
git pull --ff-only
modal deploy quant/code/production/modal_qlib_cn_a10g.py
```

部署后检查：三市场 journal、paper state、`available_dates`、forward performance 与网站渲染。
