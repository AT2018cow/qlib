# `quant/` — A股 CSI300/500/1000 量化选股与模拟组合系统

本目录集中存放我们自己的全部量化研发代码、策略文档、历史回测数据库、每日线上信号以及前端网站源码，与上层 Microsoft Qlib 框架本体文件（`qlib/`, `docs/`, `scripts/`, `tests/`, `examples/`）彻底分离。

---

## 一、目录结构总览

```text
quant/
├── README.md                              # 本项目总导航
│
├── code/                                  # 【1. 核心代码区】按职责分模块
│   ├── production/                        #   线上生产与每日信号流水线 (Modal 定时任务、生产配置、模拟组合状态机、前向净值、MVP 门禁)
│   ├── execution/                         #   A 股交易规则与底层执行引擎 (涨跌停、科创/创业板规则、出池股全市场报价 F1 修复、20天重训、前视隔离)
│   ├── research/                          #   策略研发与多轮历史搜索框架
│   │   ├── stage_ab/                      #     第一轮：Stage-A (74超参) 与 Stage-B (10相位) 调优框架
│   │   ├── layered_search/                #     第二轮：P0 -> L1 -> L2 -> L3 -> L4 预算受控分层策略搜索框架
│   │   └── pre_tuner/                     #     早期探索：调仓频率、重训相位归因、科创/创业板卫星池实验
│   ├── data_ingest/                       #   自建 overlay 数据抓取、复权、缓存与内容寻址归档
│   ├── audit/                             #   独立审计与历史账本核验脚本 (F1~F6 门禁核验、独立净值复算、MVP 准入审查)
│   └── ops/                               #   命令行运维与数据健康检查工具
│
├── docs/                                  # 【2. 研发与架构文档区】按阶段整理
│   ├── README.md                          #   文档总索引
│   ├── 00-strategy-handbook/              #   ★《CSI1000 策略选型与历史回测全景手册》(STRATEGY_ATLAS.md)
│   ├── 01-foundation-and-fixes/           #   阶段1：Qlib 审计修复、20天滚动重训、调仓频率与卫星池摸底 (01~15)
│   ├── 02-round1-stage-ab/                #   阶段2：第一轮 Stage-A / Stage-B 超参搜索与晋升 (16~20)
│   ├── 03-independent-audit-f1-f6/        #   阶段3：Stage-B 独立审计、F1 出池股断流修复、F5/F6 门禁 (24~39)
│   ├── 04-mvp-and-website/                #   阶段4：线上 MVP 发布合同、前向净值验收、网站与交接文档 (21~23, 40~49)
│   ├── 05-round2-layered-search/          #   阶段5：第二轮 P0->L1->L2->L3->L4 分层策略搜索与终局报告 (50~60)
│   ├── 06-csi300-csi500-research/        #   CSI300/CSI500 分市场研究
│   ├── 07-code-review/                   #   审查、整改、部署与验收证据
│   ├── 08-current-window-backtest/       #   近期窗口与策略重选研究
│   └── 09-data-source/                   #   行情来源与 lineage
│
├── data/                                  # 【3. 历史回测数据库与线上信号区】主要目录如下
│   ├── README.md                          #   数据字典与 Python 读取示例
│   ├── strategy_catalog/                  #   ★ 统一策略主数据库 (132套策略总表 CSV/JSON、5相位表、21个月逐月收益表、424天日频净值矩阵 Parquet)
│   ├── round2_layered_search/             #   ★ 第二轮 P0~L4 全套回测账本归档 (含 264 套 L1 账本 + 20 套 L4 五相位终审账本)
│   ├── round1_stage_ab/                   #   第一轮 Stage-A / Stage-B 原始结果与策略选择基准证据
│   ├── round1_audit_evidence/             #   第一轮 F1~F6 独立审计证据与 56 套修正重跑账本
│   ├── pre_tuner_experiments/             #   早期调仓频率、相位归因、卫星池实验数据
│   └── signals/                           #   线上三市场 canonical 信号（CSI300 Top10/Drop1、CSI500 Top20/Drop2、CSI1000 Top10/Drop1；每日 12 个文件）
│
├── website/                               # 【4. 前端静态站点源码】Cloudflare Pages 每日信号与模拟组合看板（GitHub Pages 暂停）
└── tests/                                 # 【5. 自动化测试集】量化生产、执行、搜索与前端的单元/回归测试
```

---

## 二、当前线上生产策略速查

| 市场 | 生产 profile | 标签 / 组合 | Release 状态 |
|---|---|---|---|
| CSI300 | `59b93929_top10_drop1_10d` | 10d / Top10、Drop1 | `RISK_ACCEPTED_OVERRIDE` |
| CSI500 | `raw10d_c98856b4_top20_drop2_20d` | 20d / Top20、Drop2 | `RISK_ACCEPTED_OVERRIDE` |
| CSI1000 | `c98856b4_top10_drop1` | 20d / Top10、Drop1 | `INCUMBENT_RETAINED` |

三市场分别维护 100 万元生产 paper 账户；网站另有三市场各 100 万元的独立历史重建模拟账户，两者不拼接。T 日收盘打分，下一交易日开盘按执行规则模拟交易。生产使用唯一自建 overlay archive，研究默认数据 lineage 另见[数据来源说明](docs/09-data-source/02-production-vs-research-data-lineage.md)。

[策略手册](docs/00-strategy-handbook/STRATEGY_ATLAS.md)保留历史回测结果；旧指标标记为 `LEGACY_EXECUTION_CONTRACT_NOT_REPLAYED`，不能当作已按当前执行合同复验的收益或新策略晋升证据。网站收益仍是参考纸面模拟，并非券商成交认证。

---

## 三、常用命令

```bash
# 1. 运行我们自己的全套测试
.venv/bin/python -m pytest -q quant/tests

# 2. 本地构建网站信号索引与前向净值
.venv/bin/python quant/website/build_index.py --signals quant/data/signals --output /tmp/available_dates.json
.venv/bin/python quant/code/ops/build_forward_performance.py --signals quant/data/signals --output /tmp/forward.json
```

生产部署涉及两个 App 和显式生产 selector，应按[当前调度与部署说明](docs/01-foundation-and-fixes/06-cron-setup.md)操作；仅使用默认 Modal profile 或部署主 App 可能指向旧环境或造成版本不一致。
