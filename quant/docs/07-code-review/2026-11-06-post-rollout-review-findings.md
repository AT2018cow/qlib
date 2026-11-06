# 2026-11-06 近期修改审查问题记录

> **状态：** `REVIEW_FINDINGS_ONLY / NO_CODE_FIX`
>
> **审查范围：** `a31673670c8a32e4d7bea4e65cf8ece812583deb..33bef4b64`
>
> **审查时间：** 2026-11-06
>
> **审查方式：** 只读 diff、全量本地测试、独立 NAV 审计、数量/预算性质测试。
>
> **总体结论：** 修改方向合理，安全边界明显增强；但存在一处数据谱系回归、`allow_lag` 发布安全漏洞和 CI/披露缺口。当前不能视为“完成”，不建议直接接入新数据源。

本文只记录问题，不修改任何源文件，也不改变生产状态。

---

## 1. 审查摘要

| ID | 级别 | 问题 | 状态 |
|---|---|---|---|
| F01 | **P1** | 网站发布器把 live-paper forward/backfill 覆盖成 10/08 模拟账户 | 待修 |
| F02 | **P1** | `allow_lag` 绕过三市场 R01 快照一致性校验 | 待修 |
| F03 | **P1** | 无 pending 时 `allow_lag` 会新造“旧收盘→今日开盘”paper 订单 | 待修 |
| F04 | **P1** | override 发布后无法在数据补齐时自动纠正 | 待修 |
| F05 | **P1** | 网站把 `OPERATOR_OVERRIDE lag=1` 当成正常 PASS | 待修 |
| F06 | **P1** | CI 未运行本次新增的关键测试 | 待修 |
| F07 | P2 | forward NAV 基线与 `account_identity` 的 1M 初始资金不一致 | 待修 |
| F08 | P2 | 任一 quote gap 会中止整个网站发布，包括未持有的候选 | 待修 |
| F09 | P2 | 前端拒绝所有非 schema-3 文件，schema-2 fallback 实际不可达 | 待修 |
| F10 | P2 | fit receipts 未绑定 model/data 实际覆盖的 points | 待修 |
| F11 | P2 | `latest_date=null` 被字符串比较为“最新” | 待修 |
| F12 | P2 | legacy 同市场 lineage 被误报为 cross-market | 待修 |
| F13 | P2 | 审计仍要求 pending 日期精确等于发布日 | 待修 |
| F14 | P2 | `SKIPPED_STALE_DATA` 被 pipeline 标成 SUCCEEDED | 待修 |
| F15 | P2 | 父任务 5h 超时小于子任务预算 | 待修 |
| F16 | P2 | methodology 仍写 08:15 单独更新 | 待修 |
| F17 | P2 | `make black` 会对 19 个改动文件报重排 | 待修 |
| F18 | P2 | 板块数量规则全局生效，但只重放了部分历史单元 | 待补证 |
| F19 | P2 | 官方除权参考价、逐日 ST、开盘流动性仍未核实 | 待补证 |
| F20 | P2 | 生产 v30 与最新 tag `quant-v1.2.0` 不一致 | 待发版 |

---

## 2. P1 详细记录

### F01 — 网站发布器覆盖 live-paper forward/backfill

**位置：** `quant/code/ops/website_simulation_modal.py:348-350`

```python
files[f"quant/data/signals/{market}_website_performance.json"] = content
files[f"quant/data/signals/{market}_forward_performance.json"] = content
files[f"quant/data/signals/{market}_backfill_performance.json"] = content
```

**事实：**

- 三个市场共九份文件现在都是**字节完全相同**的 schema-3 `CAUSAL_SIMULATION` 序列；
- `start_date=2026-10-08`，`data_mode=CAUSAL_SIMULATION`；
- 原 live-paper `forward_performance` 和 BACKFILL prefix 语义被覆盖。

**已复现：**

```bash
python quant/code/ops/audit_paper_nav.py \
  --signals quant/data/signals \
  --market csi1000 \
  --forward quant/data/signals/csi1000_forward_performance.json
```

