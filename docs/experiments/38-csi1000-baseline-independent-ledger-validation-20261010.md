# 38 — CSI1000 baseline 收益与相对优势的独立验证（2026-10-10）

> **研究审计，不是调参、选出最优策略或生产晋升。** 本 PR 新增独立账本、合成单测和 CPU 实验操作说明。没有重训、GPU、历史数据刷新或预填回放 PASS。务必在用户授权的**原始 Stage-B provider/snapshot** 环境运行。2025-01-02～2026-09-30 为已消耗测试区间，不得再次称为全新样本外。

## 1. 待回答的两个问题

1. **baseline 收益是否算准？** 首先验证冻结 corrected 报告能否从**交易日原始开盘/收盘价 + 冻结订单数量/方向**经独立的现金、调整后股数、费用、日终估值重新得出（不调用 Qlib Exchange、Account、backtest 或 portfolio_performance）；再做额外交易费用敏感性。仅账本算对不能证明当时真实可成交。
2. **baseline 是否最佳？** 只有第一项通过后，对同一冻结时间段按已固定的 worst-phase 相对超额 CAGR、median Sharpe，比对 baseline 与 Stage-B corrected 第二名 `c98856b4`。这**只能确认这两套固定候选在当前条件下的相对表现**，不能推出它是整个 CSI1000 最优算法，也不能用已消耗 tail 再调参。

禁止修改原模型、信号、成本、Rank、TopK/Drop2、测试窗口和 provider。在现有 55 条 corrected 结果中仅取 baseline 与 `c98856b4`，默认先跑 baseline phase0，再逐步扩展到 2×5；如有失败立即停止，检查原因，不批量掩盖。

## 2. 输入来源与严格身份

| 资源 | 必要性质 |
|---|---|
| `audit/evidence/stage_b_corrected_rerank/batch_result.json` | SHA256 固定为 `ce0075f5d8c73325a12a4ecdf8fa4be856443a6fc1bac933f3b82d5c5035ff1c`；其原始 snapshot token、provider fingerprint、55 完成数与 no-train 标签必须匹配 |
| corrected `fixed_diagnostic_report.parquet` / `fixed_diagnostic_decisions.json` | 每格文件 SHA 必须与上述批次中该 `candidate_id, phase` 的 `corrected_*_sha256` 匹配；不信任用户自报摘要 |
| 原 provider | `/vol/cn_data`（或单独只读副本），不能是公开 Qlib v1/v2 或当前更新过的数据 |
| 原 provider snapshot | `/vol/csi1000_stage_b/51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1/provider_snapshot.json`，原 token 和 fingerprint 双校验 |
| Qlib 用途 | **仅 `D.calendar` 与 `D.features`**，用明确的静态订单股票列表读 `$open/$close/$factor/$volume`，关闭 `disk_cache`；账本纯 Python 另算 |

固定 baseline ID `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`；第二名 `c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b`，phase `{0,4,6,10,15}`；交易日 424 天，初始 CNY 100m，买入费用 5bp、卖出 15bp、最低 CNY 5。

**底层语义**：保存的 `amount`/`deal_amount` 是 **Qlib adjusted-share units**；研究 provider 中的 `$open/$close` 为对应调整后价格，因此账本先用 `quantity * adjusted_open` 得成交金额，再用 `quantity * adjusted_close` 计日终市值；`factor` 用来与订单里保存的 factor 对照。不能把 amount 当作未经复权的 A 股整股数，也不能用 factor 再乘一次价格。所有订单须为 full fill，卖单须平掉账本内整个持仓；发现缺价、数量不一致、负现金、原始 SHA 不一致一律拒绝 PASS。买卖顺序采用冻结订单顺序，卖在前、买在后。

## 3. 运行方式

### A. 不需要 Modal 的最小单测（合成数据，不是原 provider 重放）

在本仓库的 Python 环境（安装 pandas）中：

```bash
python -m unittest discover -s tests -p 'test_verify_stage_b_fixed_ledger.py' -v
```

覆盖同价/价差成交额、独立现金及费用、持仓、市值、篡改原账、缺价、factor 不符、部分成交、错误卖出、订单日期漂移及 SHA 校验失败。通过只表示实现的**合成门禁**有效，不表示 baseline 实测通过。

### B. 已有隔离 Qlib + 原 provider 的 CPU 环境（不使用 Modal 也可）

```bash
# 路径由授权实验环境替换，原 provider/snapshot 严格只读。
python -m audit.verify_stage_b_fixed_ledger \
  --provider-uri /path/to/original/cn_data \
  --provider-snapshot /path/to/original/csi1000_stage_b/provider_snapshot.json \
  --candidate baseline --phases phase0 \
  --output-dir /tmp/csi1000-ledger-baseline-p00

# 仅在 phase0 结果和缺价原因审查通过后，新增全量诊断输出；
# 全部 10 格仅做一次静态订单股票行情批量读取。
python -m audit.verify_stage_b_fixed_ledger \
  --provider-uri /path/to/original/cn_data \
  --provider-snapshot /path/to/original/csi1000_stage_b/provider_snapshot.json \
  --candidate both --phases all \
  --output-dir /tmp/csi1000-ledger-baseline-vs-runnerup-10cells
```

`--output-dir` 必须是全新目录，并且不可位于仓库、provider、snapshot 或 `/vol` 下。不覆盖、删改、重写原始 report/decision/signal，不运行模型；如果 provider snapshot 缺失或与冻结值不符，标记 **BLOCKED**，绝不能自行重建/更新原 provider 以“解锁”审计。

### C. Modal CPU 环境（实验人员手动启动，无部署）

