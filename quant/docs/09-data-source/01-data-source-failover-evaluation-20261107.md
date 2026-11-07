# A 股日线数据源故障转移评估（2026-11-07）

状态：`IMPLEMENTED / DRY_RUN_PASS / NOT_PUBLISHED / PAPER_FROZEN`

本文评估在 `chenditc/investment_data` GitHub release 持续缺数时，能否用免费公开源生成可审计的日线增量，并保持三市场生产 profile、TopK/DropN、1M paper 账户不变。数据接入与 fail-closed gate 已实现并通过生产镜像 dry run；**本文仍不构成生产发布、paper 回补或账户操作授权。**

## 1. 问题与约束

- 上游最新 release 仍为 `2026-11-04`；`2026-11-05`、`2026-11-06` 的 release tag 均为 404。
- 上游 `Upload latest data to github release` 多次失败，根因是 DoltHub freshness API 连续 504，不是本地发布代码。
- 生产主应用已为 v31、网站应用 v6；三市场 signal 在缺数时会走 stale/incomplete 路径并 fail closed。
- 新数据源必须提供日线 OHLCV、复权信息、指数成分/权重、交易日历，并能在隔离环境生成 qlib bin；不得用过期数据伪造 T-1 信号。
- 本轮没有 Tushare token/积分；Tushare Pro 仅保留为后续商业源选项。

## 2. 候选数据源对比

| 来源 | 日线 OHLCV | 复权 | 指数成分/权重 | 日历 | ST/停牌 | 速度与稳定性 | 结论 |
|---|---|---|---|---|---|---|---|
| `chenditc/investment_data` | 有，qlib bin 直接可用 | 有 | 有 PIT 历史 | 有 | 有限 | 单点 GitHub release；本轮 DoltHub 504 导致连续缺数 | 仅保留为**冻结历史底座** |
| Sina finance（AkShare `stock_zh_a_daily`） | 有 | raw + qfq/hfq | 无 | 无 | 无 | 1800 只 raw+hfq 约 10 分钟（6 线程）；网页端点无 SLA，测试中出现过连接重置 | **推荐主日线增量源** |
| BaoStock | 有 | `query_adjust_factor`、raw | 无 CSI1000 成分 | 有 | `tradestatus`、`isST` | 单只约 2–4 秒；稳定但慢；不支持北交所 | **备源 / 精确除权与状态校验** |
| 中证指数官网（AkShare 包装 `cons.xls` / `closeweight.xls`） | 无 | 无 | 官方最新成分与权重 | 无 | 无 | 3 个指数约数秒 | **推荐成分/权重源** |
| 东方财富全市场快照（AkShare `stock_zh_a_spot_em`） | 有 | 无 | 无 | 无 | 无 | 测试中出现 502/连接重置 | 不作主源 |
| Tushare Pro | 有 | 有 | 有 | 有 | 有 | 质量最好，但需要 token/积分；本轮不可用 | 后续商业源候选 |

## 3. 验证方法与结果

### 3.1 与 chenditc 11/04 的全量对照

用 Sina raw 日线乘以 chenditc 的 `$factor`，重建 qlib 口径的 `open/high/low/close/volume/amount`，与 11/04 快照逐只对照：

- 对照股票：CSI300/500/1000 当日在册并集，1799 只有 11/04 行情；
- 价格/成交量/成交额全部在 float32 精度内一致；
- 最大相对误差：`1.0433e-07`；
- 无一只超过 `1e-4`。

逐只结果见 `quant/data/data_source_evaluation/20261107/validation_20261104.csv`。

### 3.2 11/05–11/06 与 BaoStock 抽样对照

随机抽取 20 只成分股，对 11/05、11/06 的 raw `open/close/volume/amount` 与 BaoStock 逐日对照：**20/20 全部精确一致**。BaoStock 同时返回 `tradestatus` 与 `isST`，可作为缺失/停牌/ST 的校验源。

### 3.3 公司行为与复权

Sina `hfq` 与 raw 的比值在正常日保持不变，在除权日发生跳变。11/04–11/06 共识别 7 只明显除权/分红事件（如 SH603858、SH601319、SZ002142、SZ002736、SZ000967、SZ002075、SH600516）。

用 BaoStock `preclose` 对照：

