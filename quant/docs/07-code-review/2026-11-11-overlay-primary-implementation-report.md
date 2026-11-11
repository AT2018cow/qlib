# 唯一自建行情管道：实施与验收记录

> 日期：2026-11-11  
> 实施基线：`83f710a76`（交接文档 `2026-11-10-overlay-primary-implementation-handoff.md`）  
> 状态：本地/隔离验收完成；生产部署与首次切换验收见第 8 节。本文只记录真实命令、测量和产物，不把单元测试数量当作生产稳定证明。

## 1. 实施前重新核对（与交接文档的差异）

| 项目 | 交接文档 | 实际核对结果 |
|---|---|---|
| Git HEAD | `83f710a76` | 一致；工作树仅有交接文档未跟踪 |
| `qlib-prod-daily` | v15 @ `83f710a76` | 一致（2026-11-10 21:57 部署） |
| `qlib-prod-website` | v15 @ `83f710a76` | 一致 |
| 06:00 / 07:30 cron | 存在 | 一致 |
| 旧晚间入口 | “已无 schedule” | **不一致**：部署 v15 仍有 `daily_evening` 18:00、`daily_evening_retry` 19:00 两个 cron；源码在 `4c317a246` 已移除 schedule，但 Modal 保留了旧触发器。2026-11-10 18:00 日志确认 `_run_evening_cycle` 实际运行并被抢占。 |
| 11/10 晚间周期 | 20:06 完成 | 一致；实际是旧晚间入口/手动入口触发 overlay 构建后发布 11/11 信号 |
| 冻结 base | `/vol/base/chenditc_bin` | **该目录已不存在**；11-06 manifest 仍指向它。实际可用锚点是 `cn_data_overlay/2026-11-06`。 |
| 快照规模 | 未记录 | 每个目录快照 **36,051 个文件 / 574 MB**（3,607 个目录） |
| `preopen_status` | 未说明 | Volume 上不存在；v15 尚未经历过 06:00 preopen |

因此本轮不能沿用“18/19 点晚间入口已无调度”的假设；第 4 阶段必须显式删除/重命名旧函数并验证部署后的 cron 清单。

## 2. 数据存储与 I/O 测量（真实 Modal Volume）

在只读生产 Volume + 隔离 probe Volume 上测量（`quant/code/ops/overlay_primary_probe.py`）：

| 操作 | 结果 |
|---|---|
| 遍历 36,051 个文件（仅 `stat`） | 35–42 s |
| 顺序读取 240 个 bin | 冷缓存 0.54 MiB/s；热缓存 25 MiB/s（逐文件延迟主导） |
| 从目录顺序构建 `tar.gz` | 冷缓存 1,082 s；热缓存 75 s |
| 单文件顺序读取 443 MB archive | **1.47 s（287 MiB/s）**，SHA256 校验通过 |
| 解压 archive 到本地 | **17.7 s** |
| 并行（32 线程）封存 11-10 目录 | **213 s**（对比顺序 >58 min 未完成） |
| 18 只股票真实重建 11-10 | 47.4 s 总耗时，build 23.2 s，**0 行差异** |

结论：日常构建和消费都应使用“单个 archive + 本地解包”，不再把 36k 文件目录复制到 Volume 或让 qlib 直接读 Volume。首次迁移需要一次 11-10 封存，并行读取后约 3.5 分钟。

## 3. 阶段 1：稳定性、可观测性和单一入口

代码：
- `quant/code/ops/resource_sampler.py`：进程树 RSS/PSS/CPU + cgroup v2（可读时）采样；不可读指标明确记为 `unavailable`，异常路径仍产出 summary。
- `quant/code/production/daily_pipeline.py`：可选 sampler，按阶段记录 wall/CPU；阶段失败仍保留其他阶段结果。
- `quant/code/production/modal_qlib_cn_a10g.py`：
  - `daily_standalone`、`ensure_overlay`、website `build_market` 均写资源/失败记录。
  - `_preopen_marker_valid` 校验 provider digest、journal `VOLUME_COMMITTED`、paper state 哈希、GitHub artifact 哈希、website receipt、代码 source SHA。
  - `daily_readonly_check`：09:00、只读 Volume、无 secret，任何缺失/不一致直接 raise。
  - `daily_cron` 成为唯一协调器；`daily_preopen_retry` 转发到同一函数（平台 `max_containers=1` 串行）。
  - `daily_cron_manual` / 晚间入口转发到 `daily_cron`，不再直接执行重活；旧函数改名为 `daily_evening_manual` / `daily_evening_retry_manual`，无 schedule。
