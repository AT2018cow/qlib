# 生产与研究的行情数据 lineage（overlay 引入后）

> 初版：2026-11-11；状态核对：2026-11-12
>
> 适用范围：生产 v26（App commit `1b5ef71ac`）与当前研究入口；部署和验收状态见[生产状态记录](../07-code-review/2026-11-12-production-deployment-status.md)。
> 性质：数据源与 lineage 说明，不是新的回测、策略选择或生产切换批准。  
> 相关文档：
> - `quant/docs/07-code-review/2026-11-10-overlay-primary-implementation-handoff.md`
> - `quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md`
> - `quant/docs/07-code-review/2026-11-09-production-workspace-migration.md`
> - `quant/docs/00-strategy-handbook/STRATEGY_ATLAS.md`

## 1. 结论摘要

引入 overlay 之后，**生产与研究不是同一条行情 lineage**：

```text
生产发布（信号 / website / paper 日常推进）
    → 自建 overlay archive（cn_data_overlay）
    → 冻结锚点 2026-11-06，之后 Sina + BaoStock + CSIndex
    → 每个交易日一个内容寻址 archive + PASS receipt

研究（回测 / 参数搜索 / 策略择优）
    → 默认仍是 chenditc 的 cn_data（qlib-cn-data Volume）
    → 可能是冻结的 chenditc snapshot，也可能是 latest release
    → 只有显式传入 overlay provider 时，研究才使用 overlay
```

因此：

- **不能**把研究回测结果直接标成“overlay 数据结果”或“生产验证”。
- **不能**假设研究会自动读取生产 `cn_data_overlay`。
- **不能**把 website/paper 的生产收益当作研究策略选择的独立证据。
- 研究若要复现生产，必须显式传 overlay archive（或 receipt 指向的本地物化目录），并记录 provider digest/receipt。

## 2. 两条 lineage 与 cutover 边界

### 2.1 生产 overlay lineage

- 冻结锚点：`cn_data_overlay/2026-11-06`。
- 该锚点的 base 是 chenditc `2026-11-04` release（archive SHA256 `dc917293925635189e982f47d35b960c3db8a9c3f11312c937673c8afdec21aa`），11-05/11-06 由 overlay 管道追加。
- 之后每个交易日：
  - `ensure_overlay` / `provider_build.build_provider_archive` 从上一 PASS archive 本地解包、追加、gate、qlib smoke；
  - 发布 `cn_data_overlay/archives/<date>/<sha256>.tar.gz` + `receipt.json`；
  - signal/website 读取 archive 并本地 materialize。
- 生产 `cn_data_overlay/2026-11-10` 已封存为 archive `658555ec103915ccac5caa7521b218f273abc5c0c862eb10c76ed48fea22ddda`，receipt `fa1744e2…`。
- 生产 `qlib-prod-data/cn_data` 是迁移遗留目录，不是 18:00 日更链路的数据源；不要把它当作生产 overlay。

### 2.2 研究 chenditc lineage

研究默认 Volume 是 `qlib-cn-data`，目录 `cn_data`。它来自 chenditc/investment_data 的 qlib 格式 release。研究内部又分三类：

| 研究类型 | 代表入口 | 数据源与冻结方式 |
|---|---|---|
| 冻结 chenditc snapshot | `research/stage_ab/csi1000_stage_b.py`、`layered_search`、`csi300_csi500` | `qlib-cn-data/cn_data`，带 `.chenditc` marker、provider fingerprint / snapshot token；Stage-B `prepare_stage_b()` 在 marker 缺失时**拒绝下载/刷新** |
| chenditc latest | `research/pre_tuner/freq_experiment.py`、`research/current_window/current_window_modal.py`、`stage_ab/csi1000_tuner.py::prepare(force=True)` | `_ensure_data()` 或 `_load_latest_to_local()` 下载 chenditc `latest` release；默认不是 pinned |
| 显式 provider | `research/regime_reselection/prelive_revalidation.py`、`october_backfill_modal.py` | 调用者通过 `--provider` / `asset` 传入；可以是 chenditc URL、本地路径或 overlay archive |

### 2.3 时间边界

| 日期 | 事件 |
|---|---|
| 2026-11-04 | 冻结 chenditc base release（`dc917…`） |
| 2026-11-06 | 首个 overlay snapshot；生产开始以自建 provider 为锚点 |
| 2026-11-09 | 生产 workspace 迁移；日常信号/网站明确使用 overlay，`cn_data` 基础数据留给研究/训练重新下载 |
| 2026-11-10 | overlay 11-10 封存为 archive `6585…` |
| 2026-11-11 | 生产 v20；研究 `qlib-cn-data/cn_data` 仍是 chenditc lineage |
| 2026-11-12 | 11-11 overlay archive 补跑 PASS；两个生产 App 部署 v26；研究默认 lineage 未切换 |

