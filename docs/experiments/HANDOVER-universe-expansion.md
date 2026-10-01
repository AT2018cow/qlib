# 交接文档——新增股票候选池（Universe Expansion）

> 面向新对话：目标是把当前 csi1000-only 的信号系统扩展到更多候选池（如 csi800/全市场/自定义池），以及随之而来的参数重选、训练、回测验证工作。

## 一、当前基线（出发点）

### 现有 Universe 与配置

| 项 | 当前值 | 来源 |
|---|---|---|
| 股票池 | csi1000（含历史真实成分，~2800 只曾进池） | chenditc `instruments/csi1000.txt` |
| 模型 | LightGBM + Alpha158 + 20 日标签 | `workflow_config_lightgbm_Alpha158_csi500.yaml` + 补丁 |
| 组合 | top20 等权 + nd2 每日微调 | 终审候选 |
| 基准指数 | SH000852（中证 1000） | benchmark 参数 |
| **5.5 年滚动超额** | **+11.6%**（t=1.44 不显著） | `results/batch_c/rolling5y_c1000_nd2.json` |

### 可用的候选池（chenditc 数据包内已有 instruments 文件）

```
instruments/all.txt       # 全部 A 股（~6000 只，含北交所）
instruments/csi300.txt   # 沪深 300
instruments/csi500.txt   # 中证 500
instruments/csi800.txt   # 中证 800 = 300 + 500
instruments/csi1000.txt # 中证 1000（当前）
instruments/csiall.txt   # 全部中证指数成分并集
```

### 已验证的池间差异（批次 A 数据）

| 池 | 单窗口 2026 年超额（csi1000+top20 口径下的批次 A 测试）|
|---|---|
| csi300 | +2.5% |
| csi500 | +8.4%（后关 Close：滚动 -4.1%）|
| csi1000 | **+26.2%**（唯一候选）|

## 二、扩展方向评估（预期 vs 风险）

| 新 Universe | 预期收益 | 风险 | 建议优先级 |
|---|---|---|---|
| **csi800**（沪深300+500）| 更大盘风格、alpha 薄（csi500 已实证关闭）| 低 | 低——除非需要风格分散 |
| **全市场**（all.txt，含北交所）| 更多 alpha 机会（小盘/微盘最强）| **流动性风险**、退市风险、ST/次新股 | **高（推荐首选）** |
| **自定义池**（如剔除 ST / 上市>1 年 / 成交额>5000 万）| 消除已知风险后再挖 alpha | 需构建自定义 instruments 文件 | **高（与全市场并行）** |

## 三、新增 Universe 的参数重选需求

### 3.1 必须重测的参数（按优先级）

| # | 参数 | 原因 | 方法 | 预计成本 |
|---|---|---|---|---|
| 1 | **topk** | 池子变大，头部浓度变化——全市场 top20 的占比从 2% 降到 0.3% | 单窗口快筛（top10/20/50）| ~$0.5 |
| 2 | **n_drop（nd）** | 池子变大，信号头部更密集或更稀疏 | 单窗口快筛（nd 1/2/3/5）| ~$0.5 |
| 3 | **LGB 超参** | 特征分布随池子变化（尤其量价密度的尾部差异） | `--batchb`（12 组快搜）| ~$2 |
| 4 | **训练起点** | 全市场含更多历史短数据股票 | 起点 2011/2013/2016 | ~$1 |
| 5 | **标签周期** | 全市场 alpha 衰减速度可能不同 | 20 日 vs 40 日 | ~$0.5 |

### 3.2 重测纪律（从批次 A/B/C 学到的）

```
① 单窗口粗筛（< 3pp 差异视为噪声）
② 只记录 top3 候选（不留唯一幸存者）
③ 最终裁判 = 5 年滚动 Walk-Forward（批次 C 模式）
④ 单段年化数字必须绑定窗口比较
```

## 四、技术实施要点

### 4.1 修改 `daily_standalone` 支持新池

当前代码已支持 `market` 参数（`--best --daily --market csi1000`）。