- `quant/code/ops/website_simulation_modal.py`：`status` 只读 receipt 查询；`build_market` 失败资源记录。

验证：
- `pytest -q quant/tests`：**982 passed, 2 skipped, 50 subtests passed**（本地）。
- `test_daily_pipeline.py` 覆盖：marker 不能伪造、失败写 FAILED marker、provider 单一入口、09:00 调度、archive materialize、缺失 provider fail-closed。
- `test_resource_sampler.py`：进程树/cgroup 聚合、不可读指标、异常路径。

## 4. 阶段 2：采集、缓存和本地构建

### 4.1 Sina raw/hfq 复用

`data_ingest/sina.py`：
- 每只股票 **1 次 history（klc2）+ 1 次 hfq factor**，不再为 raw/hfq 各下载/解码一次历史；跳过流通股本请求。
- 使用 AkShare 1.18.94 的公开 decoder/URL，版本不匹配自动回退到 public API（1 次 history + 1 次 factor）。
- 每请求 connect/read timeout，符号级重试/退避，整批 deadline；超时保留已完成结果。

真实等价性（本地，4 只股票 × 7 日，逐字段）：
- 与 `ak.stock_zh_a_daily(adjust="")` / `adjust="hfq"` **完全相等**（open/high/low/close/volume/amount）。
- 单股耗时：fast 0.17–0.34 s，public 0.97–1.64 s。

### 4.2 断点缓存

`data_ingest/cache.py`：
- `ingest_cache/<trade_date>/<contract>/`：memberships、`sina-*.parquet` 批次、errors/stats、`baostock.parquet`、`history.json.gz`。
- 每批完成后 commit；重试只重取缺失 symbol/date；空响应不写成完整数据。
- `test_ingest_cache.py` 覆盖批次合并、后写覆盖、空批次不持久化、损坏 parquet、membership/history 往返。

### 4.3 BaoStock 有界串行

`data_ingest/baostock_source.py`：
- `isolate=True` 时在 killable 子进程中运行，symbol 级增量文件；超时终止并保留已完成结果。
- 全历史 `fetch_history` 用于新成分回填；BaoStock `preclose` 是公司行为精确来源。
- 生产 `ensure_overlay` 使用 `BaoStockSource(isolate=True, deadline_seconds=600)`。

### 4.4 成分与退指持仓

- `OverlayPipeline` 复用同一次 build 的 CSIndex 结果（两次 fallback 不重复下载）。
- membership 滞后回退仅在“官方快照成员 == base PIT 成员”且单日缺口时允许；多日缺口/成员不一致直接 fail-closed（`membership_policy` 记录在 manifest）。
- `ensure_overlay` 汇总 paper 持仓/待执行 + website 最新 `final_state.json` 持仓，作为 `extra_symbols`；持仓股票缺失报价会由 gate 的 `coverage.required_extras` 拦截。
- 新成分无 frozen features 时，用 BaoStock full history + preclose 按 provider 的“首根 bar close=1.0”约定回填；manifest 记录 `history_backfill` 来源/锚点，不静默排除。

约定验证（probe）：
- provider `close = raw × factor`；`factor` 是在该股票 provider 首根 bar 归一化为 1.0 的前复权因子，不是 `hfq/raw` 的今日归一化。
- BaoStock `adjustflag=3 + preclose` 重建与 frozen bins 的误差：多数 0.01–0.04%；Pudong Bank/Ping An 等老股票在 2005–2010 有一次未捕获的公司行为，导致之后恒定 9–23% 偏移。新成分没有 provider 历史，采用同一锚点约定，并在 manifest 记录来源；该限制已在文档中说明，未用于改写已冻结历史。

### 4.5 archive store

`data_ingest/store.py`：
- 确定性 `tar.gz`（mtime=0、uid/gid=0、排序、gzip mtime=0），同时记录 archive sha256 与 provider content sha256（tree hash）。
- `archives/<date>/<digest>.tar.gz + receipt.json + manifest.json + calendar.txt + latest.json`；receipt 是唯一 commit point。
- `materialize` 校验 size/SHA/manifest/calendar 后本地解包；损坏 archive 拒绝。
- `seal_directory_snapshot` 一次性迁移旧目录，不修改目录本身。
- `prune_archives` 默认 dry-run，只处理 archive，不删除冻结目录。

### 4.6 本地构建

`production/provider_build.py`（Modal-free，生产 `ensure_overlay` 与隔离 probe 共用）：
1. 解析/封存 exact snapshot；
2. 解析 base（archive 优先）；
3. 本地解包 base；
4. `OverlayPipeline` 采集 + gate + qlib smoke；
5. 本地生成 archive；
6. 上传 staging → rename → receipt → latest；
7. 失败写 `provider_builds/<date>/failed-*.json` 后 fail-closed。