注意：**2026-11-06 之前的研究结果与生产账户历史可能来自 chenditc 或更早的 provider**；website v20 的全量 replay 是 overlay 11-10 provider 的完整重放，但 paper 账户的历史状态仍保留其当时 journal/provider lineage。以 `publication_journal`、`provider_identity`、website `provider_source` 为准，不能仅按日期推断。

## 3. 代码级数据源映射

| 消费者 / 入口 | 默认数据源 | 是否使用 overlay | 备注 |
|---|---|---|---|
| `daily_standalone`（由 18:00 协调器调用） | `cn_data_overlay` archive/directory | **是，唯一** | `_select_overlay_provider`；缺失即报错，不回退 chenditc |
| `website_simulation_modal`（生产 website） | `cn_data_overlay` archive/directory | **是，唯一** | `latest_asset` / `_latest_overlay_asset`；跨源拒绝增量 |
| `ensure_chenditc_provider` | chenditc release | 否 | 仅手动历史恢复，无 schedule、无日常调用 |
| `research/stage_ab/csi1000_stage_b.py` | `qlib-cn-data/cn_data` | 否 | `.chenditc` marker；缺失时 fail-closed，不刷新 |
| `research/stage_ab/csi1000_tuner.py` | `qlib-cn-data/cn_data` | 否 | `prepare(force=True)` 下载 chenditc latest |
| `research/layered_search/*` | `SOURCE_VOLUME=qlib-cn-data` 的 `cn_data` | 否 | 使用冻结 provider fingerprint / snapshot token |
| `research/csi300_csi500/*` | `SOURCE_VOLUME=qlib-cn-data` 的 `cn_data` | 否 | 同上 |
| `research/pre_tuner/freq_experiment.py` | `/vol/cn_data` | 否 | `_ensure_data`；`.chenditc` marker |
| `research/current_window/current_window_modal.py` | `/tmp/cn_data` 或 `_load_latest_to_local()` | 否 | 优先已有本地副本，否则 chenditc latest |
| `research/regime_reselection/prelive_modal.py` | `qlib-cn-data` 只读挂载 | 否（默认） | `prelive_revalidation --provider` 可显式传 overlay |
| `research/regime_reselection/october_backfill_modal.py` | 显式 `asset` | 可选 | `url`（chenditc）/ `local_path` / `archive_path`（overlay） |
| `backfill_signals` / `daily_signal` / `daily_signal_cpu` | `_load_latest_to_local()` / `_ensure_data()` | 否 | 研究/旧入口，不在 18:00 日常调度路径 |
| `ops/modal_data_overlay_canary.py` / `dry_run.py` | 冻结 chenditc 11-04 base + overlay | 是（验证工具） | 临时/隔离验证，不是生产调度，也不是策略研究 |

## 4. 研究复现生产时的强制规则

### 4.1 默认规则

1. **研究默认是 chenditc `cn_data` lineage**。研究结果必须标注 `data_source=chenditc/cn_data`，并尽量记录：
   - release tag / published_at；
   - provider file manifest SHA256 / fingerprint；
   - calendar SHA256 / calendar_end；
   - 是否使用 `.chenditc` marker 或 snapshot token。
2. `_ensure_data()` / `_load_latest_to_local()` 的 `latest` 不是 pin。研究要可复现，必须固定 release 或使用冻结 provider manifest/snapshot；不能只写“用了 chenditc”。
3. **不要在同一回测、同一账户或同一比较表中拼接 chenditc 和 overlay 的收益/持仓/信号**。website 的 `_incremental_prior` 已按 `provider_source`、parent digest、calendar_end 拒绝跨源拼接；研究脚本也必须遵守同样的 lineage 边界。
4. 研究输出必须与生产 website/paper 输出分开标注。生产 website/paper 是 overlay 账户证据，不是策略研究 OOS 或实盘成交认证。

### 4.2 显式使用 overlay 复现生产

研究要复现生产，使用以下任一方式：

- `prelive_revalidation.py`：
  ```bash
  python .../prelive_revalidation.py \
    --provider <materialized-overlay-provider> \
    --provider-manifest <frozen-file-manifest> \
    --register <register.json> ...
  ```
  其中 `<materialized-overlay-provider>` 应来自生产 archive receipt 的 `archive.path` 物化结果，并记录 archive SHA256 / receipt SHA256。