结果：`status=FAIL`，issue=`FORWARD_SCHEMA_DIFF`（`audit_paper_nav.py:27,412-414` 期望 `start_date=2026-10-12` 且 points 与 canonical live ledger 对齐）。

`quant/code/audit/audit_repair_impact.py:203` 仍把 `_backfill_performance.json` 当 prefix 读取，也会受影响。

**影响：** 独立 NAV 审计和账户谱系对账失效；live-paper 与网站模拟账户被混为同一份文件。

**建议：** 只覆盖 `_website_performance.json`；保留 `_forward_performance.json` 和 `_backfill_performance.json`，或使用带版本号的新文件名，并同步更新所有读取方。

---

### F02 — `allow_lag` 绕过三市场 R01 快照一致性

**位置：**

- `quant/code/production/signal_publication_gate.py:103-109`
- `quant/code/production/modal_qlib_cn_a10g.py:4375-4386`

**事实：**

`validate_multi_market_publication` 在第一个 market lag≠0 时立即返回，尚未检查后续市场。`daily_cron` 的 `allow_lag` 分支只要看到 `"got lag=1"` 就把 `multi_action` 改成 `publish`。

因此可能出现：

- CSI1000 lag=1，CSI500/CSI300 完全未校验；
- CSI500 lag=1，CSI300 未校验；
- 三市场使用不同 data_date/archive SHA/calendar SHA，却一起发布在同一个 signal_date 下。

**影响：** 破坏 R01 核心保证；override 路径可发布混合快照。

**建议：** allow_lag 先独立校验三市场身份；只有三市场 data_date、archive、calendar 完全一致且均为同一单日滞后时，才允许 override。

---

### F03 — 无 pending 时 `allow_lag` 会新造滞后 paper 订单

**位置：**

- `quant/code/production/modal_qlib_cn_a10g.py:1390-1395`（仅当 pending 存在时冻结）
- `quant/code/production/modal_qlib_cn_a10g.py:1430+`（pending 为空时正常 plan）

**事实：**

如果 `pp.pending_signal is None`（新账户、已结算账户等），代码会进入正常 plan 分支：

- `signal_date = res["date"]`（旧数据日）；
- `execution_date = usage_date`（今天）；
- 生成一笔“旧收盘 → 今日开盘”的新 paper 订单。

这与 allow_lag 声明中的 `ranking-only; paper pending stays frozen` 矛盾。

**影响：** paper 账户可能真的按滞后数据交易，而不仅是发布排名。

**建议：** allow_lag 模式下要求必须存在待执行的旧 pending；否则只发布排名、不生成 paper 订单，或直接拒绝。

---

### F04 — override 发布后无法在数据补齐时纠正

**位置：** `quant/code/production/modal_qlib_cn_a10g.py:4278-4283`

**事实：**

一旦 journal 记录 `VOLUME_COMMITTED`，后续运行直接返回 `ALREADY_PUBLISHED`，不再重新评估 freshness。journal 记录保存 files/states，但没有保存 `data_date`/`lag_days`，补跑无法判断“这份排名基于哪个数据日”。

**影响：**

- 11/06 排名会永久保持滞后版本；
- 文档中“等待数据源补发后回补该交易日”在当前 journal 语义下不可执行；
- 数据补齐后也没有自动替换路径。

**建议：** metadata/journal 增加 `data_date`、`lag_days`、`publication_mode`；提供版本化回补/替换路径，或明确标记为不可变 stale 记录。

---

### F05 — 网站把 override 当成正常 PASS

**位置：**

- `quant/website/app.js:566`（只接受 `PASS`/`PASS_BACKFILL`）
- `quant/website/app.js:623`（只根据 `PASS_BACKFILL` 标记回补）

**事实：**

11/06 metadata：

- `publication.status = PASS`；
- `publication.canonical_freshness_gate` 才包含 `OPERATOR_OVERRIDE lag=1 ...`；
- `paper.status = awaiting_execution_data`。

前端不检查 gate 字符串，因此页面会显示“校验 · 通过”，不会突出“滞后排名、paper 未执行”。

**影响：** 用户可能把 11/06 的滞后排名当成正常 T-1 信号。