```python
# 只需在 _load_and_patch_cfg 的 market patch 中扩展 _indices dict：
_indices = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852",
            "csi800": "SH000906",           # 需确认 chenditc 是否含 SH000906 数据
            "all": "SH000001"}               # 全市场用上证综指做基准（或等权池收益）
```

### 4.2 自定义 Universe 构建

```python
# 示例：构建"全市场剔除 ST/次新/低流动性"自定义池
# 写一个新的 instruments 文件到 /tmp/cn_data/instruments/my_universe.txt
# 格式：SYMBOL\tSTART\tEND（与 chenditc 现有文件相同）
# 来源：akshare 获取 ST 状态、上市日期、日均成交额
# 然后让 qlib.init 的 provider_uri 指向含此文件的目录
```

### 4.3 需要修改的代码位置

| 文件 | 函数 | 修改 |
|---|---|---|
| `modal_qlib_cn_a10g.py` | `_load_and_patch_cfg` | 扩展 `_indices` 映射 |
| `modal_qlib_cn_a10g.py` | `daily_standalone` | 支持自定义池的 benchmark 选择 |
| `modal_qlib_cn_a10g.py` | `daily_cron` | 如果新池作为独立 cron（可能双池并行） |
| `freq_experiment.py` | `_load_task` | 同理扩展 market |

## 五、训练与验证工作流程

```
阶段 1：粗筛（1 小时，~$1）
  modal run modal_qlib_cn_a10g.py --batcha   # 适配新池后跑 topk×nd 网格

阶段 2：参数依据（2 小时，~$3）
  modal run modal_qlib_cn_a10g.py --batchb   # 新池上跑起点/窗口/超参

阶段 3：终审（1 小时，~$3）
  modal run modal_qlib_cn_a10g.py --batchc   # 5 年滚动，market=新池

阶段 4：部署
  modal deploy modal_qlib_cn_a10g.py          # cron 切换或双池并行
```

### 5.1 双池并行（如果新旧池都要保留）

当前 `daily_cron` 只调一次 `daily_standalone`（csi1000）。如需同时输出新旧两池：
- 方案 A：`daily_cron` 内跑两次 `daily_standalone.remote()`（market 参数不同）
- 方案 B：部署两个 cron app（不同 schedule 标签）

## 六、数据库/成本注意事项

| 项 | 说明 |
|---|---|
| 全市场训练时间 | ~2500→6000 只股票，单次训练 15→35 分钟（每 20 日一次），月成本 +~$1 |
| 走势 JSON | 全市场 top20 依然只有 20 只——无额外成本 |
| 流动性风险 | 全市场含微盘股——**强烈建议**自定义池加流动性门槛（如日均成交额 >2000 万） |
| 北交所 | all.txt 含 BJ 前缀股票——涨跌停规则不同（30%），limit_threshold 需检查 |

## 七、关联文档

- 系统总交接：`HANDOVER.md`
- 终审基线：`05-final-audit.md`（+11.6% 的统计意义与局限性——新池工作也受同一约束）
- 网站交接：`HANDOVER-website.md`（新池信号可能需要网站新增列/过滤）
- 验证纪律：`01-findings.md` 的"经验教训"板块（7 条）

## 八、star_chn 池（科创板+创业板，2026-09-30 已接入）

### 8.1 板块规则（`board_rules.py`，根模块，纯函数 fail-closed，9 单测 `tests/test_qlib_board_rules.py`）

| 板块 | 判定（instruments 符号） | 涨跌停 | 关键日期 |
|---|---|---|---|
| 科创板 star | SH688*/SH689* | 全程 ±20%（过滤阈 0.195） | 2019-07-22 开市 |
| 创业板 chinext | SZ300*/SZ301*/SZ302* | 改革前 ±10%（0.095）、改革后 ±20%（0.195） | **2020-08-24 注册制改革** |
| 主板 main | SH60*/SZ00*/SZ00*2 等 | ±10%（0.095） | — |
| ST | 符号无法判定 | ±5%（0.045） | 需外部 ST 名单传入 `st_symbols` |
| 北交所 bse | BJ* | ±30%（0.295） | all.txt 含但不在 star_chn 池 |
| 指数 index | SH000*/SH880*/SZ399*/BJ899* | 不参与过滤 | 注意 SZ000xxx 是深主板股票不是指数 |
| 新股豁免 | star/chinext | **上市前 5 交易日无涨跌停（不剔除）** | 上市日= instruments 首个 span 的 start；无日历时近似 7 自然日 |