- `preclose` 相对前一日 raw close 的变动方向与幅度，与 Sina hfq/raw 比值变动一致；
- 差异在 0.1%–0.2% 量级，属于 hfq 舍入/复权方法差异；
- 生产接入时，除权日应优先用 BaoStock `preclose` 计算精确 factor，而不是只用 hfq 比值。

### 3.4 指数成分与权重

2026-11-06 中证指数官网成分：

- CSI300：300 只；
- CSI500：500 只；
- CSI1000：1000 只；
- 权重文件日期：2026-10-30。

与 chenditc 11/04 的在册集合相比，11/06 有少量调整：

- CSI300：`SZ002294` 进入，`SH601059` 退出；
- CSI500：`SH603083`、`SZ301297` 进入，`SH600363`、`SH601198` 退出；
- CSI1000：`SH603680`、`SZ300277` 进入，`SH603083`、`SZ301297` 退出。

这说明 chenditc 的指数成员本身可能滞后；生产接入应以中证官网当日文件为准，并记录文件日期与 SHA。

### 3.5 隔离 overlay 与历史完整性

构建隔离 qlib overlay 时，只复制 `chenditc` 的 3601 只历史 CSI300/500/1000 成员（3600 只找到 feature 目录，`SHT00018` 缺失），而不是只复制当前 1800 只。否则训练集会缺少历史成分：

- 2024-01-02：当前-only overlay 只有 692/1000，完整 overlay 为 1000/1000；
- 2016-01-04：当前-only overlay 只有 283/1000，完整 overlay 为 1000/1000。

完整 overlay 在 2016-01-04、2024-01-02、2026-11-04、2026-11-06 的 CSI300/500/1000 在册数量分别为 300/500/1000（2024-01-02 的 CSI500 为 501，是上游原文件本身的重叠，不是 overlay 引入）。

### 3.6 生产镜像下的模型复现

在**生产镜像**的一次性 Modal 研究容器中，用完整 overlay 重训 CSI1000 `c98856b4_top10_drop1`，asof=`2026-11-04`：

- 训练/验证分段与生产 metadata 完全一致：train_end `2025-08-15`、valid_end `2026-09-29`；
- 涨跌停展示过滤剔除 35 只；
- 展示 Top10 与已发布 11/06 override 的 Top10 **逐位、逐分数完全一致**：

| 排名 | 标的 | 分数 |
|---:|---|---:|
| 1 | SZ300131 | 0.4092971804 |
| 2 | SH603516 | 0.4072689148 |
| 3 | SH688449 | 0.3611332844 |
| 4 | SH688127 | 0.3533335536 |
| 5 | SH688362 | 0.3385806478 |
| 6 | SZ002859 | 0.3311091153 |
| 7 | SZ301217 | 0.3218944033 |
| 8 | SZ002484 | 0.3194477980 |
| 9 | SH688372 | 0.3157085932 |
| 10 | SH688182 | 0.3124140110 |

这是本轮最强的端到端证据：历史 overlay 未改变训练数据，模型链路与生产一致。

## 4. 2026-11-06 三市场研究排名

以下排名由完整 overlay + 生产镜像生成，已应用与生产一致的涨跌停展示过滤。**仅供研究验证，不是已发布信号，paper 仍冻结在 11/05 pending。**

### CSI1000（Top10/Drop1）

| 排名 | 标的 | 分数 |
|---:|---|---:|
| 1 | SH688449 | 0.3322814689 |
| 2 | SH600353 | 0.3255651517 |
| 3 | SH688182 | 0.3204190881 |
| 4 | SZ300131 | 0.3194636326 |
| 5 | SH688627 | 0.3189471520 |
| 6 | SZ002484 | 0.3089099202 |
| 7 | SH688141 | 0.3071649989 |
| 8 | SZ000636 | 0.3061557167 |
| 9 | SH605376 | 0.2972032300 |
| 10 | SH688362 | 0.2958249927 |

### CSI500（Top20/Drop2）