**建议：** 增加 `PASS_LAGGED`/`PASS_OPERATOR_OVERRIDE` 状态，或前端显式检查 gate 字符串并显示警告。

---

### F06 — CI 未运行新增关键测试

**位置：** `.github/workflows/csi1000-tuner-core.yml`

**事实：**

core-tests 和 extended-execution-tests 仍使用手工测试清单，未包含：

- `test_daily_pipeline.py`
- `test_website_simulation.py`
- `test_extension_calculation_fixes.py`
- `test_audit_account_contracts.py`
- `test_benchmark_evidence_overlay.py`
- `test_limited_rollout_readiness.py`
- `test_prelive_ranking.py`
- `test_prelive_revalidation.py`
- `test_research_identity.py`
- `test_website_async_and_evidence.cjs`
- `test_website_simulation_frontend.cjs`

`Quant extension checks` 显示 success，只证明旧清单通过；新增的发布/网站/账本逻辑没有被 CI 覆盖。`allow_lag=True` 分支也没有行为测试。

**建议：** CI 直接运行 `pytest -q quant/tests` 和 `node --test quant/tests/*.cjs`，或至少补齐上述清单并增加 allow_lag 分支测试。

---

## 3. P2 详细记录

### F07 — forward NAV 基线与 `account_identity` 不一致

**位置：** `quant/code/production/forward_performance.py:173-206`

`baseline = ordered[0]["portfolio_value_pre_trade_open"]`，但返回的 `account_identity` 使用 `initial_cash=1,000,000`。实际 live artifacts 的首个 pre-trade open 约为 753,903。

如果 prefix 缺失，`merge_forward_performance(None, live)` 会原样返回 live 文件（`merge_forward_performance.py:124-127`），页面会显示“1M 账户”，但收益实际 rebase 到 753k。

**建议：** 明确 forward NAV 的基准语义；live-only 文件必须以 1M 初始资金为基准，或显式标记 rebased baseline；缺 prefix 时拒绝发布。

---

### F08 — 任一 quote gap 中止整个网站发布

**位置：**

- `quant/code/production/website_simulation.py:45-46`
- `quant/code/research/regime_reselection/prelive_revalidation.py:220-250`

`result["gaps"]` 非空即抛错；但 gap list 由所有 active names 构成，包括 `planned_buy` 候选。一个停牌/涨停候选就可能阻断三市场发布，而不是按规则保留现金。

**建议：** 只对实际持仓/实际成交的缺失报价 fail-closed；未持有的 planned buy 应按“无法买入、保留现金”处理，并记录原因。

---

### F09 — 前端拒绝所有非 schema-3 文件

**位置：** `quant/website/app.js:307,382`

`loadForwardPerformance` 总是传入 `pool.key`，而 `normalizeForwardPerformance` 在有 market 时对非 schema-3 直接返回 invalid。schema-2 的 `BACKFILL_PLUS_LIVE` 校验代码不可达。

如果 schema-3 文件缺失或被替换为合法 live-paper 系列，页面显示“数据异常”，不能回退。

**建议：** 明确是否保留 schema-2 fallback；如果保留，修正调用路径和测试；否则删除死代码并明确只支持 schema 3。

---

### F10 — fit receipts 未绑定实际 points

**位置：**

- `quant/code/production/website_simulation.py:180-194`
- `quant/website/app.js:266-302`

Python 校验只检查 `fit_asof`、profile、training contract 顺序、`signal_end >= fit_asof`；没有要求 `model_sha256`/`data_prefix_sha256`，也没有把每个 fit 的日期区间映射到实际 points。前端完全不检查 `fit_receipts`。

**建议：** 为每个 point 绑定 fit block，校验 model/data SHA 和覆盖区间；前端至少验证 receipt 数量和首尾 fit。

---

### F11 — `latest_date=null` 被字符串比较为“最新”

**位置：** `quant/code/ops/website_simulation_modal.py:372-375`

```python
if str(previous.get("latest_date", "")) > s["latest_date"]:
```

`str(None) == "None"`，字典序大于任何 ISO 日期，因此已有的 `awaiting_first_valuation` 空序列会被误判为“比新数据更新”，发布被拒绝。