真实隔离复现：18 只股票、真实 Sina/BaoStock、真实 gate/qlib smoke、与 frozen 11-10 逐字段比较 **0 mismatch**；archive 大小 3 MiB（样本），耗时 47 s。

## 5. 阶段 3：计算与资源

### 5.1 信号短窗口

`daily_standalone` cache-hit 分支在 `QLIB_SIGNAL_SHORT_WINDOW=1` 时把 handler `start_time` 设为 `asof - 180` 个交易日；重训日仍走完整训练。

真实等价性 probe（production image，11-10 provider，cached csi1000 model `3529e523...`）：
- long window 2015-01-01 vs short window 2026-02-06；
- **1,000/1,000 predictions，index equal，max_abs diff 0.0，exact_equal=true**；
- long 44.3 s / short 35.5 s（本 probe 的增益主要来自数据加载；生产仍保留 8 CPU/16 GiB）。

已把 `QLIB_SIGNAL_SHORT_WINDOW=1` 写入生产镜像环境。新增运行时保护：有限预测数低于 PIT 成员数 90% 直接失败。

### 5.2 Website 增量与 archive

- `latest_asset` 只解析自建 provider（archive 优先，directory 兼容），缺失即报错；chenditc 自动选择退出日常路径。
- `download_provider` 支持 `archive_path`：单文件读取 → SHA/size 校验 → 本地解包 → digest cache。
- `_overlay_base` 优先使用 receipt 的 `base.digest/calendar_end`；`_incremental_prior` 要求 prior `asset_digest == base.digest`、`provider_calendar_end == previous`，跨源/跨 digest 不拼接。
- 新增 `status` 只读 receipt 查询；09:00/07:30 用它核验 website 发布。

### 5.3 资源测量

- 18 只股票 mini build：wall 23.2 s，进程树 CPU 15.82 s，avg 0.68 core，RSS max 4.81 GiB（包含 qlib smoke 进程树；cgroup/PSS 在 Modal 容器内不可读，已标记 `unavailable`，未当作 0）。
- 首轮仍采用候选配置：provider build 4 CPU / 8 GiB；信号 worker 8 CPU / 16 GiB；website 8 CPU / 16 GiB；coordinator 1 CPU / 2 GiB。**没有**依据 4.8 GiB 样本宣称 2 CPU / 4 GiB 足够；等真实全市场普通日/重训日账单后再评估。

## 6. 阶段 4：调度与单一写入入口

最终调度（北京时间工作日）：

| 时间 | 函数 | 行为 |
|---|---|---|
| 06:00 | `daily_cron` | 唯一主协调器：provider → signals → website；`max_containers=1` |
| 07:30 | `daily_preopen_retry` | 先核验 receipt；不足则 `daily_cron.remote(refresh_provider=True)` 排队 |
| 09:00 | `daily_readonly_check` | 只读 Volume、无 secret，检查日期/provider/journal/state/website receipt；异常 raise |

旧 `daily_evening`/`daily_evening_retry` 已改名为 `*_manual`，无 schedule，只转发 `daily_cron`。部署后必须用 `modal app info` 确认只剩以上 3 个 cron；v15 的 18:00/19:00 触发器是平台残留，不能只凭源码没有 schedule 就认为已消失。

手动入口：
- `daily_cron_manual(signal_date_override, data_asof_override)` → `daily_cron.remote(...)`
- `daily_evening_manual` / `daily_evening_retry_manual` → 同一入口
- `daily_repair_manual` → 同一入口
- 研究/回填函数不再被调度；它们仍可能写 `signals/` 研究目录，不能与主入口同时运行。

回滚：
- 回滚到明确提交，不删除 journal、不回滚已发生账户交易。
- v15 不是 overlay-only 回滚点（它仍会选 chenditc、且平台保留旧晚间 cron）；回滚说明必须写明数据源语义。
- 回滚后先停旧触发器/确认 cron 清单，再恢复 06:00。

## 7. 已知限制与未完成项

