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

### C. Modal CPU：从本地一条命令启动（推荐）

**此 PR 内已经提供可运行的入口**：`audit/modal_stage_b_ledger.py`。你只需在自己的 Modal 已授权账户、可访问 `qlib-cn-data` 的机器上执行 `modal run`，**不需要手写 Modal 函数，也不需要先进入容器 shell**。启动器使用 `modal.Volume.from_name("qlib-cn-data", create_if_missing=False)`，在一次性容器内读取原始 `/vol/cn_data` 和冻结 snapshot；只向容器 `/tmp` 写临时派生文件，将结果通过 Modal 函数返回**发起命令的本地机器**。如果审计命令返回非零，启动器也会尽可能把 `independent_ledger.json` 或 `audit_failure.json` 的诊断信息传回本地，然后以失败状态退出。**不会修改/刷新原始行情、提交 Volume、部署服务或启动训练。**

`modal run` 前，在**本机仓库根目录**准备**你将要合并的同一版代码**：

```bash
# 已完成该 PR 合并后执行：
git fetch origin
git checkout main
git pull --ff-only
git rev-parse HEAD
test -f audit/verify_stage_b_fixed_ledger.py
test -f audit/modal_stage_b_ledger.py
test -f audit/evidence/stage_b_corrected_rerank/batch_result.json

# 若合并还未完成，需要预试 PR 的内容，则不要误用旧 main：
# git fetch origin audit/csi1000-fixed-ledger-baseline-vs-runnerup-20261010
# git checkout --detach origin/audit/csi1000-fixed-ledger-baseline-vs-runnerup-20261010

python -m pip install --upgrade modal
modal --version
# 首次使用请按你自己 Modal 账户的连接流程完成授权；
# 不要在日志或 GitHub 提交 token/密钥。
```

使用同一仓库根目录（`modal run` 的本地构建上下文为 `.`），先跑**唯一的 baseline phase0**：

```bash
modal run audit/modal_stage_b_ledger.py \
  --candidate baseline --phases phase0 \
  --output /tmp/csi1000-baseline-phase0-ledger.json
```

启动器为你准备 **Python 3.11 / pandas / pyarrow / Qlib** 镜像，然后启动一个一次性 CPU 容器；`--output` 指向**本地**未存在的文件，其父目录必须已存在。执行成功时该文件包含 `cells[0]`、`ledger`、`max_abs_diff`、`metrics_comparison` 和额外成本敏感性。执行失败时仍应查看同一个输出 JSON 的 `status`、`reason` 或 `diagnostic_output_tail`；**没有 JSON 的情况**（例如 Modal 尚未构建完镜像或身份授权失败），请保存本地 CLI 错误日志，标记 `BLOCKED_ENVIRONMENT`。请把文件原样保存在本地用于审计，但分享前需脱敏日志。

检查 phase0 结果，不要只看 `modal run` 的退出码：

```bash
python - <<'PY'
import json
p = "/tmp/csi1000-baseline-phase0-ledger.json"
r = json.load(open(p))
print("status =", r.get("status", "NO_EXPLICIT_STATUS"))
print("worker_exit =", r.get("modal_command_exit_code"))
print("all_research_accounting_pass =", r.get("all_research_accounting_pass"))
for c in r.get("cells", []):
    print(c["candidate_id"][:16], c["phase"], c["ledger"],
          "fail_fields=", c["fail_fields"], "first_diff_date=", c["first_diff_date"])
    print("metric_diffs =", c.get("metrics_comparison"))
if "reason" in r:
    print("reason =", r["reason"])
PY
```

只有当 `worker_exit==0`、`all_research_accounting_pass==true` 且 `cells[0].ledger == PASS_RESEARCH_ACCOUNTING_ONLY`，并且人工核实缺价/成交警告与口径解释后，才能继续。再运行**两个候选、五个 phase、共十格**：

```bash
modal run audit/modal_stage_b_ledger.py \
  --candidate both --phases all \
  --output /tmp/csi1000-baseline-runnerup-10cells.json
```

两个命令每次只开**一个** worker，十格共享一轮历史行情批量读取。不要同时开十个容器。十格 `all_research_accounting_pass==true` 后才能比较 `cohort_summaries`；前者若失败，后者不启动。结果绝不能解读为新样本外最优策略认证。