### 8.2 接入位置（管道支持 `--market star_chn`）

| 文件 | 修改 |
|---|---|
| `board_rules.py`（新增） | `board_of`/`limit_threshold`/`build_custom_instruments`（all.txt 前缀过滤生成池文件）/`board_aware_limited`（掩码，按行 datetime 判阈）/`star_chn_backtest_guard`（回测窗口 < 2020-08-24 显式拒绝——float 阈值无法表达板块混池） |
| `modal_qlib_cn_a10g.py` | `_load_and_patch_cfg` 支持 star_chn（生成池文件 + 基准 SZ399006 存在性检查 fail-closed + 阈值 0.195）；`_daily_impl`/`daily_standalone` 涨跌停过滤改 `board_aware_limited`（板块感知，listing 从 all.txt 取）；研究信号文件名带池后缀避免多池互相覆盖；新增 `::verify_universe`（纯读验证：池数/上市区间/基准存在性/qlib 读数） |
| `freq_experiment.py` | `BENCH_BY_MARKET` + `_ensure_custom_pool` + 阈值 0.195 + guard |
| cron/生产 | **`daily_cron` 维持 csi1000 不变**——换池须先走 §三 参数重选纪律 |

### 8.3 已知局限（诚实清单）

1. **ST ±5% 未接线**：符号无法判定，需 akshare ST 名单 → `st_symbols` 参数已预留
2. **科创板手数**：200 股起、1 股递增；qlib `trade_unit=100` 对 688 不准（研究口径可接受，执行需修正）
3. **基准 = 等权合成指数 SZ399999（已解决）**：chenditc 实测只含中证系指数（SH000300/SH000852/SH000905/SH000906/SH000985），无创业板指/科创50——跨池基准污染归因。`::build_star_chn_bench` 构造等权合成指数（纯派生自真实池成分 close，日收益=有效前收股票等权均值，按日重算；核心数学 `board_rules.ew_index_matrix` 有单测）。已就绪：2070 只成分，4104/6475 日有均值收益，最新 13.50。等权基准下超额 = 纯选股 α，减外部创业板指（akshare）得 β 归因
4. **合并池基准不完美**：创业板主导（1449 只 vs 科创板 621），跨板块超额解释需谨慎
5. `verify_integrity`/研究批 bt 的 0.095 口径未改——它们审计的是 csi 池；star_chn 批次自用 0.195

### 8.4 验证结果（2026-09-30 实跑记录）

| 项 | 结果 |
|---|---|
| `::verify_universe` | star 621 只（最早上市 2019-07-22）+ chinext 1449 只（2009-10-30）= 2070 只；qlib 端到端 2024 后 2042 只可交易，688 读数正常 |
| `::build_star_chn_bench` | SZ399999 等权合成基准已写入 Volume（6475 bar，最新 13.50） |
| `daily_signal_cpu --market star_chn` 冒烟 | 训练完成 → 板块感知过滤剔除 5 只涨/跌停（09-18）→ top20 全部 688/301/300 前缀 → CSV `2026-09-18_top20_lgb158_star_chn.csv`（池后缀防覆盖） |

### 8.5 拆池判别实验（2026-10-01 实跑，infi workspace）

三池同口径对照（窗口 2026-01-01~09-11、各自等权合成基准、阈 0.195、9 组全记录；结果 /vol/batch_a_<pool>/results.json，infi Volume）：