1. **首次全市场新日期构建尚未在 06:00 调度中发生**。11-11 的信号/账户已在 11-10 晚间发布；本轮部署后的首次手动验收复用 11-10 provider（封存），首次真正“从 archive base 追加新交易日”的构建预计 11-12 06:00（data_asof=11-11）。在它通过前不能宣称生产稳定。
2. 旧目录封存需要读 36k 文件；并行后 213 s，但仍是冷缓存下的一次性成本。
3. cgroup/PSS 在 Modal 容器内不可读；RSS 是进程树汇总，可能重复计算共享页。
4. 新成分历史回填使用 BaoStock 重建，不能保证与 chenditc 历史特有的老公司行为完全一致；manifest 已记录来源/锚点/限制，且不回写冻结历史。
5. Website 首次切换因 source digest 变化会全量 replay；之后同为 archive source 才增量。全量 replay 不得被误读为“账户回滚”。
6. budget guard 若返回 `SKIPPED_BUDGET`，信号可发布但网站 NAV 不推进；09:00 会告警。不要把它当作成功。
7. 旧晚间/manual 函数虽已转发，但仍有独立函数名和 `max_containers=1`；真正的跨函数互斥仍依赖它们只调用 `daily_cron.remote()`，不能再加入直接执行路径。

## 8. 部署与验收清单

部署前：
```bash
pytest -q quant/tests
black . -l 120 --check --diff --exclude qlib/_version.py
# 生产 selector 必须显式：
QLIB_DEPLOY_PROFILE=<production-selector> modal deploy quant/code/production/modal_qlib_cn_a10g.py
QLIB_DEPLOY_PROFILE=<production-selector> modal deploy quant/code/ops/website_simulation_modal.py
```
部署后：
```bash
modal app history qlib-prod-daily      # 记录 v16/commit
modal app history qlib-prod-website
modal app info qlib-prod-daily         # 必须只有 06:00/07:30/09:00 三个 cron
modal app info qlib-prod-website      # 无 cron
```
首次验收（手动、前台、attached）：
```bash
modal run quant/code/production/modal_qlib_cn_a10g.py::daily_cron \
  --signal-date-override 2026-11-11 --data-asof-override 2026-11-10
```
预期：
1. `ensure_overlay(2026-11-10)` 把旧目录封存为 archive（约 3.5 min）；
2. 11-11 signal journal 已 `VOLUME_COMMITTED` → `ALREADY_PUBLISHED`，不重算；
3. website 因 source digest 变化全量 replay 三市场，发布新的 source/digest snapshot；
4. marker 写入，包含 provider digest/journal 哈希/website receipt；
5. 随后 `daily_preopen_retry` 或 06:00 应返回 `ALREADY_COMPLETED`，不重复执行。

验收证据必须保存：app version/commit、实际命令、退出码、阶段耗时、provider receipt、journal record、website receipt、三市场 NAV 与上一版对比。若 website NAV 与上一版不一致，先查数据/模型 identity，不能直接接受“全量重算导致差异”。

## 9. 本轮真实验证记录

| 验证 | 命令/位置 | 结果 |
|---|---|---|
| 本地全量测试 | `pytest -q quant/tests` | 982 passed, 2 skipped, 50 subtests |
| Sina fast vs public | 本地 4 symbols | 逐字段完全相等；fast 0.17–0.34 s vs 0.97–1.64 s |
| Archive 读取 | probe `archive_read_bench` | 443 MB / 1.47 s / 287 MiB/s，SHA 通过 |
| Archive 解包 | probe `archive_read_bench` | 17.7 s |
| 并行封存 | probe `seal_bench` | 213 s，receipt PASS |
| 真实 mini rebuild | probe `mini_build` | 18 symbols，0 mismatch，47 s |
| 短窗口等价 | probe `short_window_probe` | 1000/1000，max_abs 0.0，exact_equal=true |
| 全量目录顺序封存 | probe `build_repro` | >58 min 未完成（已取消，无生产写入）；已用并行封存替代 |

未完成：11-12 06:00 首次全市场新日期调度构建；生产账单/阶段成本；真实全市场 build RSS/cgroup。

## 10. 生产部署与切换验收（真实结果，2026-11-11 凌晨）

### 10.1 部署

| App | 最终版本 | Commit | 时间（北京时间） |
|---|---|---|---|
| `qlib-prod-daily` | v18 | `1fdc8a751` | 2026-11-11 03:44 |
| `qlib-prod-website` | v18 | `1fdc8a751` | 2026-11-11 03:45 |

中间版本：v16 `78883ef`（首次切换）、v17 `f77a3b3`（digest 前缀修复）。最终 v18 与 `f77a3b3` 的 Python 代码完全相同，只多了验收文档；`_code_source_sha` 不变，marker 继续有效。