| 排名 | 标的 | 分数 |
|---:|---|---:|
| 1 | SZ300620 | 0.4625403957 |
| 2 | SZ001309 | 0.3419582198 |
| 3 | SH688052 | 0.3015749802 |
| 4 | SH688266 | 0.2785560531 |
| 5 | SZ301536 | 0.2690532435 |
| 6 | SH688234 | 0.2323449800 |
| 7 | SH688318 | 0.2206381274 |
| 8 | SH688120 | 0.1990562043 |
| 9 | SH688002 | 0.1961750256 |
| 10 | SZ002126 | 0.1908296569 |
| 11 | SZ000021 | 0.1885543449 |
| 12 | SZ002821 | 0.1873112375 |
| 13 | SH688561 | 0.1845931469 |
| 14 | SZ000415 | 0.1803892611 |
| 15 | SH603899 | 0.1746147865 |
| 16 | SH600711 | 0.1733487283 |
| 17 | SH600378 | 0.1711748320 |
| 18 | SH688122 | 0.1709737894 |
| 19 | SH605589 | 0.1665590945 |
| 20 | SZ000032 | 0.1653197405 |

### CSI300（Top10/Drop1，10 日标签）

| 排名 | 标的 | 分数 |
|---:|---|---:|
| 1 | SH688521 | 0.4531427980 |
| 2 | SH603986 | 0.4325347128 |
| 3 | SH601138 | 0.4006551579 |
| 4 | SZ301308 | 0.3945968380 |
| 5 | SZ000657 | 0.3826269818 |
| 6 | SZ300476 | 0.3743928822 |
| 7 | SH600183 | 0.3727685676 |
| 8 | SZ000988 | 0.3652462257 |
| 9 | SZ002938 | 0.3649904546 |
| 10 | SZ300394 | 0.3536219989 |

## 4.1 实现与生产镜像 dry run

新增隔离包 `quant/code/data_ingest/`：

- `sina.py`：Sina raw+hfq 主源，线程池 + 重试；
- `baostock_source.py`：BaoStock 备源，提供 `preclose`、`tradestatus`、`isST`；
- `csindex.py`：中证官网 `cons.xls` / `closeweight.xls` 解析与 SHA；
- `adjust.py`：Qlib 口径复权；正常日保持 factor 精确连续，除权日优先用 BaoStock `preclose`；
- `overlay.py`：复制全部历史成分、追加日历/bin、更新 PIT instruments；
- `gate.py`：日历、成分、覆盖率、OHLC、底座完整性、抽样对照、除权、qlib smoke；
- `pipeline.py` / `cli.py`：编排与本地入口；
- `quant/code/ops/modal_data_overlay_dry_run.py`：生产镜像 dry-run 入口；
- `quant/tests/test_data_ingest.py`：合成数据单测。

生产镜像 dry run 使用独立的临时 Volume，不挂载生产 `qlib-cn-data`：

- 冻结底座：chenditc 2026-11-04 release，archive SHA `dc9172…`；
- overlay：`csi300/500/1000` 历史并集 3601 个 feature 目录，追加 2026-11-05/11-06；
- gate：16 项全部 PASS，包括 300/500/1000 覆盖率、40 次 Sina/BaoStock 抽样对照、11 个除权 factor 校验、qlib smoke；
- 11/04 复现：`repro_status=PASS`，Top10 与分数逐位一致；
- 11/06 三市场排名：与第 4 节研究结果逐位一致。

本轮**没有**把新路径接入 `daily_cron`，也没有部署新镜像、发布信号或改动 paper。`baostock` 已加入主应用镜像依赖，下一次部署才会生效。

## 5. 推荐接入架构

### 5.1 角色划分

- **历史底座**：保留 chenditc 2026-11-04 qlib bin，作为已审计训练历史；不在生产 Volume 上原地修改。
- **主日线增量源**：Sina `stock_zh_a_daily`（raw + hfq），覆盖当前指数成分 + 当前持仓 + 必要的新增成分。
- **备源/校验源**：BaoStock，用于缺口补齐、`preclose` 精确除权、`tradestatus`/`isST`、日历交叉校验。
- **成分/权重源**：中证指数官网 `cons.xls` / `closeweight.xls`，每日保存原始文件、日期和 SHA。
- **商业源**：Tushare Pro 留作后续升级；本轮无 token，不阻塞免费方案。

### 5.2 每日流程