- `october_backfill_modal.py`：
  - `asset.archive_path` 指向 overlay archive；
  - 或 `asset.local_path` 指向已物化的本地 provider；
  - `asset.url` 仅用于 chenditc 或明确登记的外部 release。
- 其他研究脚本如果只接受 `provider_uri`，先物化 overlay archive 到本地/研究 Volume，再把 `provider_uri` 指向该目录；不要直接指向 `cn_data_overlay` 目录做远程小文件读取。

### 4.3 记录字段建议

研究结果 JSON 至少记录：

```json
{
  "data_source": "chenditc/cn_data" | "overlay/archive",
  "provider_calendar_end": "YYYY-MM-DD",
  "provider_calendar_sha256": "...",
  "provider_archive_sha256": "...",
  "provider_receipt_sha256": "...",
  "chenditc_release": "..." ,
  "chenditc_published_at": "...",
  "code_source_sha256": "..."
}
```

字段按实际来源填写；未知字段不要伪造。overlay 结果应带 receipt 哈希，chenditc 结果应带 release/fingerprint。

## 5. 已知差异与风险

1. **复权坐标不同**：生产 overlay 的冻结锚点来自 chenditc 11-04，之后用 Sina raw/hfq + BaoStock preclose 续接；研究 `cn_data` 是另一份 chenditc release。两者在 11-04/11-06 之后不是同一组日线行。
2. **新成分历史回填**：overlay 对新成分使用 BaoStock full history + 首根 bar 归一化；这与 chenditc 历史中老股票特有的公司行为处理不完全一致。manifest 已记录 `history_backfill`，不要把回填历史当作 chenditc 原始历史。
3. **chenditc latest 漂移**：Stage-A/tuner/pre_tuner/current_window 可能重新下载 latest；冻结 snapshot 的 Stage-B/layered/csi300-csi500 则依赖 `.chenditc` marker 和 provider fingerprint。研究结论必须区分这两类。
4. **website/paper 历史跨 cutover**：website v20 的完整 replay 是 overlay provider；paper 账户历史状态保留原 journal/provider lineage。不要把两者混为“全历史 overlay”或“全历史 chenditc”。
5. **研究结论不能替代生产验证**：Stage-A/B、P0/L1–L4 的收益、Sharpe、回撤、换手和成本压力结果均基于研究 provider；生产 website/paper 的 NAV 只证明 overlay 账户路径，不证明研究策略的 OOS 或实盘可执行性。
6. **生产 overlay 的 archive 不是研究数据仓库**：研究应使用 materialize 后的本地目录或研究 Volume 副本；不要直接让 Qlib 读 Volume 上的 36k 小文件，也不要将研究输出写回生产 `cn_data_overlay`。

## 6. 只读核对命令

以下命令仅用于核对，不写生产数据；使用已授权的本地 Modal profile：

```bash
# 生产 overlay 链
modal volume ls <production-volume> cn_data_overlay/archives
modal volume get <production-volume> cn_data_overlay/archives/2026-11-10/receipt.json -
modal volume ls <production-volume> cn_data_overlay/archives/2026-11-10

# 研究 chenditc Volume
modal volume ls qlib-cn-data cn_data
modal volume get qlib-cn-data cn_data/calendars/day.txt -
```

代码核对：

```bash
rg -n "_select_overlay_provider|_load_latest_to_local|_ensure_data" quant/code/production/modal_qlib_cn_a10g.py
rg -n "VOL_NAME = \"qlib-cn-data\"|SOURCE_VOLUME = \"qlib-cn-data\"|DATA_DIR = VOL_ROOT / \"cn_data\"" quant/code/research
rg -n "archive_path|local_path|provider" quant/code/research/regime_reselection/october_backfill_modal.py
```

## 7. 后续治理建议

- 在所有研究结果写入统一 `data_source` 字段；对 chenditc 结果记录 release/fingerprint，对 overlay 结果记录 receipt。
- 在 `STRATEGY_ATLAS.md` 和研究报告中增加“数据源/lineage”列，避免把不同 provider 的指标放在同一排行榜。
- 若要做“研究结论迁移到生产 overlay”的验证，单独建立固定输入、固定信号、固定账户的比较实验；不要复用 chenditc 的历史 NAV 直接对照。
- 研究默认路径是否切换为 overlay，是一个需要单独批准的治理决策；本文只记录现状，不改变研究默认数据源。

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-11 | 新建本文；记录 v20 生产 overlay 与 chenditc 研究 lineage 的差异、代码入口、复现规则和风险 |
| 2026-11-12 | 核对 v26 已部署状态与 18:00 调度；研究与生产的数据 lineage 边界不变 |
