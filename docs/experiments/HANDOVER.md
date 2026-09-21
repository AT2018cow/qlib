# 工作交接文档（2026-09-19）——面向下一步 RD-Agent / 消息面工作

> 本文档面向新对话的接手者，概括本对话完成的全部工作、当前系统状态、已固化的结论、以及下一步（P3）的启动要点。

## 一、项目概览

**仓库**：`AT2018cow/qlib`（fork of microsoft/qlib，public）
**目标**：A 股日频量价研究 → 实盘 paper trading → 寻找信息增量
**已完成阶段**：qlib 范围内的全部研究、验证、自动化收尾；下一步是 P3（RD-Agent / 消息面）

## 二、当前系统架构（正在运行）

### 生产系统（at2018cow workspace，已部署且稳定）

```
Modal Cron：每个 A 股交易日 07:00（北京时间）
  → daily_standalone（nonpreemptible=True，8核32G CPU 容器，无 GPU）
    1. 下载 chenditc/investment_data 最新全量包（565MB，数据必最新，无陈旧问题）
    2. 训练/推理：
       - 每 20 个交易日重训一次终审候选模型
       - 其余日复用 /vol/live_models/ 缓存模型（SHA-256 校验，处理器拟合窗口锁定）
    3. 生成 csi1000 top20 排名 CSV（ranking_only，非可执行订单，涨跌停已过滤）
  → daily_cron（nonpreemptible=True）
    4. 严格日期检查（节假日/数据滞后显式失败）
    5. GitHub API 推送到 results/signals/<日期>_top20_lgb158.csv（同日记录不可变）
```

- **成本**：月约 $7.1（nonpreemptible 3x 价）
- **Secret**：`github-push`（含 GitHub PAT，fine-grained 只授权本 fork Contents 读写）
- **首次运行**：infi workspace 无 secret、at2018cow 无 `/vol/live_models`——首次运行会自动重训并建缓存

### 终审候选配置（唯一实盘路径）

```
LightGBM + Alpha158 + 20日收益标签 + 2016起 expanding 训练
+ csi1000 全历史成分 + top20 等权 + n_drop=2 每日微调
```

## 三、最终结论基线（新对话必读）

### 3.1 核心数字（post-purge 新口径，2026-09-19 重跑）

| 配置 | 5.5年滚动年化超额 | 统计强度 |
|---|---|---|
| **csi1000+top20/nd2**（唯一候选） | **+11.6%** | t=1.44，**不显著** |
| csi500+top20/nd3 | -4.1%（批次C）/-4.7%（P2）双重收敛 | **路线关闭** |

### 3.2 重训频率实验（第六步已完成）

- freq=20: +12.7% / IR 0.74 / MDD -32.8% / 正日比例 50.4%
- freq=60: +10.5% / IR 0.65 / MDD -27.2% / 正日比例 49.9%
- **差异在噪声带内，维持生产 20 日频率不变，无需跑 5 日档**

### 3.3 必须记住的统计事实

1. **+11.6% 与 +7.5% 不可区分**（窗口级相关 0.918，逐季差 0.7pp）
2. **信号未确立**（t=1.44，p≈0.08），仓位按"可能无效"定价（卫星仓 ≤10-20%）
3. **正日比例约 50%**——收益靠盈亏比不靠胜率
4. **小盘风格暴露**——2025 年 -12% 是风格反转的真实剧本，size 风格门控必须执行
5. 绝对收益 = 指数 β + 选队超额；A 股无法做空对冲，门控是唯一的 β 管理工具

## 四、验证方法论（已固化为纪律）

| 原则 | 来源教训 |
|---|---|
| 单段年化数字必须绑定窗口比较 | 区间末端效应：差3天可摆动6个点 |
| **IC ≠ 赚钱** | 三次实证（40日标签 IC 0.137 但收益 -6.8%；Alpha158 IC 高 4 倍收益反低） |
| **任何好得意外的数字先做前视审计** | 批次C +15.3% 查出 train_end 泄露 test 头部，修复后掉半 |
| 优化目标 = 有成本超额收益，非 Rank IC | |
| 先验证信号真伪再优化执行 | 若 P2 早做可省部分实验 |
| 弱信号的生死在成本假设 | 费率 0.04%~0.2% 决定期望 ±2% |
| 不要把"测过的配置天花板"外推为"信息源天花板" | 对 RD-Agent 的判断错误曾因此撤回 |

## 五、已修复的关键 bug 清单（新对话避免重蹈覆辙）

| Bug | 影响 | 修复位置 |
|---|---|---|
| 涨跌停过滤双重错误（公式 close/open + index 对齐） | 过滤从未生效，"已剔除0只"是假象 | `_daily_impl` 已修复并实证（09-16 剔除7只） |
| batch C 窗口 train_end 泄露 test 头部 | +15.3% 虚高 7.7pp | `_gen_5y_windows` 修复，批次C重跑 |
| `--daily` 不带 `--long-train` | 实盘信号用了短训练模型 | `--best` 固化入口解决 |
| `qlib_audit_fixes` 镜像内不可导入 | 部署后 ModuleNotFoundError | `run_commands cp` 修复 |
| Modal 容器抢占循环 | prepare_data 两次超时 | `nonpreemptible=True` + skip_health |
| `skip_if_reg` 缺失 | 容器复用时 qlib 重复初始化报错 | tune_one/freq_window 修复 |

