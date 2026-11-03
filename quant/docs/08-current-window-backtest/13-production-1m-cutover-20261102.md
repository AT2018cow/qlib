# 13 — 生产账户 1M 切换（已激活）

> **状态：已激活。** 2026-11-02 切换；三市场各 100 万；`2026-10-08`–`2026-11-02` 为 point-in-time 回补，`2026-11-03` 首次真实前向发布并已成功完成。

## 1. 决策

- 生产/网站/后续研究统一使用 **100 万元/市场**；
- 三个市场仍是独立账户，不合并、不等权；
- 旧 100M 账户历史**不缩放**，归档只读；
- 新账户使用新 lineage，从 **NAV=1.0000** 重新开始；
- 新账户从 `2026-09-30` 收盘起算，`2026-10-08` 开盘首次建仓。

## 2. 新 lineage

| 市场 | 旧 100M lineage（归档） | 新 1M lineage |
|---|---|---|
| CSI1000 | `c98856b4_top10_drop1_canonical` | `c98856b4_top10_drop1_canonical_1m` |
| CSI500 | `raw10d_c98856b4_top10_drop1_canonical` | `raw10d_c98856b4_top20_drop2_20d_canonical_1m` |
| CSI300 | `59b93929_top10_drop1_canonical` | `59b93929_top10_drop1_10d_canonical_1m` |

旧 lineage 的 `paper_portfolio/<market>.json` 保留在 Volume，不删除、不缩放；新 lineage 写入 `<market>__..._1m.json`。

## 3. 代码变更

- `csi1000_production_config.py`：
  - `PAPER_INITIAL_CASH = 1_000_000`；
  - canonical lineages 改为 `_1m`；
  - 新增 `LEGACY_CANONICAL_PAPER_LINEAGES_BY_MARKET` 供归档/审计识别；
  - 新增按市场 profile/topk/nd 映射。
- `modal_qlib_cn_a10g.py`：
  - 新 paper account 使用 `PAPER_INITIAL_CASH`，不再硬编码 100M；
  - 三市场按各自 profile/topk/nd 生成信号与 paper。
- `forward_performance.py`：
  - canonical lineage 改为 `_1m`，旧 lineage 保留在 `CANONICAL_LINEAGES` 中作为 legacy。
- `website/app.js`：
  - profile → lineage 映射改为 `_1m`；
  - 按市场显示 Top10/Top20 与 DropN。
- 审计/lineage 测试更新到 `_1m`。

## 4. 网站口径

- 网站展示归一化 NAV，起点 1.0000；
- 当前展示 `2026-09-30` 起的三市场 1M 连续账户；
- `2026-10-08`–`2026-11-02` 为回补，`2026-11-03` 起为真实前向 paper；
- 旧 100M 账户归档只读，不展示、不拼接；
- 方法论页展示 1M 全周期表与 2025–2026 扩展观察。

## 5. D8 容量

同一批 Top20 候选、单日 5% ADV：

| 账户 | 超 5% ADV 比例 | ADV 占比中位数 |
|---:|---:|---:|
| **100 万** | **0%** | 0.06% |
| 500 万 | 0% | 0.31% |
| 1000 万 | 0.4% | 0.61% |
| 5000 万 | 27.8% | 3.06% |
| 1 亿 | 58.95% | 6.12% |

结论：

- 1M 口径下 D8 不再绑定；
- 策略容量约在 5–10M；
- 不能把 1M 结果外推为 1 亿可执行。

## 6. 部署顺序（已执行）

1. 代码 review + 测试通过；
2. 归档旧 100M state（只读副本/SHA 记录）；
3. 部署 `quant/code/production/modal_qlib_cn_a10g.py`（v21 → v22 修复 profile gate）；
4. `2026-11-02` 回补并入同一 1M 账户；
5. `2026-11-03` 首个真实前向 paper 发布成功；
6. 网站切换到新 lineage；旧历史归档；
7. 观察 4 周，不回溯、不拼接旧曲线。

## 7. 研究口径

- 历史 G1/G2 结果仍在 100M 口径下，保留可复现记录；
- 当前 1M 全周期复核见 `19-g2-1m-account-replay-20261103.md`；
- 2025–2026 1M 扩展见 `20-g2-1m-2026-holdout-20261103.md`；
- 新研究默认使用 1M；
- 收益率在忽略整手/最低佣金时近似不变，但容量、成交和成本不同；
- 不应把 1M 的 D8 通过写成 1 亿容量通过。

## 8. 待办

- [x] 配置/lineage 代码
- [x] 审计/lineage 测试
- [x] 网站 lineage 映射
- [x] 部署
- [x] 首个 1M artifact 校验
- [x] 网站 cutover
- [x] 1M 全周期与 2025–2026 扩展复核
- [x] CSI1000 D6 相位/seed 复核（S02 不采用）