**建议：** 先判断 `previous.get("latest_date")` 是否为 `None`/空；只在双方都有有效日期时比较。

---

### F12 — legacy 同市场 lineage 被误报为 cross-market

**位置：** `quant/code/production/forward_performance.py:25-28,55-60`

`CANONICAL_LINEAGES` 包含 legacy csi1000/csi500/csi300 lineage，但代码对任何非当前 `_canonical_1m` 的 lineage 都报 `cross-market paper lineage mismatch`。注释说 legacy/unknown 应跳过，实际 check 在 `usage_date` 过滤之前执行，一条旧 artifact 就可能让整个 build 失败。

**建议：** 先按 `usage_date` 过滤，再对同市场 legacy lineage 跳过并记录，不把同市场旧 lineage 当成跨市场。

---

### F13 — 审计仍要求 pending 日期精确等于发布日

**位置：** `quant/code/audit/mvp_entry_review.py:72-73`

`awaiting_execution_data` 工件的 top-level `signal_date`/`execution_date` 是本次发布日，而 `pending_orders` 是旧的 11/05。新 metadata 已提供 `pending_signal_date`/`pending_execution_date`，但审计仍精确比较旧字段，会把合法工件报成 `PENDING_ORDER_DATES_MISMATCH`。

**建议：** 审计读取 pending 专用字段，并接受 `status=awaiting_execution_data`。

---

### F14 — `SKIPPED_STALE_DATA` 被标成成功

**位置：** `quant/code/production/daily_pipeline.py:25`

`run_daily_pipeline` 把任何正常返回都标为 `SUCCEEDED`，包括 `SKIPPED_STALE_DATA`。Modal 运行是绿的，监控如果只看退出码，会漏掉“今天没有新信号”。

**建议：** 在 pipeline 结果中显式区分 `PUBLISHED`/`SKIPPED_STALE_DATA`，并增加告警/指标；或者让 stale skip 成为非绿状态。

---

### F15 — 父任务超时小于子任务预算

**位置：**

- `quant/code/production/modal_qlib_cn_a10g.py:4141`（`daily_cron` timeout 5h）
- `quant/code/production/modal_qlib_cn_a10g.py:3551`（单个 `daily_standalone` timeout 2h，三市场串行）
- `quant/code/ops/website_simulation_modal.py:415`（网站任务 timeout 3h）

最坏情况下信号阶段 3×2h + 网站 3h = 9h，父任务 5h 会先超时。`daily_cron` 还保留 5 次重试，可能进一步超过 24h 补跑预算。

**建议：** 统一父子超时预算，或降低子任务上限；明确最坏情况总时长。

---

### F16 — methodology 仍写 08:15 单独更新

**位置：**

- `quant/website/methodology.html:185`（仍写“每日 08:15 单独更新”）
- `quant/website/methodology.html:170`（“每日自动获取 ST 快照”容易与网站历史模拟的 `ST_HISTORY_UNVERIFIED_REFERENCE_ONLY` 混淆）

实际调度是 07:00 主跑 + 08:00 补跑，08:15 独立调度已取消。

**建议：** 改为“随 07:00 主跑/08:00 补跑更新”；明确网站历史模拟未核实逐日 ST。

---

### F17 — `make black` 未通过

本地 `black 25.1.0` 按仓库 120 列检查，19 个改动文件会被重排，包括：

- `quant/code/execution/execution_limits.py`
- `quant/code/execution/portfolio_performance.py`
- `quant/code/production/paper_portfolio.py`
- `quant/code/production/modal_qlib_cn_a10g.py`
- `quant/code/research/csi300_csi500/stage0_p0_core.py`
- 多个 tests

`make black` 会失败；当前 CI 不跑 black，所以 CI 绿不等于 lint 通过。

**建议：** 在最终发布前运行 `make black`，再跑 `make pylint`/`flake8`/`mypy`（按 AGENTS.md 顺序）。

---

### F18 — 板块数量规则全局生效，但只重放了部分历史单元

