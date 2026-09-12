# Qlib 量化策略验证记录（2026-09）

## 环境与数据

- 计算：Modal serverless（A10G GPU / CPU 容器），入口 `modal_qlib_cn_a10g.py`
- 数据：chenditc/investment_data 每日 release（A股日频，qlib 格式），Volume `qlib-cn-data`
- 股票池：csi300 / csi500（含历史上全部成分股，按时间区间过滤），benchmark SH000300/SH000905
- 训练区间：recent 模式 2021-2024 训练 / 2025 验证 / 2026-01~09 回测；long_train 模式 2016-2024 训练

## 实验矩阵与结论

### 1. 标签周期（影响最大）

| 标签 | 模型 | Rank IC | 有成本超额(2026) |
|---|---|---|---|
| 2 日收益 | LGB+Alpha158 | 0.029 | -7.6% |
| 2 日收益 | GRU+Alpha158 (csi300) | 0.040 | -3.3% |
| **20 日收益** | **LGB+Alpha158** | **0.114** | **+1.7%** |
| **20 日收益** | **LGB+Alpha360** | **0.105** | **+6.5%** ⭐ |

结论：**标签周期是最大杠杆**。2 日标签噪声过大（IC≈0.03），20 日标签 IC 提升 3-4 倍。

### 2. 股票池 / 特征 / 模型

| 实验 | Rank IC | 结论 |
|---|---|---|
| csi300 → csi500 | 持平 | 无提升 |
| Alpha158 → Alpha360 | 0.114→0.105（IC 略降） | **回测收益大幅提升**（+1.7%→+6.5%），360 特征信号更均衡 |
| GRU/ALSTM vs LGB | LGB 明显更优 | RNN 在 20 日标签下训练不充分（early stop 早） |

### 3. 调仓 / 持仓（组合层）

| 实验 | 有成本超额 | 结论 |
|---|---|---|
| top50 每日微调（n_drop=2） | +6.5% | ✅ 最优 |
| top20 集中持仓 | +4.1% | ❌ 头部信号不更准，集中反而差 |
| 每 20 日硬调仓（PeriodicTopk） | -8.6% | ❌ 调仓点碰巧差，8 个月样本仅 ~8 次调仓 |

结论：**TopkDropoutStrategy 每日微调 + n_drop=2 在 20 日信号下实际换手低**，是正确组合方式。

### 4. Walk-forward 验证（1.7 年）

训练 2016-2023 / 回测 2025-01~2026-09：Rank IC 0.075（仍显著）、有成本超额 +1.0%（2025 基准大涨 +19%，跑赢大涨市困难）。**信号真实但收益衰减——公开方法年化超额 1-3% 是常态**。

### 5. 超参搜索 / 多周期融合 / 基本面因子（均为负面结果）

| 实验 | 结果 | 结论 |
|---|---|---|
| LGB 超参搜索 40 组 | Top1 0.1064 vs 默认 0.105 | 超参不是瓶颈，官方参数已近最优 |
| 20日+60日标签融合 | 有成本 -4.7% | 60 日标签模型是噪声源，不可行 |
| Alpha158+6 基本面字段（ROE/毛利率/PE/PB） | 无提升甚至下降 | 季度信息与 2 日标签周期错配；20 日标签下 PIT 粗糙（固定+4月）负贡献 |

## 固化最优配置（当前最佳）

```bash
# 每日信号（预测未来 20 日收益的 top50 候选池）
modal run modal_qlib_cn_a10g.py --daily --model lgb360 --label20 --topk 50

# 完整训练+回测（PortAnaRecord 输出 IC/回撤/超额）
modal run modal_qlib_cn_a10g.py --model lgb360 --recent --long-train --label20

# 等价 --best 快捷方式
modal run modal_qlib_cn_a10g.py --best
```

- 模型：**LightGBM + Alpha360 + 20 日标签（Ref($close,-20)/$close-1）**
- 训练：2016-2024（long_train），csi500 全成分（1802 只，按区间过滤）
- 组合：TopkDropoutStrategy topk=50, n_drop=2，每日微调（实际换手低）
- 信号后处理：剔除当日涨/跌停（≥9.5%）
- 2026-01~09 结果：Rank IC 0.105 / Rank ICIR 0.75 / 无成本超额 +8.4% / 有成本超额 +6.5%

## 未来方向（按优先级）

1. **消息面**：北向资金 / 融资融券 / 龙虎榜（akshare 免费接口，A股特有资金流 alpha，与 20 日周期匹配）
2. 基本面 PIT 精修（真实披露日期）后重试基本面因子
3. RD-Agent（Azure VM + Azure OpenAI）：LLM 自动因子挖掘（构建在 Qlib 之上）
4. 组合优化：EnhancedIndexingModel（预期一般，A股风险模型精度有限）

## 资源使用规范（Modal）

- Starter 计划：并发容器 100、GPU 并发 10
- **GPU 只挂给 RNN 模型（GRU/ALSTM/集成）**；LGB/数据/超参搜索全走 CPU 容器（挂载未使用 = 按挂载计费，浪费）
- 超参搜索等并行任务：`TUNE_WORKERS=8`（8 并发 × 16 核）
- 长任务建议普通 `modal run`（本地保持连接），不要 detach（本地断开后输入会被平台取消）