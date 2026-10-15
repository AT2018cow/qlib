# `quant/` — A股中证1000量化选股与模拟组合系统

本目录集中存放我们自己的全部量化研发代码、策略文档、历史回测数据库、每日线上信号以及前端网站源码，与上层 Microsoft Qlib 框架本体文件（`qlib/`, `docs/`, `scripts/`, `tests/`, `examples/`）彻底分离。

---

## 一、目录结构总览

```text
quant/
├── README.md                              # 本项目总导航
│
├── code/                                  # 【1. 核心代码区】按职责分为 5 个子模块
│   ├── production/                        #   线上生产与每日信号流水线 (Modal 定时任务、生产配置、模拟组合状态机、前向净值、MVP 门禁)
│   ├── execution/                         #   A 股交易规则与底层执行引擎 (涨跌停、科创/创业板规则、出池股全市场报价 F1 修复、20天重训、前视隔离)
│   ├── research/                          #   策略研发与多轮历史搜索框架
│   │   ├── stage_ab/                      #     第一轮：Stage-A (74超参) 与 Stage-B (10相位) 调优框架
│   │   ├── layered_search/                #     第二轮：P0 -> L1 -> L2 -> L3 -> L4 预算受控分层策略搜索框架
│   │   └── pre_tuner/                     #     早期探索：调仓频率、重训相位归因、科创/创业板卫星池实验
│   ├── audit/                             #   独立审计与历史账本核验脚本 (F1~F6 门禁核验、独立净值复算、MVP 准入审查)
│   └── ops/                               #   命令行运维与数据健康检查工具
│
├── docs/                                  # 【2. 研发与架构文档区】按阶段分为 6 个专题目录
│   ├── README.md                          #   文档总索引
│   ├── 00-strategy-handbook/              #   ★《CSI1000 策略选型与历史回测全景手册》(STRATEGY_ATLAS.md)
│   ├── 01-foundation-and-fixes/           #   阶段1：Qlib 审计修复、20天滚动重训、调仓频率与卫星池摸底 (01~15)
│   ├── 02-round1-stage-ab/                #   阶段2：第一轮 Stage-A / Stage-B 超参搜索与晋升 (16~20)
│   ├── 03-independent-audit-f1-f6/        #   阶段3：Stage-B 独立审计、F1 出池股断流修复、F5/F6 门禁 (24~39)
│   ├── 04-mvp-and-website/                #   阶段4：线上 MVP 发布合同、前向净值验收、网站与交接文档 (21~23, 40~49)
│   └── 05-round2-layered-search/          #   阶段5：第二轮 P0->L1->L2->L3->L4 分层策略搜索与终局报告 (50~60)
│
├── data/                                  # 【3. 历史回测数据库与线上信号区】按层次分为 6 个子目录
│   ├── README.md                          #   数据字典与 Python 读取示例
│   ├── strategy_catalog/                  #   ★ 统一策略主数据库 (132套策略总表 CSV/JSON、5相位表、21个月逐月收益表、424天日频净值矩阵 Parquet)
│   ├── round2_layered_search/             #   ★ 第二轮 P0~L4 全套回测账本归档 (含 264 套 L1 账本 + 20 套 L4 五相位终审账本)
│   ├── round1_stage_ab/                   #   第一轮 Stage-A / Stage-B 原始结果与策略选择基准证据
│   ├── round1_audit_evidence/             #   第一轮 F1~F6 独立审计证据与 56 套修正重跑账本
│   ├── pre_tuner_experiments/             #   早期调仓频率、相位归因、卫星池实验数据
│   └── signals/                           #   线上每日自动发布的 CSI1000 Top10 信号四件套
│
├── website/                               # 【4. 前端静态站点源码】GitHub Pages 每日信号与模拟组合看板
└── tests/                                 # 【5. 自动化测试集】50 个针对量化生产、执行、搜索与前端的单元/回归测试
```

---

## 二、当前线上生产策略速查

- **线上主力 Profile**：`c98856b4_top10_drop1`（定义于 [`quant/code/production/csi1000_production_config.py`](code/production/csi1000_production_config.py)）
- **模型 ID (`candidate_id`)**：`c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b`
- **持仓与换手规则**：`Top10 / Drop1`（持有排名前 10 只股票，每日最多调换 1 只，`T` 日收盘打分 -> `T+1` 日开盘成交）
- **历史回测表现（2025-01-02 ~ 2026-09-30，424 交易日扣费后净值）**：
  - **5 相位平均累计净收益**：**`+101.20%`**（`phase00` `+108.38%`，`phase15` `+130.90%`）
  - **5 相位中位年化 (CAGR)**：**`52.45%`**（最差相位 `34.44%`）
  - **5 相位中位 Sharpe**：**`1.757`**
  - **5 相位最深回撤**：**`-21.84%`**
  - 详见 [`quant/docs/00-strategy-handbook/STRATEGY_ATLAS.md`](docs/00-strategy-handbook/STRATEGY_ATLAS.md)。

---

## 三、常用命令

```bash
# 1. 运行我们自己的全套单元测试 (50 个测试文件)
.venv/bin/python -m pytest -q quant/tests

# 2. 部署线上生产定时任务到 Modal (at2018cow)
MODAL_PROFILE=at2018cow .venv/bin/modal deploy quant/code/production/modal_qlib_cn_a10g.py

# 3. 本地构建网站信号索引与前向净值
.venv/bin/python quant/website/build_index.py --signals quant/data/signals --output /tmp/available_dates.json
.venv/bin/python quant/code/ops/build_forward_performance.py --signals quant/data/signals --output /tmp/forward.json
```