| topk/nd | star_chn 合并 | chinext（创业板） | star（科创板） |
|---|---|---|---|
| top10/nd1 | -76.0% | -39.0% | -74.9% |
| top10/nd2 | -50.3% | -11.7% | -52.3% |
| top10/nd3 | -24.6% | -25.2% | -41.0% |
| top20/nd1 | -43.2% | -31.8% | -8.3% |
| top20/nd2 | -20.1% | **+2.9%** | -25.5% |
| top20/nd3 | -7.9% | **+10.4%** | -26.5% |
| top50/nd1 | -20.5% | -13.3% | -3.5% |
| top50/nd2 | -14.2% | -0.8% | **+7.2%**（孤点） |
| top50/nd3 | -16.4% | **+11.2%** | -5.4% |

**判别结论（单窗口粗筛口径）**：
1. **合并稀释假设成立**：chinext top20/nd3 +10.4% / top50/nd3 +11.2% vs 合并池 -7.9% / -16.4%，差 >18pp 远超噪声带——合并池负结果主要由科创板负 α 拖累
2. **star 板块几乎全负**：唯一正值 top50/nd2 +7.2% 是 9 组里的孤点（多重检验下不可信）；科创板方向按纪律应放弃
3. **chinext 正超额集中在 nd3 低换手**（nd1/nd2 仍差），与 csi1000 形态（top20/nd2 最优）不同——20 日标签头部信号在创业板衰减形态不同
4. 对照 csi1000 同窗口 +26.2%：chinext 最好 +11.2% 仍弱一截，但为正——值得进入下一阶段
5. 拆池基准已建好：SZ399998(chinext, 1449 只, 最新 14.18)、SZ399997(star, 621 只, 最新 1.33)——纯派生可复算

### 8.5b 批次B-chinext（2026-10-01 实跑，infi，并行版 batch_b_one 16任务×8并发，~15min）

| 项 | 结果 |
|---|---|
| B1 训练起点 | 2011: **-29.9%** / 2013: **-15.4%** / 2016: **-20.0%**（全负） |
| B2 expanding vs sliding | expanding(=2013) -15.4% vs **sliding_6y +11.7%**（差 26pp） |
| B3 超参 12 组 | 全在 -30%~+0.9%，最好 +0.9%（lr0.12/depth4/l1=l2=100）——**超参无增益** |

**⚠️ 关键发现：复现性问题（比 B1/B3 结论更重要）**
- 同一配置（2016 起点 + top20/nd2 + chinext + 等权基准 SZ399998 + 阈 0.195）：批次A **+2.9%**，批次B b1_2016 **-20.0%**——同配置两次运行差 **23pp**
- 机理：LGB seed=0 固定，但多线程浮点归约顺序跨容器（不同物理 CPU）不保证一致 → 树结构微差 → 头部排名抖动；top20 策略对头部排名极敏感（批次A 观察"头部密集且错"的另一种体现）
- 含义：**chinext 单窗口结果的运行间方差 ~20pp 级**，批次A 的 +10.4%/+11.2% 与 -20% 统计不可区分——"chinext 存活"的粗筛结论被显著削弱
- csi1000 主线此前未观测到该量级抖动（批次A/B/C 结果方向一致），可能与池波动率/头部浓度有关，未验证

**下一步只有两条路**：① 批次C 5 年滚动终审（多窗口平均稀释运行方差，仍是有效裁判，~$3/池）；② 暂停新池实验记录关池。若要压运行方差本身：LGB `deterministic=True`+单线程代价过大（8核×8倍时间），不推荐；实用替代 = 同配置多 seed 平均（成本×3）。

### 8.5c 批次C-chinext 终审（2026-10-02 实跑，infi，top20/nd3，23窗口 12 并行 ~20min）

| 统计 | chinext top20/nd3 | csi1000 top20/nd2（终审基线） |
|---|---|---|
| 5.5 年滚动年化超额 | **≈+11.5%**（季度均值 2.88%×4） | +11.6% |
| t 值 | **1.12（不显著）** | 1.44（不显著） |
| 正窗口 | **18/23（78%）** | — |
| 中位季度超额 | +5.1% | — |
| 最差季 | **-30.8%**（2024Q2） | 全程 MDD -32.8% |