## 六、关键基础设施状态

### Modal Workspaces

| Workspace | 用途 | 状态 |
|---|---|---|
| **at2018cow** | daily cron 生产 | ✅ 已部署（nonpreemptible），余额充足 |
| **infi** | 第六步频率实验已用 | qlib-cn-data Volume 存在，无 github-push secret |

### Modal Volume (`qlib-cn-data`)

- `cn_data/`：chenditc A股日频数据（最新 09-18 版本）
- **app-only 数据源**（实证零修订、真实历史成分、无幸存者偏差）

### 基准指数年度收益（绝对收益 = 指数 + 超额 用）

| 年份 | 中证500 | 中证1000 |
|---|---|---|
| 2021 | +13.5% | +17.8% |
| 2022 | -20.3% | -21.3% |
| 2023 | -8.8% | -8.4% |
| 2024 | +5.9% | +1.8% |
| 2025 | +34.6% | +31.0% |

## 七、下一步工作（新对话的起点）

### 7.1 P3 选项与优先级

**选项 A：RD-Agent（Azure）**——LLM 自动因子挖掘
- **价值修正已做**：原判"量价层面帮助有限"**已被撤回**（论文 NeurIPS 2025 样本外数据强：CSI500 2024-25 测试 IR 2.17 vs Alpha158 0.25）
- **正确用法**：RD-Agent 挖新因子 → **用本项目的滚动验证框架复验** → 通过者进入实盘候选池
- 前置条件：Azure VM（用户有 credits）+ Azure OpenAI（用户已确认有）+ Docker
- 部署方案：见 `02-roadmap.md` P3-19 条目与 `05-final-audit.md` 4.3 节

**选项 B：消息面因子**——A 股特有资金流 alpha（akshare 免费接口）
- 北向资金 / 融资融券 / 龙虎榜
- 可复用 `build_fund_factors` 模式（akshare → QLib bin → 接入训练）

**建议**：先 RD-Agent——样本外证据等级最高，且用户已有 Azure credits。

### 7.2 前向 paper trading（无需操作，在后台自动进行）

cron 每日自动积累，**3 个月后** `results/signals/` 的完整序列 + 实际对账 = 最终审判。这是唯一不依赖历史假设的证据，比任何回测延长都有价值。

### 7.3 新对话需要知道的三个操作事实

1. **实验跑在哪个 workspace**：日常信号在 at2018cow，新实验建议在 infi（余额）——注意 secret（github-push）只在 at2018cow 有，跨 workspace 需重建
2. **数据永远是每次全量下载**：`daily_standalone` 每次运行下载最新 566MB——不存在"忘记 force-data"问题；研究批用 Volume 数据时需 `--force-data`
3. **镜像构建约 2 分钟**（已缓存），容器启动秒级——实验循环成本极低

## 八、文件与复现速查

```
主代码:      modal_qlib_cn_a10g.py        # 全部 Modal functions + CLI 入口
频率实验:    freq_experiment.py           # 独立实验文件（不依赖 Secret，可跨 workspace）
泄漏防护:    qlib_audit_fixes.py          # 成熟期数学 + purge + fail-closed（9单元测试）
重训机制:    qlib_live_retrain.py         # 20日重训调度 + 缓存签名 + configure_asof
审计工具:    ::verify_integrity           # $change 语义 + 过滤口径 + 每日涨停数
独立复算:    ::independent_recheck        # 手写配置端到端复算（偏差 0.15pp 验证通过）

文档体系:    docs/experiments/README.md   # 速览 + 索引
             01-findings.md              # 全实验矩阵与结论
             02-roadmap.md               # 路线图终态 + 7条经验教训
             03-risks-and-audit.md       # 风险条款 + bug 审计
             04-playbook.md              # 实盘操作手册（预期管理）
             05-final-audit.md           # 终审报告（最终基线）
             06-cron-setup.md            # cron 部署指南
             07-live-retrain.md          # 重训机制

原始数据:    results/p0|p1|p2|batch_a|batch_b|batch_c|freq_experiment/signals/
复现入口:    --p0 / --p1 / --p2 / --vcheck / --batcha / --batchb / --batchc / --best --daily
```

## 九、免责声明

本项目仅为学术研究与实验性工程验证（research-only, not investment advice）。任何信号、配置、清单不得作为实际投资决策依据；用户需自担全部投资风险。

---
**交接人**：本对话全部工作已完成并推送至 `AT2018cow/qlib` main 分支（HEAD: `6fb30849`），工作区干净。
