# 16 — 生产策略切换：CSI300/500/1000 + 1M 账户（2026-11-02）

> **状态：已激活。** v21 于 2026-11-02 22:25 +08 部署；2026-11-02 回补并入 1M 账户，2026-11-03 首次真实前向发布成功；v22 修复 profile gate。旧 100M 账户归档只读。
>
> **补充（2026-11-03）：** 为保持账户曲线连续，2026-11-02 已作为 point-in-time 回补并入同一 1M 账户；11/03 从 11/02 收盘持仓/现金继续。详见 `17-october-backfill-continuous-account-20261102.md`。

## 1. 最终选择

| 市场 | 新生产策略 | 模型/标签 | 组合 | 相对旧 S01/A（G2 100M 研究） |
|---|---|---|---|---|
| CSI300 | `59b93929` | **10d** | Top10/Drop1 | +4.94pp CAGR；MDD 改善 13pp |
| CSI500 | `raw10d_c98856b4` | **20d** | **Top20/Drop2** | +3.51pp CAGR；Sharpe +0.145 |
| CSI1000 | `c98856b4` | 20d | Top10/Drop1 | 保持不变；S02/D6 未通过 |

CSI1000 明确选 **S01**，不采用 D6 风险不稳定的 S02。

## 2. 账户

- 三个市场各自独立 **100 万**账户；
- 新 lineage：
  - CSI300：`59b93929_top10_drop1_10d_canonical_1m`
  - CSI500：`raw10d_c98856b4_top20_drop2_20d_canonical_1m`
  - CSI1000：`c98856b4_top10_drop1_canonical_1m`
- 旧 100M canonical 账户归档只读，不缩放、不拼接；
- 网站 NAV 从 1.0000 重新开始。

## 3. 生产配置

- 新增 profile：
  - `59b93929_top10_drop1_10d`：label_horizon=10，topk=10，nd=1；
  - `raw10d_c98856b4_top20_drop2_20d`：label_horizon=20，topk=20，nd=2；
- 新增按市场映射：
  - `CANONICAL_TOPK_BY_MARKET`
  - `CANONICAL_ND_BY_MARKET`
  - `CANONICAL_PROFILES_BY_MARKET`
  - `CANONICAL_PAPER_LINEAGES_BY_MARKET`
- `PAPER_INITIAL_CASH=1_000_000`；
- `daily_cron` 每个市场分别传 topk/nd/profile/lineage，不再共用全局 Top10/Drop1；
- 网站按 profile 识别 10/20 只榜单与新 lineage。

## 4. Dry-run 证据

只生成信号，不发布、不建仓：

| 市场 | 结果 | 标签 | 榜单 | 数据日 |
|---|---|---|---|---|
| CSI300 | PASS | **10-session label maturity** | top10 | 2026-11-02 |
| CSI500 | PASS | 20-session label maturity | top20 | 2026-11-02 |
| CSI1000 | PASS | 20-session label maturity | top10 | 2026-11-02 |

- CSI300 dry-run 第一次暴露了硬编码的 “20-session/20日” 文案；已修正为从 profile 读取 horizon。
- 三个市场均通过 PIT、label maturity、模型重训、榜单生成。
- 日志：`/tmp/opencode/prod_dryrun_csi300_v2.log`、`prod_dryrun_csi500.log`、`prod_dryrun_csi1000.log`。

## 5. 测试

- 生产/审计/lineage：175 passed，16 subtests；
- 网站/前端安全：19 passed；
- 配置测试覆盖新 profile 的 horizon/topk/nd/lineage。

## 6. 上线步骤

1. 提交代码；
2. `modal deploy` 生产 app（`at2018cow`）——**已完成：v21，2026-11-02 22:25 +08**；
3. `daily_cron` 创建新 1M lineage、生成三市场信号（2026-11-03 已完成）；
4. 核对 artifact：lineage、initial_cash=1M、topk/nd、label_horizon、NAV=1.0（已完成）；
5. 网站切换到新 lineage；旧 100M 历史标记 archived（已完成）；
6. 不做并行 shadow；上线后按日监控异常，必要时回滚旧 lineage/app。

> 1M 账户的全周期与 2025–2026 扩展指标见 `19-g2-1m-account-replay-20261103.md` 与 `20-g2-1m-2026-holdout-20261103.md`；本文件表中的相对指标来自 G2 100M 研究。

## 7. 风险

- 无 shadow，直接切换；
- CSI300/CSI500 的新策略在 G2 100M 硬门槛上有缺口（D2 0.81pp / D3 0.85pp）；1M 口径见 `19`：CSI300 压力测试回撤深 0.97pp（v2 2pp 容忍），CSI500 最大回撤/全周期收益门槛未过。属于已接受的收益优先取舍；
- CSI1000 保持 S01，风险不变；
- 1M 账户容量通过，但整手/最低佣金仍需上线后监控；
- 2025–2026 数据未作为切换依据；本次切换基于 G2 2018–2024 与 dry-run，锁定后的扩展观察见 `20`。

## 8. 回滚

- 回滚 app 至上一生产版本；
- 旧 100M state 仍保留；
- 新 1M state 保留只读，不用于旧账户；
- 网站恢复旧 lineage 映射。