1. 读取并校验交易日历；确认目标信号日是最新交易日。
2. 拉取中证官网当日成分/权重；与上一版对比，记录进入/退出和文件 SHA。
3. 对当前成分并集拉取 Sina raw+hfq；失败重试后仍缺失的标的转 BaoStock。
4. 对除权嫌疑标的用 BaoStock `preclose` 精确更新 factor；对停牌/ST 标的记录状态。
5. 在**版本化副本**上追加缺失日期，生成 `provider_manifest.json`（base archive SHA、calendar SHA、新增行 SHA、来源 SHA、成分 SHA）。
6. 运行 gate：
   - 当前成分覆盖率 ≥ 99.5%，缺口必须能解释为停牌/退市；
   - 随机 20 只与 BaoStock 对照，价格/成交量/成交额一致或仅有 float32 误差；
   - OHLC 关系、成交量/成交额非负、无重复日期；
   - qlib 查询 smoke：最新日期可读、CSI300/500/1000 在册数量正确；
   - 除权日 factor 与 BaoStock `preclose` 一致；
   - PIT 成分 SHA 与当日官网文件一致。
7. gate 全部通过才允许发布；任何一项失败即 fail closed，paper 保持冻结，不静默回退到过期数据。

### 5.3 版本与回退

- 生产 Volume 使用不可变快照目录：`provider_snapshots/<asof>/<manifest_sha>/`；只有 gate 通过后才原子切换 `latest` 指针。
- chenditc release 仍可作为**历史底座**或第二校验源；但若其 lag > 0，不得用它替代新源生成正常 T-1 信号。
- 新源失败且 chenditc 也缺数时，signals 必须继续返回 `INCOMPLETE_STALE_DATA`，不得生成排名。

## 6. 风险与未决事项

| 风险 | 现状 | 缓解 |
|---|---|---|
| Sina 网页端点无 SLA、可能反爬/重置 | 本轮 1800 只约 10 分钟；曾出现连接重置 | 重试、并发限流、BaoStock 备源、逐日抽样校验 |
| BaoStock 慢、无北交所 | 单只约 2–4 秒 | 仅用于缺口/除权/状态校验；不用于全量主拉取 |
| 中证官网只有最新成分，无完整历史 PIT | 11/06 文件可用；历史回溯依赖 chenditc | 从接入日起每日存档；历史训练继续用 chenditc PIT |
| 除权日 hfq 与 chenditc/Tushare factor 有 0.1%–0.2% 差异 | 7 只除权样本已验证方向一致 | 除权日强制用 BaoStock `preclose` 精确更新 factor |
| ST/停牌/退市状态 | Sina 日线可能缺行 | BaoStock `tradestatus`/`isST`、`query_stock_basic`、交易所名单 |
| 许可与再分发 | 公开免费源 | 仅内部使用；不提交/公开原始行情；遵守各源条款 |
| 11/06 排名尚未发布 | 仅研究输出 | 需单独授权后才可进入 paper/网站流程 |

## 7. 结论与需授权事项

结论：

- Sina raw+hfq + 中证官网成分 + BaoStock 校验，能够在**不依赖 Tushare**的情况下补齐 11/05/11/06，并在生产镜像中复现 11/04 生产排名；
- 当前最稳妥的接入方式是“chenditc 历史底座 + 新源日线 overlay”，而不是立即全量重建历史；
- 数据接入、manifest、cross-check、overlay writer 与 fail-closed gate 已实现；生产镜像 dry run 通过；
- 11/06 三市场研究排名已生成，但**不得**视为已发布信号，paper 仍冻结。

已完成：

1. 数据源角色划分与 overlay 架构；
2. ingestion、manifest、cross-check、qlib overlay writer 与 fail-closed gate（含 6 项合成单测）；
3. 生产镜像 11/05–11/06 dry run，并与本文研究排名逐项对照。

仍需单独授权：

1. 将 overlay 路径接入 `daily_cron` / `daily_cron_second_chance`，包括版本化快照、原子切换、失败回退和监控；
2. 部署带 `baostock` 依赖的新主应用镜像，并在生产 shadow 模式下观察若干交易日；
3. paper 11/05–11/06 回补及 11/06 override 的处理方案（需另行审计，不与数据源接入捆绑）。

## 8. 证据

- `quant/data/data_source_evaluation/20261107/summary.json`
- `quant/data/data_source_evaluation/20261107/validation_20261104.csv`
- `quant/data/data_source_evaluation/20261107/reproducibility_20261104.json`
- `quant/data/data_source_evaluation/20261107/ranking_csi1000_20261106.csv`
- `quant/data/data_source_evaluation/20261107/ranking_csi500_20261106.csv`
- `quant/data/data_source_evaluation/20261107/ranking_csi300_20261106.csv`
- `quant/data/data_source_evaluation/20261107/dryrun_result.json`
- `quant/data/data_source_evaluation/20261107/SHA256SUMS.json`