窗口明细（/vol/batch_c/rolling5y_chinext_t20nd3.json，0 误差）：
- **2021-2022 八连正**（w01-w08，+4.2%~+14.2%）；2023Q2 -20.2%、2024Q2 -30.8% 两个深坑；2025Q4 +24.9%、2026Q3 +22.1% 两个强季
- t=1.12 < csi1000 的 1.44：均值相同但窗口方差更大（创业板高波动），**统计上与 csi1000 不可区分，也都不显著**

**终审判读（诚实口径）**：
1. chinext 滚动均值 ≈ csi1000 基线——新池**没有提供更高 α**，但 t 更低 + 单季 -30.8% 尾部更肥
2. 78% 正窗口是亮点（一致性），但均值被两个深坑拖平；-30.8% 单季 > 生产可承受回撤口径（HANDOVER §4：卫星仓 ≤10-20% 的定价前提）
3. 批次B 的 sliding +11.7% 与批次C 的 +11.5% 方向一致（低换手+分散形态），但**复现性问题（§8.5b 23pp）未解决**——批次C 每窗口独立训练平均掉了部分方差，单窗口读数仍按噪声口径
4. 结论：**chinext 与 csi1000 统计不可区分、尾部更差、基建更复杂（合成基准/板块阈值/200股手数未修）**——按纪律不换池；作为"风格分散卫星池"观察可保留，优先级让位于主线

**优先级更新（2026-10-02 用户决定）**：universe expansion > RD-Agent（HANDOVER.md §7.1 原建议已更新）——新池研究继续保留 runbook，但批次C 证据不支持切池，下一步重心回主线（P3 消息面因子/前向验证积累）。

### 8.7 双池生产化（2026-10-02，代码就绪待部署）

**用户决定：不切池，双池并行选股**（csi1000 生产基线 + chinext 卫星池各出各榜）。chinext 生产化差距已逐项补齐，与 csi1000 同等成熟度：

| 成熟度项 | 实现 |
|---|---|
| 容器内合成基准 | `daily_standalone` 下载解压后调 `board_rules.build_ew_bench_files(data_dir, market)`（chenditc 包不含合成指数，训练前必须构建） |
| **基准单一真源** | Volume 研究路径（`::build_star_chn_bench`）与生产容器路径共用同一 `build_ew_bench_files` + `ew_index_matrix`（有单测对照，两侧不允许漂移） |
| 模型缓存隔离 | `cache_signature` 按配置哈希，chinext/csi1000 各自独立签名文件，互不覆盖 |
| 双榜发布 | `daily_cron` 双池各出 `results/signals/<date>_top20_lgb158.csv`（csi1000 网站兼容不变）与 `..._lgb158_chinext.csv`，chart 同理 `<date>_chart{,_chinext}.json`；**一次 commit 4 文件**（避免 Actions 并发取消） |
| 失败隔离 | 单池失败不影响另一池发布；双池同失败才 raise（告警）；数据日不一致拒绝发布 chinext |
| 审计口径 | `::verify_integrity --market chinext` 已跑：$change 语义 ✅（95 分位偏差 3e-4=复权舍入）；**逐日计数一致率 71.4%** —— 差异全部来自 0.195 阈值边缘的舍入（单日 1-2 只/1408 只），非语义错误；两口径（信号过滤 Ref vs 回测 $change）在边界股票上判定可能不同，量级可忽略 |
| 冒烟验证 | `daily_standalone --market chinext` 全路径实跑 ✅（09-30 top20 全部 300/301 前缀，板块过滤剔除 2 只，合成基准 1451 只构建于容器内） |