你已使用 Modal 成功建立过隔离实验容器；本次继续使用同一**授权账户**，但必须挂载**原始**实验 provider，不可复用此前 CSI500 的官方 v1 包。创建一个**一次性、无 GPU、不部署、单 worker** 的 Modal CPU 实验容器，将当前 PR checkout 放入容器的 `/root/qlib`，在容器内安装本仓库匹配的 Qlib、pandas、pyarrow；为防 Qlib 版本飘移记录 commit、Python 和 Qlib 包版本。

| 资源参数 | 建议 | 理由 |
|---|---:|---|
| CPU | `4` 个 Modal CPU 单位 | 数据读取、Parquet、Python 账本，不运行 LightGBM |
| RAM | `16 GiB` | 缓冲指数历史行情及 10 格订单；如超额先测量 |
| GPU | **0** | 完全无模型训练 |
| 并行 | **1 个容器 / 1 个进程** | 同时处理十格、股票去重、`D.features` 仅取一次；10 容器会重复 I/O |
| 超时 | 60 分钟 | 作为故障/预算上限；不要无限重试 |

在已授权、代码已挂载的 Modal 容器内，以仅执行代码读取的方式挂载现有 `qlib-cn-data` Volume（`create_if_missing=False`），必要的输入路径是 `/vol/cn_data` 和 `/vol/csi1000_stage_b/<token>/provider_snapshot.json`。不要声明挂载是硬只读：Modal Volume 通常具备写能力，因此脚本禁止输出写入 `/vol`、关闭 Qlib `disk_cache`，且**不调用 `vol.commit()`**。最好使用原始 provider 的独立只读副本；若这点做不到，保证没有其他写任务，并只按上述命令运行审计器。

```bash
# 以下命令在一次性 Modal 容器内部执行，不是本地 shell 启动 Modal 的 CLI。
cd /root/qlib
python -m unittest discover -s tests -p 'test_verify_stage_b_fixed_ledger.py' -v
python -m audit.verify_stage_b_fixed_ledger \
  --provider-uri /vol/cn_data \
  --provider-snapshot /vol/csi1000_stage_b/51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1/provider_snapshot.json \
  --candidate baseline --phases phase0 \
  --output-dir /tmp/csi1000-ledger-baseline-p00

# 人工审查首轮 JSON/日志，确认可以扩展后再执行：
python -m audit.verify_stage_b_fixed_ledger \
  --provider-uri /vol/cn_data \
  --provider-snapshot /vol/csi1000_stage_b/51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1/provider_snapshot.json \
  --candidate both --phases all \
  --output-dir /tmp/csi1000-ledger-10cells
```

从隔离容器取回 **派生的** `independent_ledger.json` 及脱敏运行日志，不上传原市场行情/凭据/整卷数据。若一次执行超出预算，不要自动扩成 10 worker；仅在实测证明需要时考虑将 baseline 与 runnerup 分为最多两个 worker（各 2 CPU、12 GiB），但会重复读取 provider。注意 CPU 并行度控制与 Qlib 的交易执行逻辑彼此独立。

## 4. 验收证据、结果解释与暂停线

程序只输出 `independent_ledger.json`（每格只含衍生数值、缺失/异常订单摘要、原始输入 SHA），不包含 provider 价格序列、凭据或真实账户信息。**尚未在真实 provider 上执行**：代码审查和合成测试不可写为 `PASS_RESEARCH_ACCOUNTING_ONLY` 的实测结果。

| 验证层次 | PASS 的必要条件 | 不能证明什么 |
|---|---|---|
| 文件身份 | 原批次结果和每格 corrected report/decisions SHA 一致、原 snapshot 与 fingerprint 一致 | 原价格数据库逐文件正确或历史 PIT 合法 |
| 账本 | 每天现金、持仓估值、总账户、累计费用与总成交额差值 ≤ ¥0.01；净收益与费率/换手差 ≤ 1e-10；计算 Sharpe/CAGR/MDD 与保存指标差 ≤ 1e-6 | 外部券商逐笔成交、市场冲击/盘口 |
| 订单缺失与异常 | 任意缺交易开盘价或持仓收盘价、数量/因子不符、负现金必须拒绝 PASS；不存在静默补价 | 有效停牌日仍需独立证明与合理处理；缺 volume 作为警告，不代表已确认外部成交 |
| 成本敏感性 | 报告 0/5/10/20bp **冻结名义成交额上额外扣费**的净值/Sharpe/CAGR 变化，且明确不是成交重新模拟 | 不代表真实滑点/成交量/开盘成交价格偏差 |
| 相对比较 | 仅在 2×5 格完整且账本一致时，列出每候选五阶段最差 relative-excess CAGR 和 Sharpe 中位数 | 不认证 baseline 为 CSI1000 全模型空间最优，也不是新 OOS |

**所有通过后也只是 `PASS_RESEARCH_ACCOUNTING_ONLY`。** `external_fill_liquidity_st_ipo_pit_maturity` 始终为 `BLOCKED_NOT_TESTED`。真实开盘集合竞价成交、历史 ST/涨跌停、IPO 豁免、停牌、volume 单位、点时成分及标签可得性仍需要独立外部证据。`D.features` 的同 provider 价格不构成不同供应商的外部价格交叉验证。基准 `bench` 日收益此阶段仍来自冻结报告，程序会标记 `BLOCKED_SAVED_BENCH_RETURNS_USED`。若账本失败，首先保存差异和最早异常日期，禁止提升策略、自动修复历史报告或改变容差。

这次**不增加新候选、不重训、不改生产代码**；第三方发行数据不可得就明确 BLOCKED。若结果将用于公开 PR，只提交最小汇总与 hash，不提交全量市场行情、私有 Volume 凭据。