| 容器资源 | 固定或建议 | 说明 |
|---|---:|---|
| Modal Worker | **1** 个、按需 | 两个命令分开顺序运行，未启用自动重试 |
| CPU / RAM | **4 CPU / 16 GiB** | 批量 Qlib 特征读取 + Python 账本，不训练模型 |
| GPU | **0** | 无 LightGBM fit、无 GPU |
| 超时 | 最长 **60 分钟** | 常规不应接近此上限；异常时停止并检查 |
| 输入 Volume | `qlib-cn-data` | 已存在、授权读取；使用 `create_if_missing=False` |
| 输出 | 本地 `/tmp/*.json` | 临时容器中的派生结果通过返回值带回；不得放到 `/vol` |

**关于 Volume 权限**：Modal 挂载的 Volume 未必是操作系统级只读；这里使用**操作上的只读策略**——Qlib `disk_cache=False`，审计器禁止输出落到 `/vol`，启动器不调用 `vol.commit()`。如果你的安全要求必须是硬只读，应先使用授权的独立只读快照/副本，并相应调整入口；切勿把写权限存在误称为物理只读。不得上传原始行情/账户凭据到公开 GitHub。另请保留 `git rev-parse HEAD`、`modal --version`、完整运行状态、脱敏错误信息和本地结果 JSON。

## 4. 验收证据、结果解释与暂停线

审计核心程序成功时输出 `independent_ledger.json`；预检阶段失败时尽量在隔离输出目录保存 `audit_failure.json`，Modal 入口会将其内容作为本地 JSON 返回（其中只含衍生数值、有限的失败上下文和原始输入 SHA），不包含 provider 价格序列、凭据或真实账户信息。**尚未在真实 provider 上执行**：代码审查和合成测试不可写为 `PASS_RESEARCH_ACCOUNTING_ONLY` 的实测结果。

| 验证层次 | PASS 的必要条件 | 不能证明什么 |
|---|---|---|
| 文件身份 | 原批次结果和每格 corrected report/decisions SHA 一致、原 snapshot 与 fingerprint 一致 | 原价格数据库逐文件正确或历史 PIT 合法 |
| 账本 | 每天现金、持仓估值、总账户、累计费用与总成交额差值 ≤ ¥0.01；净收益与费率/换手差 ≤ 1e-10；计算 Sharpe/CAGR/MDD 与保存指标差 ≤ 1e-6 | 外部券商逐笔成交、市场冲击/盘口 |
| 订单缺失与异常 | 任意缺交易开盘价或持仓收盘价、数量/因子不符、负现金必须拒绝 PASS；不存在静默补价 | 有效停牌日仍需独立证明与合理处理；缺 volume 作为警告，不代表已确认外部成交 |
| 成本敏感性 | 报告 0/5/10/20bp **冻结名义成交额上额外扣费**的净值/Sharpe/CAGR 变化，且明确不是成交重新模拟 | 不代表真实滑点/成交量/开盘成交价格偏差 |
| 相对比较 | 仅在 2×5 格完整且账本一致时，列出每候选五阶段最差 relative-excess CAGR 和 Sharpe 中位数 | 不认证 baseline 为 CSI1000 全模型空间最优，也不是新 OOS |

**所有通过后也只是 `PASS_RESEARCH_ACCOUNTING_ONLY`。** `external_fill_liquidity_st_ipo_pit_maturity` 始终为 `BLOCKED_NOT_TESTED`。真实开盘集合竞价成交、历史 ST/涨跌停、IPO 豁免、停牌、volume 单位、点时成分及标签可得性仍需要独立外部证据。`D.features` 的同 provider 价格不构成不同供应商的外部价格交叉验证。基准 `bench` 日收益此阶段仍来自冻结报告，程序会标记 `BLOCKED_SAVED_BENCH_RETURNS_USED`。若账本失败，首先保存差异和最早异常日期，禁止提升策略、自动修复历史报告或改变容差。

这次**不增加新候选、不重训、不改生产代码**；第三方发行数据不可得就明确 BLOCKED。若结果将用于公开 PR，只提交最小汇总与 hash，不提交全量市场行情、私有 Volume 凭据。