首次部署 v16 `78883ef` 后，`modal app info qlib-prod-daily` 只剩：
`daily_cron` 06:00、`daily_preopen_retry` 07:30、`daily_readonly_check` 09:00。
旧 `daily_evening`/`daily_evening_retry` 函数名已不存在，18:00/19:00 触发器随函数删除而消失；这验证了“源码没有 schedule 不等于平台触发器已删除”。

### 10.2 切换验收（v16）

命令：通过 `modal.Function.from_name("qlib-prod-daily","daily_cron").remote(signal_date_override="2026-11-11", data_asof_override="2026-11-10")`，前台 attached，耗时 **640 s**。

- provider：`SEALED` 11-10 目录 → archive `658555ec…`，seal wall **181.6 s**，receipt `fa1744e2…`；
- signals：`ALREADY_PUBLISHED`（39.4 s；journal `VOLUME_COMMITTED`，GitHub commit `d2df363b…` 未变）；
- website：`PUBLISHED`（395.3 s；source `3d44ecf6…`，commit `317ee0a1…`，assets 12）；
- marker 写入 `preopen_status/2026-11-11.json`，包含 provider/journal/website/code 身份。

与上一版 website 发布比较：NAV、cash、持仓、费用、收益 **完全相同**；`model_sha256` 因新 source 重新序列化而不同。进一步比较 `signal.parquet`：7,478 行，**score 完全相等（max abs diff 0.0），rank 顺序完全一致**。因此全量 replay 只改变代码/来源身份，不改变业务路径。

### 10.3 发现并修复 07:30 误判（v17）

首次调用 `daily_preopen_retry` 时返回了重跑而不是跳过。日志原因：
`website receipt is not published: {'status':'MISSING', ... 'asset_digest':'6585…'}`。
marker 的 provider digest 是裸 64 位 hex，而 website `status` 接口要求 `sha256:` 前缀；验证器已按 `sha256:` 形式比较 website 结果。修复提交 `f77a3b349`，并在 07:30/09:00 两处统一 `_asset_digest` 归一化。

修复后重新部署 daily v17；由于 daily/website source identity 必须一致，website 也重新部署 v17。v17 首次运行中 daily 正确拒绝了 v16 website（不同 source identity），证明身份门禁有效；website 部署 v17 后再次运行成功。

### 10.4 v17 验收

命令与 v16 相同，耗时 **483 s**：

- signals：`ALREADY_PUBLISHED`（3.9 s；无重复发布）；
- website：`PUBLISHED`（470.1 s；source `3b8a473a…`，commit `abb837d0…`）；
- `daily_preopen_retry`：`ALREADY_COMPLETED`；
- `daily_cron` 无 override（模拟 06:00）：`ALREADY_COMPLETED`；
- `daily_readonly_check`：`{"status":"OK","provider_digest":"6585…","journal_commit":"d2df363b…"}`；
- 新 website JSON 与上一版除 `source_manifest_sha256`/`provider_archive_sha256`/`model_sha256` 外完全相等；最新 NAV 0.82346966、累计 -0.17653034 不变。
- 最终 v18 `1fdc8a751` 部署后再次调用 `daily_preopen_retry`，仍返回 `ALREADY_COMPLETED`，说明 docs-only 提交没有改变训练/发布身份。

### 10.5 成本

切换前 `budget_status`：today $0.6065，monthly metered $34.3838/$40，reserve $2，允许。
切换后（含探测、两次 website replay、封存）：today $0.6065（账单报表尚未滚动今日口径），monthly metered $34.6740。实际增量约 **$0.29**；仍需在普通日/重训日分别统计 App 账单，不能把本次一次性 replay 当作日常成本。注意 monthly reserve 仅剩约 $3.3（$40 credit - $34.67 metered - $2 reserve），11-12 首次重训 + website 可能触及 website budget guard。

### 10.6 剩余验收门

**尚未发生**：11-12 06:00 的首次“从 archive base 追加新交易日 + 信号（预计因 source 变化重训）+ website 增量”调度运行。这是唯一仍未真实验证的主路径；在它通过前，本轮状态应为“切换验收通过，日常新日期构建待首次调度验收”，不能宣布生产已完全稳定。

建议验收：11-12 06:30 检查 `preopen_status/2026-11-12.json`、`provider_builds/` 失败记录、`cn_data_overlay/archives/2026-11-11/` receipt、signal journal/website status、`daily_readonly_check` 09:00 结果；并保存普通日账单。

## 11. 第二轮复审整改（当前未提交工作区，2026-11-11）

本节是后续本地整改记录，不能回写成第 10 节生产 v18 已包含的能力。整改开始和结束时 Git HEAD 仍为
`41b1c96bde94cde141e906ba70c8295afdeda78b`；第一轮 R01–R10 与本轮 S01–S05 都在同一个**未提交工作区**。
没有执行 reset/restore/clean，没有提交、推送、部署、Modal 云端实测或生产数据写入。