**位置：** `quant/code/execution/board_execution.py:224`（`research_exchange` 默认 `enforce_board_quantities=True`）

这会改变所有历史研究回测的成交数量。当前只重放了 12 个基础执行单元和 22 个 overlay 单元；其他历史候选的旧指标不能直接与新口径比较。

**建议：** 对全量候选重跑，或在报告中明确标记 `LEGACY`；不要把未重跑的旧指标当作修正后业绩。

---

### F19 — 官方除权参考价、逐日 ST、开盘流动性仍未核实

`execution_limits` 仍使用“当前复权因子还原参考价”的推断方式；官方除权参考价、逐日历史 ST、开盘集合竞价流动性尚未取得外部证据。

**建议：** 保留文档中的限制说明；不要把这些修正后的历史指标称为最终可成交业绩。

---

### F20 — 生产 v30 与最新 tag 不一致

`git describe` 为 `quant-v1.2.0-15-g33bef4b64`；生产应用已是 v30，但最新 tag 仍停在 `quant-v1.2.0`。版本策略要求 tag 与部署代码一致。

**建议：** 在 P1 修复并重新验收后，按策略发新版本（策略/执行契约变更应为 MINOR，例如 `quant-v1.3.0`）。

---

## 4. 已验证合理的部分

以下改动方向正确，建议保留：

- Q01–Q12 的缺陷修复：缺基准 fail-closed、NaN/Inf 边界、Decimal 涨跌停、板块数量、账本守恒、分段 NAV、前端乱序；
- `artifact_io` 严格 JSON、`paper_accounting.verify_ledger` 有限数校验；
- `account_boundary`/`coordinate_contract` 的账户身份与复权坐标边界；
- 07:00 / 08:00 统一调度、`max_containers=1`、journal 幂等恢复；
- 网站模拟账户的 10/08 起算、逐日账本、source identity、causal fit；
- 全量本地回归 **865 passed, 1 skipped, 50 subtests**；前端 **28 passed**；
- 我另做的 30 万组随机数量/预算性质测试未发现超预算或非法数量。

---

## 5. 复现命令与观察

```bash
# 全量 Python
.venv/bin/python -m pytest -q quant/tests
# 865 passed, 1 skipped, 50 subtests passed

# 前端
node --test quant/tests/*.cjs
# 28 passed

# 独立 NAV 审计（当前会失败）
.venv/bin/python quant/code/ops/audit_paper_nav.py \
  --signals quant/data/signals \
  --market csi1000 \
  --forward quant/data/signals/csi1000_forward_performance.json
# status=FAIL, issue=FORWARD_SCHEMA_DIFF

# black
.venv/bin/python -m black -l 120 --check <changed files>
# 19 files would be reformatted

# 版本状态
git describe --tags --always
# quant-v1.2.0-15-g33bef4b64
```

---

## 6. 建议修复顺序

1. **F01**：恢复 live-paper forward/backfill 文件，网站只写 `_website_performance.json`；重跑 `audit_paper_nav`。
2. **F02–F04**：收紧 `allow_lag`：三市场一致性、必须存在 frozen pending、journal 记录 data_date/lag 并支持版本化回补。
3. **F05/F13/F14**：前端和审计显式识别 `OPERATOR_OVERRIDE`/`awaiting_execution_data`；pipeline 区分 stale skip。
4. **F06**：把新增测试加入 CI，并补 `allow_lag=True` 行为测试。
5. **F07–F12**：修 forward 基线、quote gap、schema-2 fallback、fit receipts、null latest_date、legacy lineage。
6. **F15–F17**：统一超时预算、修正 methodology、运行 `make black`。
7. **F18–F20**：全量重跑或标记 legacy，补外部证据，发新版本。
8. 最后再评估接入 Tushare/BaoStock 等新数据源。

---

## 7. 边界

- 本文只记录问题，不修改任何源文件，不部署、不推送、不改变生产状态。
- 测试通过不等于成交模拟、策略排名或公开收益均正确；本文列出的问题仍未修复。
- 未读取真实券商成交、官方除权参考价、逐日历史 ST 或开盘集合竞价流动性；相关结论保留为待补证。
