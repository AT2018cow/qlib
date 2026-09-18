# 实验文档导览

> Qlib A 股日频量化研究（2026-09）。本项目在 microsoft/qlib 之上构建了 Modal 云端研究管道，
> 完成了从信号验证到滚动终局评估的完整闭环。所有结论均基于实测数据，可复现。

## 重要声明

本仓库全部内容仅为**学术研究与实验性工程验证**（research-only）。所有信号、配置、结论
不构成任何投资建议（not investment advice）；不得将其中的任何信号或清单用于实际投资决策。
用户应自行判断并承担一切投资决策风险。

## 一页纸速览（终审版，截至 2026-09-18 —— 详见 [05-final-audit.md](05-final-audit.md)）

- **唯一实盘候选**：LightGBM + Alpha158 + 20日标签 + csi1000 + top20 等权 + nd2 每日微调（csi500 路线已关闭：滚动 +0.6%）
- **5.5 年滚动（23 窗口，严格无前视）**：年化有成本超额 **+7.5%**（点估计），正窗口 13/23，最差季 -20%
- **统计诚实**：p=0.18 不显著、95%CI [-8.2%, +23.2%]——"未证伪但未确立"，仓位必须按"信号可能无效"定价
- **计算可信度**：三重独立验证（含完全独立端到端复算，偏差 0.15pp）+ 前视/幸存者偏差排除
- **绝对收益 = 指数 β + 选股超额**：指数风险全自担，门控是唯一 β 管理工具
- **实盘定位**：卫星仓 ≤10-20%，先 3 个月 paper trading 积累前向样本
- **下一步**：前向验证 + P3 信息维度（RD-Agent 因子挖掘 / 消息面）

## 文档索引

| 文档 | 内容 |
|---|---|
| [01-findings.md](01-findings.md) | 研究结论全记录：环境、实验矩阵、各阶段结果（P0/P1/vcheck/P2/成本推算）、固化配置 |
| [02-roadmap.md](02-roadmap.md) | 工作路线图：P0-P3 全量清单、各项完成/关闭状态、剩余工作 |
| [03-risks-and-audit.md](03-risks-and-audit.md) | 风险条款、代码审计记录、资源使用规范 |
| [04-playbook.md](04-playbook.md) | 实盘操作手册：模型能力边界、月度流程、仓位与风控、预期管理 |
| [05-final-audit.md](05-final-audit.md) | **终审报告（最终基线）**：准确结果、完整分析过程、统计强度结论、操作启示 |
| [06-cron-setup.md](06-cron-setup.md) | 每日信号云端定时任务（Modal Cron）：架构、部署步骤、费用、验证与生命周期 |

## 数据

实验原始数据按批次归档于仓库根 `results/`：`p0/`、`p1/`（含 version_check）、`p2/`。
每批含 summary.json 与明细 csv/pkl，关键数值均与云端运行日志双重核对过。

## 快速命令（2026-09-18 终审版，全部经过验证）

```bash
# 1. 每日信号（自包含单容器：自动下载当日最新数据 → 训练 csi1000+top20+nd2 →
#    信号自动取回本地 results/signals/ 并 git 推送，无需任何参数、无需 Volume）
modal run modal_qlib_cn_a10g.py --best --daily
#    注意：不再需要 --force-data（每次运行必然使用最新数据）；Volume 仅供研究批并行使用

# 2. 完整训练+回测（同配置）
modal run modal_qlib_cn_a10g.py --best

# 3. 实盘前审计（涨跌停口径）——建议建仓前跑
modal run modal_qlib_cn_a10g.py::verify_integrity

# 4. 复现各阶段实验（历史记录见 01/05 文档）
modal run modal_qlib_cn_a10g.py --p0        # 分组单调性/IC衰减/分月/n_drop网格（csi500时代）
modal run modal_qlib_cn_a10g.py --p1        # 特征融合/40日标签/组合构造（已被后续部分修正）
modal run modal_qlib_cn_a10g.py --p2        # 7窗口滚动（被批次C的23窗口滚动取代）
modal run modal_qlib_cn_a10g.py --vcheck    # 数据版本/特征/幸存者验证
modal run modal_qlib_cn_a10g.py --batcha    # topk×nd 耦合 + 股票池粗筛
modal run modal_qlib_cn_a10g.py --batchb    # 训练起点/窗口模式/LGB超参（csi1000）
modal run modal_qlib_cn_a10g.py --batchc    # ⭐ 23窗口滚动终审（唯一裁判）
modal run modal_qlib_cn_a10g.py::independent_recheck  # 独立端到端复算
modal run modal_qlib_cn_a10g.py::bench_years          # 基准指数年度收益
```