### 11.1 S01–S05 本地关闭证据

| 编号 | 本地状态 | 实现与数值/失败证据 |
|---|---|---|
| S01 | 已关闭 | 生产入口改为有界祖先扫描并优先验证容器代码根；候选不可读时继续，本地/浅层序列化布局均测试，全部缺失时明确报错；不再固定索引 `parents[2]`。 |
| S02 | 已关闭 | frozen anchor 的真实日期进入逐 session 续接；只向新 provider 写 target 行，旧前缀不改。10→11→12 无公司行为场景得到 close=1.2、factor=0.1；另覆盖连续两次公司行为、停牌桥接和桥接证据缺失拒绝。gate 比较独立生成的 expected factor 与实际物化 `.bin` 中的 factor。 |
| S03 | 已关闭 | 对缓存与本次新抓取合并后的最终 symbol/date 输入统一校验；每个交易 session 必须有 Sina hfq、BaoStock preclose 或日期化停牌证据。坏缓存→补拉仍坏、首次抓取仍坏均 fail-closed；有效 preclose fallback 记录为 `baostock_preclose`；partial raw 按 symbol/date 合并；篡改落盘 factor 会被独立 gate 拒绝。 |
| S04 | 已关闭 | 增量分支先加载并核对 prior ledger/final_state，再把恢复持仓及 pending buy/sell 加入 quote universe；signal/ranking universe 保持原 frozen signal。退指持仓经历 blocked sell 后的增量 replay 与 full replay 的逐日 report、session、orders、final_state 精确一致；真实缺价仍产生 gap 并由既有发布门禁拒绝。 |
| S05 | 已关闭 | cache hit 和首次解包共用实际 calendar/manifest 身份校验；校验本地 `day.txt` 的 SHA256、非空、顺序唯一和首尾日期。截断、同末日篡改中间日期、空日历均重解包；正常命中仍不读远程 archive、不重复 extract。 |

### 11.2 R01–R10 保留情况

第二轮交接已认定 R01、R02、R04、R06、R07、R08、R09、R10 的第一轮实现有效；本轮没有移除这些实现或其测试。
R03 的历史前缀保护由 dated-anchor 续接进一步补强，R05 的坏 hfq 缓存补拉由“最终合并输入仍需具备调整证据”补齐。
相关 builder recovery、真实 archive asset、Sina factor 对齐、逐日 PIT、镜像过滤、materialize 命中、进程生命周期 CPU、sampler finally 测试均在完整套件中继续通过。

### 11.3 本地命令与结果

| 验证 | 结果 |
|---|---|
| 修复前 S01–S05 正式回归 | 25 passed、10 failed；失败分别命中路径解析、anchor 数值、调整证据、quote universe、calendar 身份 |
| S01–S05 定向修复后 | 35 passed |
| gate 实际物化 factor 独立校验 | 修复前 1 failed；修复后 1 passed |
| data_ingest/store/website/sampler 等相关套件 | 148 passed、1 skipped |
| `.venv/bin/pytest -q quant/tests` | **1062 passed、2 skipped、50 subtests passed**；12 个 Modal `.local()` 只读警告 |
| 24 个变更/新增 Python 文件逐文件 Black 120 `--check` | PASS |
| `git diff --check` | PASS |

本地固定输入证明了价格/factor、blocked-sell 账户现金/持仓/成交/费用/NAV 的路径一致性，也覆盖 archive asset
候选、builder 恢复和重试短路的既有回归；但本轮没有运行真实全市场 archive→新日期→信号→网站三市场云端链路。

### 11.4 尚未完成和发布边界

- **尚未提交/推送**：HEAD 仍是 `41b1c96bd`，本节代码、测试、第一轮修复和文档均在工作区。
- **尚未部署**：第 10 节 v18 是整改前历史部署，不含当前 R/S 修复；未查询或改变当前 App 版本/活动任务。
- **尚未云端/生产验收**：未验证真实全市场新日期构建、三市场信号、网站增量 receipt、09:00 只读检查和账单。
- **资源结论未更新**：R09 的进程树 CPU 是观测下限，旧 0.68 core 样本仍不能支持进一步缩容。
- 后续若获授权，只做生产输入只读、隔离输出、有界前台 attached 的集中验证；禁止 `modal run --detach`，不重复付费预热。

因此当前准确状态是：**R01–R10 保留，S01–S05 已本地代码/回归关闭；整个工作区尚未提交、部署或生产验收。**

