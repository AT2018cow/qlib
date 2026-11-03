# 18 — 2026-11-03 定时任务失败与修复（MVP lineage gate）

> **状态：已修复并手动补跑成功。** 11/3 的 12 个 canonical 文件已发布，三市场 paper state 已提交；v22 已部署，后续 07:00 cron 使用修复后的代码。

## 1. 现象

2026-11-03 07:00 的 `daily_cron` 与 07:45 的 `daily_cron_second_chance` 均失败：

```
ValueError: canonical CSI1000 MVP profile must be a frozen canonical profile
  at csi1000_mvp_contract.py:338 validate_production_lineage
  at csi1000_mvp_contract.py:451 build_mvp_metadata
  at modal_qlib_cn_a10g.py:4491 daily_cron
```

CSI1000 已生成信号，失败发生在 CSI500/CSI300 的 `extra_meta` 构建阶段。

## 2. 根因

`validate_production_lineage` 的冻结 profile 白名单是 CSI1000 时代的，缺少：

- `59b93929_top10_drop1_10d`（CSI300）
- `raw10d_c98856b4_top20_drop2_20d`（CSI500）

因此新市场信号虽然生成，但 MVP metadata 侧车无法构建，整个 journal 发布在 `prepare` 之前中止。

**影响范围：** 11/3 没有产生 journal record、没有 Volume state 提交、没有 GitHub 信号文件；11/2 的账户状态与 anchor 未被修改。

## 3. 修复

- 将两个新 profile 加入 `validate_production_lineage` 的冻结白名单；
- 错误文案从 CSI1000 专用改为通用 production lineage；
- CSI300/CSI500 的 `canonical_freshness_gate` 改用 `multi_detail`，不再复用 CSI1000 专用文案；
- 新增回归测试：三个当前市场 profile 均被接受，未知 profile 被拒绝。

## 4. 部署与补跑

- `modal deploy` v22：2026-11-03 08:22 +08；
- 手动前台补跑 `daily_cron`：2026-11-03 08:24–08:27 +08；
- 结果：
  - `journal 2026-11-03: stage=PREPARED, published=0/12`；
  - 推送 12 个 canonical 文件；
  - paper state 提交：`csi1000`, `csi300`, `csi500`；
  - journal 最终 `VOLUME_COMMITTED`；
  - 名称映射更新 5,578 只。

## 5. 核验

- `publication_journal/2026-11-03/record.json`：`VOLUME_COMMITTED`，12 个文件，3 个 state；
- 三市场 11/3 `paper_portfolio`：
  - lineage 均为 `*_canonical_1m`；
  - `signal_data_date=2026-11-02`，`usage_date=2026-11-03`；
  - `pending_orders.execution_date=2026-11-03`；
  - `execution_report=null`（等待 11/4 结算 11/3 开盘）；
- journal 链完整性 PASS，三个 1M 路径 `check_live_state` PASS；
- 线上网站已出现 11/3 榜单，数据截止 11/02；forward 曲线 latest 仍为 11/02，11/3 显示“待结算”；
- CI：`Quant extension checks`、`CSI1000 MVP publication contract` 均通过；
- 网站部署：11/3 信号 commit 与名称映射 commit 的 Pages workflow 均成功。

## 6. 风险与后续

- 11/3 的 ST 快照接口连续 TRANSPORT 失败，按既有 fallback 使用 `ST_IGNORED_REFERENCE_ONLY`，网站已披露“ST未纳入模拟”；
- 11/3 的 20 日/10 日 label maturity、PIT universe、provider calendar 均 PASS；
- 11/3 是切换后首个真实 paper 日，live 模型缓存为空，按生产 bootstrap 规则以 11/2 数据完成首次 fit；后续按 20-session 节律重训；
- 11/4 07:00 的 cron 将用 11/3 开盘/收盘数据结算 11/3 pending 订单，forward 曲线届时新增 11/3 估值点；
- 若后续再出现同类 profile gate 失败，应直接检查 `validate_production_lineage` 白名单与 `profile_manifest` 是否同步。
