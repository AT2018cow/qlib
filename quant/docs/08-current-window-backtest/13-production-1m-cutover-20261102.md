# 13 — 生产账户 1M 切换方案（已随新策略激活）

> **状态：已激活。** v21 已部署；当前生产为 CSI300 B/S01 10d、CSI500 B/S02 20d、CSI1000 S01 20d，三市场各 1M；下一个交易日 2026-11-03 首次发布。

## 1. 决策

- 生产/网站/后续研究统一使用 **100 万元/市场**（仅在新策略上线时激活）；
- 生产已随新策略切换为 **1M**；旧 100M/S01 账户归档只读；
- 三个市场仍是独立账户，不合并、不等权；
- 旧 100M 账户历史**不缩放**，新策略上线时归档只读；
- 新账户使用新 lineage，从 **NAV=1.0000** 重新开始；
- 切换日：与新策略上线同日，日期未定。

## 2. 新 lineage

| 市场 | 旧 100M lineage（归档） | 新 1M lineage |
|---|---|---|
| CSI1000 | `c98856b4_top10_drop1_canonical` | `c98856b4_top10_drop1_canonical_1m` |
| CSI500 | `raw10d_c98856b4_top10_drop1_canonical` | `raw10d_c98856b4_top10_drop1_canonical_1m` |
| CSI300 | `59b93929_top10_drop1_canonical` | `59b93929_top10_drop1_canonical_1m` |

旧 lineage 的 `paper_portfolio/<market>.json` 保留在 Volume，不删除、不缩放；新 lineage 写入 `<market>__..._1m.json`。

## 3. 代码变更

- `csi1000_production_config.py`：
  - 新增 `PAPER_INITIAL_CASH = 1_000_000`；
  - canonical lineages 改为 `_1m`；
  - 新增 `LEGACY_CANONICAL_PAPER_LINEAGES_BY_MARKET` 供归档/审计识别。
- `modal_qlib_cn_a10g.py`：
  - 新 paper account 使用 `PAPER_INITIAL_CASH`，不再硬编码 100M。
- `forward_performance.py`：
  - canonical lineage 改为 `_1m`，旧 lineage 保留在 `CANONICAL_LINEAGES` 中作为 legacy。
- `website/app.js`：
  - profile → lineage 映射改为 `_1m`。
- 审计/lineage 测试更新到 `_1m`。

## 4. 网站口径

- 网站仍展示归一化 NAV，起点 1.0000；
- 切换后不再拼接旧 100M 历史；
- 方法论页应注明：
  - 2026-10-15～2026-11-02 为旧 100M 账户；
  - 2026-11-03 起为新的 1M 账户；
  - 两段不连续，不能直接比较累计收益。

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
3. 部署 `quant/code/production/modal_qlib_cn_a10g.py`；
4. 下一个交易日运行，创建新 1M lineage 状态；
5. 确认新 `paper_portfolio` artifact 的 lineage、initial_cash、NAV=1.0；
6. 网站切换到新 lineage；旧历史标记为 archived；
7. 观察 4 周，不回溯、不拼接旧曲线。

## 7. 研究口径

- 历史 G1/G2 结果仍在 100M 口径下，保留可复现记录；
- 新研究默认使用 1M；
- 收益率在忽略整手/最低佣金时近似不变，但容量、成交和成本不同；
- 不应把 1M 的 D8 通过写成 1 亿容量通过。

## 8. 待办

- [x] 配置/lineage 代码
- [x] 审计/lineage 测试
- [x] 网站 lineage 映射
- [ ] 部署（需操作员确认时点）
- [ ] 首个 1M artifact 校验
- [ ] 网站 cutover 公告
- [ ] D6 相位/seed（S02 仍是 v2 候选）
