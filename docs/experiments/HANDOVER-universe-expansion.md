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
