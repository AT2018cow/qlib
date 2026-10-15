# 31 — Stage-B F5 独立账本证据哈希与失败即停止门禁（2026-10-09）

> **审计工具加固，非策略修复、非新收益结果。** 基线 PR #45 已合并；本 PR 仅修改 `audit/run_winner_phase0_execution_audit.py`、新增纯 Python 审计门禁/负控测试/CI 与本说明。**没有**改 `csi1000_stage_b.py`、模型/信号/生产、Modal 部署、历史原始/修复版结果或提交的 F5 证据。重审时只在**全新本地目录**生成衍生证据，不覆盖现存文件。

## 修复什么

先前 winner phase0 fixed-mode F5 输出为 424 个交易日、1696 个订单，重算最大账户差约 `7e-8` CNY、收益差约 `5e-16`，数字可从已提交 CSV 独立复算，但原审计脚本有验收漏洞：

1. `--market-file` 可以指定固定轨新行情，却在 `execution_export_manifest.json` 中错误地记录默认旧 `raw/market_data.parquet` 的 SHA。原有 manifest 对报表与决策的 `expected_sha256=null`，无法从单次审计中判断意外替换。
2. 哈希不匹配只是记录 `match=false` 而不会停止；卖单请求量对卖出前持仓量的检查是空逻辑，`sell_amount_mismatches` 无法触发。
3. 以前 `independent_execution_account=PASS` 只看账户与日收益误差；没有将订单/可交易性/因子/整手/现金/累计成本/累计换手与日度费率纳入统一的判定。原脚本会覆盖默认审计输出文件。

本 PR 提供纯函数 `audit/stage_b_f5_gates.py`，让这些约束成为审计结果的**实际门禁**，并由 CI 注入反例。

## 数据身份与最小信任边界

- **原始 legacy 模式**：报告/决策/信号哈希由 `stage_b_full_51756897fc752304.json` 冻结；calendar、index-spans、默认行情来自原已提交的 `audit/evidence/winner_phase0/execution_export_manifest.json`。临时使用其他行情文件必须显式给出**在本次审计前由原始授权导出步骤锁定**的 `--expected-market-sha256`。
- **fixed-mode**：明确要求修复版报告、修复版决策、**实际传入的修复版行情文件**三个预先锁定的 SHA256；缺少任何一个即拒绝运行。signal 始终核对冻结字节哈希；calendar 与 CSI1000 成分定义继续核对初始导出 manifest 的字节哈希；原始 provider `provider_snapshot.json` 的 `snapshot_token` / `provider_fingerprint` 必须与冻结 result JSON 一致。
- **不能把同一次审计即时算出的 SHA 当作独立 expected 值**；应从先行的只读 provider 数据导出记录或已有可信证据 manifest 取得 expected，并在导出、回放、重建账户三个阶段保持同一批 pinned evidence。
- 所有实际输入经字节验证后才进入账本；新 manifest 的 `verified_inputs.market.actual_sha256` 和 `auxiliary_raw.market_data.parquet.sha256` 均来自**实际 `--market-file`**，不再从默认路径误取；另记录该文件名（不泄露私人绝对路径）、大小、expected / actual。
- `fixed_independent_ledger_summary.json`（PR #45 后提供）原始字节保持不变，它仍是**历史诊断性陈述**；请不要用该文件中的 `match=null` 当新门禁通过的证据。新输出必须和新运行结果关联，不得冒充旧证据已自动重新签署。

已提交的 fixed report / decisions 可作为后续复验的**先行固定值**：
```text
fixed report.parquet SHA256:
0e9ba034b94d363117dbb924639178f26af4a116763e9868d9960f321b601048
fixed decisions.json SHA256:
4694e67bd1a1870707d02d0eaf0e9bb0b29533cb209707e894d648fdd2a11063
frozen signal.parquet SHA256:
189d06cce7cdc52b1908439fb18fd9e3f1b8dcc8875a87bf5b6ec75d82e0278e
```
**固定轨 market_data.parquet 的预先固定 SHA 目前未提交，不能猜。** 在授权实验环境内从原行情导出日志/导出 manifest 核对并填入 `--expected-market-sha256`；若尚无可信先验，应先做只读导出、保存可审核的证据清单，再运行 F5。

## 严格 F5 门禁

