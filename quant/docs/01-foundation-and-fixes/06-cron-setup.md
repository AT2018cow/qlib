# 每日信号自动运行（当前生产说明）

> 2026-11-12 核对：两个生产 App 均为 v26；调度为 T 日 18:00 主跑、23:00 只读检查、23:15 排队恢复，下一交易日 U 的 07:00 只读检查。配置已读回，v26 自然调度运行和邮件送达尚待验收。详见[生产状态记录](../07-code-review/2026-11-12-production-deployment-status.md)。
> 生产已切换为 CSI300 / CSI500 / CSI1000 三市场、每市场独立 100 万账户、分市场 TopK/DropN。
> ChiNext 日更与网页展示继续暂停。部署凭据与私有 workspace 信息不写入公开仓库。
> 安全边界与凭证加固见 `quant/docs/07-code-review/2026-11-10-security-hardening.md`。

## 当前架构

```text
T 18:00（北京时间）
  → 构建 T 日 PASS overlay
  → 分别运行 CSI300 / CSI500 / CSI1000 的冻结 profile
  → 要求数据日 T 相对最近完成交易日的 lag = 0
  → 生成下一交易日 U 的各市场 TopK ranking / chart / paper artifact
  → journal 冻结 payloads，推送仓库，提交三市场 paper state
  → 发布 T 日 NAV 并触发静态网站更新

T 23:00 `daily_postclose_check`：只读核对 T/U receipt 和状态，失败时报错并依平台配置告警
T 23:15 `daily_evening_retry`：需要恢复时只转发至同一 daily_cron，已有主跑则排队
U 07:00 `daily_readonly_check`：只读最终核对，不补建或重算
```

## 生产协议

| 市场 | Profile | 标签 | 组合 | 1M lineage |
|---|---|---|---|---|
| CSI300 | `59b93929_top10_drop1_10d` | 10d | Top10/Drop1 | `59b93929_top10_drop1_10d_canonical_1m` |
| CSI500 | `raw10d_c98856b4_top20_drop2_20d` | 20d | Top20/Drop2 | `raw10d_c98856b4_top20_drop2_20d_canonical_1m` |
| CSI1000 | `c98856b4_top10_drop1` | 20d | Top10/Drop1 | `c98856b4_top10_drop1_canonical_1m` |

- 信号：T 日收盘形成，供下一交易日 U 开盘使用；买入 5bp / 卖出 15bp，最低 5 元，委托数量遵循板块和日期相关规则；
- 重训：固定 20-session 时钟，原点 `2026-09-18`；新谱系首次运行或缓存缺失时先 bootstrap；
- 账户：三市场各自独立 100 万生产 paper；`2026-10-08`–`2026-11-02` 为 point-in-time 回补，`2026-11-03` 起为前向 paper。网站另有自 `2026-10-08` 开盘开始的独立因果重建账户，首日使用 `2026-09-30` 收盘评分；两条账户链不拼接；
- freshness：使用日 U 对应的上一真实交易日 T 必须有完整收盘数据；交易日历不可验证或数据滞后时 fail closed；
- paper：三市场状态分别按 journal 链提交，不继承旧 100M 账户，不跨市场共享现金/持仓；
- 历史记录：同日 artifact 不做事后重写；旧 100M 账户只读归档。

历史 1M 全周期与 2025–2026 扩展指标见下列文档；旧执行合同证据范围以[策略手册](../00-strategy-handbook/STRATEGY_ATLAS.md)和其引用的审计记录为准：

- `quant/docs/08-current-window-backtest/19-g2-1m-account-replay-20261103.md`
- `quant/docs/08-current-window-backtest/20-g2-1m-2026-holdout-20261103.md`

## 部署

生产部署需要单独授权。部署前检查干净的提交与远端一致，并从 `modal_qlib_cn_a10g.py` 确认 `QLIB_DEPLOY_PROFILE` 选中生产分支；该 selector 与 Modal 授权 profile 不是同一概念。以下变量须由操作者先在本地设置，勿把私有 profile 或凭据写入仓库。

```bash
: "${QLIB_DEPLOY_PROFILE:?请设置源码中的生产 selector}"
: "${QUANT_MODAL_PROFILE:?请设置已授权的 Modal profile}"
: "${QUANT_MODAL_ENVIRONMENT:?请设置生产 Modal environment}"

# 使用同一 checkout 和 selector，先 Website，后 Daily。
env QLIB_DEPLOY_PROFILE="$QLIB_DEPLOY_PROFILE" .venv/bin/modal deploy \
  --profile "$QUANT_MODAL_PROFILE" --env "$QUANT_MODAL_ENVIRONMENT" \
  quant/code/ops/website_simulation_modal.py
env QLIB_DEPLOY_PROFILE="$QLIB_DEPLOY_PROFILE" .venv/bin/modal deploy \
  --profile "$QUANT_MODAL_PROFILE" --env "$QUANT_MODAL_ENVIRONMENT" \
  quant/code/production/modal_qlib_cn_a10g.py
```

部署前必须核对 selector 实际解析到生产 App/Volume，不能仅以变量非空代替核对。部署后只读核对两个 App 的版本和提交、Daily 四个 cron、Website 无 cron、函数资源与 `daily_cron` 单容器限制；之后按自然运行结果核验 provider receipt、三市场 journal/paper state、website receipt 和网页更新。若 23:00 检查时主任务仍在运行，23:15 恢复会排队；不得绕开协调器并发重跑。