**✅ 已部署（2026-10-02）**：`modal deploy`（at2018cow）完成，cron 双池生效——每个交易日 07:00（北京时间）先跑 csi1000（top20/nd2）再跑 chinext（top20/**nd3**=批次A/B/C 终审口径），一次 commit 推 4 文件。成本 ~$7.1→~$14/月（nonpreemptible 3x）。首个 chinext 运行会在 at2018cow 首次重训并建缓存。部署前最终验证：57 测试 + 双 cron 6 场景冒烟 + csi1000 生产路径回归实跑（板块感知过滤对主池内 688 成员正确用 0.195）+ chinext 全路径实跑 + `cache_signature` 含 universe（双池模型缓存不碰撞，已核实 qlib_live_retrain.py:89）。

**已知限制（诚实清单，同 §8.3）**：ST ±5% 未接线；科创板 200 股手数未修（chinext 池无此问题）；运行方差 ~23pp 未量化到 csi1000（可选 dual-seed 实验）。

### 8.5d 科创板批次C 终审（2026-10-02 实跑，infi，top50/nd2=批次A孤点配置，23窗口 12 并行）

| 统计 | star top50/nd2 | chinext top20/nd3（对照） |
|---|---|---|
| 年化超额 | **≈+1.6%** | +11.5% |
| t 值 | **0.37（彻底不显著）** | 1.12 |
| 正窗口 | **10/23（43%）** | 18/23（78%） |
| 中位季度超额 | **-0.9%**（中位为负！） | +5.1% |
| 最差/最好季 | -5.4% / +18.0% | -30.8% / +24.9% |

**终审结论（star 板块）**：批次A 孤点（top50/nd2 单窗口 +7.2%）在 5.5 年滚动下**彻底消解**——均值≈0（+1.6% 年化）、中位为负、43% 正窗、t=0.37。**科创板方向关闭升级为终审级证据**（此前是批次A单窗口的暂时关闭）。窗口明细中唯一的 +18% 季度（w17=2025Q1）进一步印证"孤点本质"：正贡献集中于个别窗口，其余 22 窗基本围绕零。
本地存档：`results/batch_c/rolling5y_star_t50nd2.json`（0 误差）。

### 8.8 下一步 runbook

```
# ✅ 批次A 三池对照完成（§8.5）：chinext 表面存活，star/合并池关闭
# ✅ 批次B-chinext 完成（§8.5b）：起点全负、超参无增益、sliding +11.7%——但同配置复现差 23pp
# ✅ 批次C-chinext 终审完成（§8.5c）：+11.5%/t=1.12/78%正窗 vs csi1000 +11.6%/t=1.44——统计不可区分
# ✅ 双池生产化代码完成（§8.7）：daily_cron 双榜发布+失败隔离，daily_standalone chinext 全路径冒烟通过
# 待用户确认：modal deploy（at2018cow，成本 ~$7.1→~$14/月）后 cron 每日双榜
# ✅ star 批次C 终审完成（§8.5d）：+1.6%/t=0.37/43%正窗/中位负——孤点消解，科创板终审级关闭
# 可选后续：dual-seed 复跑 csi1000 终审量化运行方差（~$1）；前向观察 ≥3 个月再评估
```

冒烟口径参考：star_chn 池内创业板成员涨跌停过滤从 0.095 放宽到 0.195（改革后真实规则），单日剔除数与 csi1000 口径量级一致；显著变化（±3pp 以上）需先查 `board_aware_limited` 掩码。**注意：`prepare_data --force` 会清空 Volume 上的合成基准与池文件（features/instruments 全删重建）——force 后必须重跑 `::build_star_chn_bench`**（生产 daily 路径不受影响，容器内自动重建）。Volume 数据止于 09-30（已 force 刷新）；生产 daily_standalone 每次全量下载无需此步。**infi workspace 已验证可用**：`::verify_universe`/`::build_star_chn_bench`/`::batch_a_star_chn`/`::batch_b --market chinext`/`::batch_c --market chinext`/`::daily_standalone --market chinext` 全跑通；唯一前置 = 一次性 `modal secret create github-push GITHUB_TOKEN=...`（占位值即可，只解锁 modal run 的 spec 校验，研究入口不推送）。