- 先验：所有输入 SHA256 准确，原始 provider snapshot 身份一致，424 个报告日与 424 个决策日逐个等于原日历交易日，信号前一交易日对应关系不变。
- D1/D2：424/424 日决策重建一致；所有订单可交易性违规、涨跌停/因子/买入手数/买入金额不符、负现金事件均为 **0**。
- **新的卖出校验**：对每个 sell，在执行前记录 `held_qty`，要求 `requested_qty == held_qty` 且 `filled_qty == requested_qty`（浮点容差 `max(1e-6, abs(held_qty)*1e-10)`）；对 buy 要求 `requested_qty == filled_qty`。数量检查在 `Position` 更新之前进行。
- D3：每日 `cash/value/account` 最大差 ≤ **CNY 0.01**；毛收益最大差 ≤ **1e-10**；**逐日**累计 fee/turnover（元）最大差 ≤ **CNY 0.01**，逐日 fee/turnover rate 最大差 ≤ **1e-10**。
- 任一检查不满足，抛出 `ValueError`，非零退出，不创建 `execution_audit_summary.json` 的虚假 PASS；输出必须为此前不存在的目录，禁止写入 `/vol` 和输入目录。
- 所有内部门禁 PASS 后，`f5_internal_evidence_gate=PASS` 只能证明**模拟路径内部证据一致性**，不证明真实可成交性。历史 ST、停牌、盘口、一字板、IPO 规则、容量、特征/标签时点与其余 9 phase **仍为 BLOCKED**；`overall=BLOCKED`。

## 实验环境重新复验（不重训、不回填、不改冻结果）

在 repo 根目录执行，`<...>` 为占位值：

```bash
python -m unittest discover -s tests -p "test_stage_b_f5_gates.py" -v
python -m audit.run_winner_phase0_execution_audit \
  --fixed-mode \
  --repo-dir . \
  --raw-dir audit/evidence/winner_phase0/raw \
  --report-file audit/evidence/winner_phase0_fixed/fixed_diagnostic_report.parquet \
  --decisions-file audit/evidence/winner_phase0_fixed/fixed_diagnostic_decisions.json \
  --market-file /path/to/read-only/fixed_market_data.parquet \
  --expected-report-sha256 0e9ba034b94d363117dbb924639178f26af4a116763e9868d9960f321b601048 \
  --expected-decisions-sha256 4694e67bd1a1870707d02d0eaf0e9bb0b29533cb209707e894d648fdd2a11063 \
  --expected-market-sha256 <SHA256_FIXED_MARKET_FROM_PRIOR_EXPORT_MANIFEST> \
  --provider-snapshot-file /path/to/read-only/original/provider_snapshot.json \
  --out-dir /path/to/new/audit-output/f5-hardened-winner-phase00
```

必须保存新 `execution_export_manifest.json`、`execution_audit_summary.json`、`calendar_compare.csv`、`decision_vs_signal.csv`、`execution_orders.csv`、`account_rebuild_daily.csv`；发布可公开的衍生核验结果时附这些文件的 SHA 与版本提交。不要提交原始私有 provider、Modal workspace/profile 名称或凭据。

### 验收计划

| Gate | 验收内容 | 当前状态 |
|---|---|---|
| H0 | 仅审计文件/CI/测试变化；冻结产物及生产配置字节不变 | 待 PR diff 核对 |
| H1 | 纯 Python hash/数量/门禁正向单测；反例：篡改行情、无先验 SHA、卖单偏差、部分成交、日历/账务/费率异常 | 代码及 CI 已提交，等待运行 |
| H2 | 授权环境固定轨 report/decision/**实际 market** SHA 全部固定；provider token 与 fingerprint 同源，任何不同必须非零退出 | 尚未复验 |
| H3 | 修复后 424/424 日、1696/1696 单 F5 全部门禁 PASS，费用/换手日度差不超容差，新审计 summary 使用明确来源哈希 | 尚未复验 |
| H4 | 新账本直接计算 Sharpe/CAGR 与独立报告确认一致；保留 F1 原停摆段 950 单的审计痕迹，无收益优化选择 | 尚未复验 |
| H5 | 历史 ST/真实停牌/开盘成交容量/20-session label 成熟，以及其他 9 个 phase | 仍 BLOCKED；不因本 PR 自动解除 |

**PR 合并不表示 H2–H5 自动通过。** 合并可接受为 fail-closed 审计工具升级；一切真实性和策略收益结论继续依赖外部证据。