## 12. 第三轮复权数值整改与最终本地验收（2026-11-11）

第 11 节为前一阶段的历史状态。后续独立复审发现两处仍可错误 PASS 的公司行为边界，本节记录其修复与重新验收。
用户授权修复后把本轮及此前累积整改一起提交、推送；提交/推送版本以包含本节的 Git 记录和操作回执为准。
**本轮没有部署 App、运行 Modal 实验或改写生产数据。**

### 12.1 两项 P1 修复

| 问题 | 修复 | 回归证据 |
|---|---|---|
| 小 factor 下把真实除权变化当作绝对误差忽略 | `_adjustment_evidence` 使用相对 factor 容差，独立 `build_rows` 的 hfq fallback 同样使用尺度无关容差；落盘 factor gate 改为相对误差校验，避免小 factor 篡改被绝对容差放过 | 归一化 factor 从 `1e-6` 到 `100`；现金分红 `100 → 99.5` 的连续调整价/收益与坐标尺度无关；正常 hfq 舍入噪声仍保留已有 factor |
| hfq 有限就忽略已取得的精确 BaoStock preclose | 有效、日期化的 BaoStock preclose 优先，记录 `baostock_preclose`；缺失/无效 preclose 时才使用有效 hfq | hfq 正确、滞后、偏高三类输入和多种 factor；均由精确 preclose 得到正确调整价 |

额外保护：一旦 hfq 已与精确调整冲突，后续缺少 preclose 的 hfq-only 行不能撤销已经采用的精确调整，必须 fail-closed。
后续精确行重新证实 hfq 一致后，可以恢复 hfq fallback；分别有拒绝和恢复回归。

### 12.2 本地验证

- 新增 **35 个参数化回归用例**；初始 31 个场景在修复前为 **24 failed、7 passed**，另补舍入噪声及证据重新一致场景。
- 完整 `quant/tests`：**1097 passed、2 skipped、50 subtests passed**，12 个 Modal `.local()` 警告。
- website Node 测试：**30 passed**。
- 24 个变更/新增 Python 文件 Black 120 列检查通过；`git diff --check` 通过。
- 独立复审原例重跑：normalized、unit、stale-hfq-with-precise-preclose 三种情形的调整价收益乘数全部为 **1.0**，不再产生虚假 0.5% 亏损。
- provider 级回归核对实际 `.bin` close/factor、gate PASS、调整来源及冻结历史前缀，不仅检查内存计算结果。

### 12.3 当前边界

本地验收覆盖此前 R01–R10、S01–S05 和本节两项复权边界；交接文档中“未提交”等表述保留其历史时间点，不代表最终 Git 状态。
真实全市场新日期 archive 构建、三市场信号/网站增量、09:00 只读检查和普通/重训日账单仍需单独的有界云端及生产验收。
生产 v18 的历史记录不能被解释为已经部署本次修复；不因本地测试通过继续下调资源，也不回写既有不可变快照或账户记录。

## 13. 第二轮复审后的隔离实测（2026-11-11，未部署）

本节记录在 `ad707424d` + `befe35bc8` 本地代码上的隔离实测。生产 Volume 只读或无挂载，输出写入独立 probe Volume；未修改生产 provider、journal、账户或部署。

### 13.1 真实 archive 父快照与新日期构建（CSIndex 滞后分支）

- 在 probe Volume 封存 11-09：**88.3 s**，digest `76900cb0…`；随后重封 11-10：**528.6 s**，digest `658555ec…`。
- 发现并修复：封存 receipt 原先复制旧 manifest 的 base，而不是实际 11-09 archive digest；导致网站父 digest 校验拒绝合法 prefix。修复后 11-10 receipt 明确记录 `base.kind=archive`、`base.digest=76900cb0…`（提交 `befe35bc8`）。
- `mini_build(stale_membership=True)` 使用 11-10 archive 中的 18 只真实股票、11-09 截断 base、真实 Sina/BaoStock，模拟官方成分日期滞后一天：
  - 第一次 build 抛出 `constituent date 2026-11-09 != asof 2026-11-10`；
  - builder 在未创建 staging 的情况下合法重试，`membership_policy=BASE_PIT_LAG`、`equality_verified=true`、gate **PASS**、archive/receipt 发布；
  - 与旧 frozen 11-10 比较：52 个字段差异，全部是 `change` 近零相对误差或 **price/factor ≤ 2.76e-4** 的差异，来自第三轮“精确 BaoStock preclose 优先”的调整证据口径；不是历史前缀改写，也不作为旧快照逐字节 oracle。新代码自身 full/incremental 账户路径已在 13.2 精确一致。
  - 资源：wall 23 s；process CPU lower bound 13.83 s、avg 0.469 core；RSS max 992 MiB；cgroup/PSS 不可读，明确标记 `cpu_seconds_is_lower_bound=true`，不据此缩容。

### 13.2 真实 archive 的网站 INCREMENTAL

用 11-09/11-10 真实 archive、csi300、隔离 simulation Volume 执行：

1. 11-10 smoke + full（baseline）；
2. 11-09 smoke + full（prior）；
3. 移走 11-10 baseline，重跑 11-10，命中 11-09 prior。

结果：

| 项目 | 结果 |
|---|---|
| 11-10 replay_mode | **INCREMENTAL** |
| prefix prediction exact | **true** |
| new_fit | **false** |
| previous asset digest | `sha256:76900cb0…` |
| points（24 日） | 完全相等 |
| final_state | 完全相等 |
| ledger/report.parquet | 完全相等 |
| ledger/orders.parquet | 完全相等 |
| sessions.jsonl / metrics.json | 完全相等 |
| 总耗时 | 260.6 s（含两次 full + smoke） |

这验证了 R02 的“真实 archive asset 无 local_path”增量入口和 S04 的报价集合路径；本次真实 csi300 样本没有出现退指仍持有的股票，blocked-sell/退指持仓的精确 full-vs-incremental 证据仍由 `test_prelive_revalidation.py` 的本地回归覆盖。

### 13.3 未完成边界

- 本次未部署；生产两 App 仍为 v18（`1fdc8a7`），不包含本轮 S01–S05 与第三轮复权修复。
- 11-12 06:00 才是首次“真实全市场新日期 provider + 信号（预计重训）+ 网站增量”调度验收；在它通过前不得宣布生产稳定。
- 若部署，source identity 变化会触发一次 website full replay；必须先完成一次 11-11/11-10 部署后验收，使 11-12 的 website 能从新的 11-10 baseline 走增量。

## 14. v19 集中部署与切换验收（2026-11-11 12:16）

### 14.1 部署

| App | 版本 | Commit | 时间（北京时间） |
|---|---|---|---|
| `qlib-prod-daily` | v19 | `a9c079eac` | 2026-11-11 12:16 |
| `qlib-prod-website` | v19 | `a9c079eac` | 2026-11-11 12:16 |

`modal app info qlib-prod-daily` 只有 06:00 `daily_cron`、07:30 `daily_preopen_retry`、09:00 `daily_readonly_check` 三个 cron；website App 无 cron。v19 包含上一轮 S01–S05、第三轮复权修复以及隔离验收发现的 seal-base digest 修复（`befe35bc8`）。

### 14.2 11-11/11-10 切换验收

命令：部署后 `daily_cron.remote(signal_date_override="2026-11-11", data_asof_override="2026-11-10")`，前台 attached，**486.9 s**。

- provider：`EXISTS` 11-10 archive `658555ec…`；
- signals：`ALREADY_PUBLISHED`（56.3 s；journal `d2df363b…` 未变）；
- website：`PUBLISHED`（404.1 s；source `515568f9…`，commit `3ec47293…`，12 files）；
- marker：`code_source_sha256=515568f9…`，provider `658555ec…`。

业务一致性：
- csi1000 `signal.parquet` 24,998 行，score **完全相等（max diff 0.0）**；
- website `csi1000_website_performance.json` 除 source/provider/model identity 外完全相等；最新 NAV **0.82346966**、累计 **-0.17653034** 不变。

### 14.3 调度路径

- `daily_preopen_retry` → `ALREADY_COMPLETED`；
- `daily_cron` 无 override（06:00 路径）→ `ALREADY_COMPLETED`；
- `daily_readonly_check` → `{"status":"OK","provider_digest":"658555ec…","journal_commit":"d2df363b…"}`。

### 14.4 成本与剩余门

- 切换前 monthly metered $34.79 / $40，reserve $2；切换后 **$34.95**，headroom 约 **$3.05**。
- 仍未发生：**11-12 06:00 首次全市场新日期 provider 构建 + 信号重训 + 网站 INCREMENTAL**。这是唯一未完成的生产门；通过前状态为“v19 切换验收通过，日常新日期构建待首次调度验收”。
- 首次重训日 + 网站增量可能进一步消耗 headroom；若 website budget guard 触发，信号仍发布，网站会 `SKIPPED_BUDGET` 并由 09:00 告警。是否提高 monthly credit 由用户决定，本轮未修改预算策略。
