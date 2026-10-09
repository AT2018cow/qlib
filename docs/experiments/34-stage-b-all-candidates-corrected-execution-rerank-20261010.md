# 34 — 一次性重放 Stage-B 冻结 11 候选（执行规则修复后重新评估，2026-10-10）

**目标只有一个：判断原始候选在修复 F1 动态成分报价缺失后还是否值得保留；尽快决定是否需要重新选策略或另启新数据调参。**

## 已证实的历史风险

PR #49 已合并，`main` CI 真实复算十份冻结 Parquet：winner/baseline 各五个 phase 均存在至少 114 个连续交易日零换手，六个 phase 至少 240 日。**winner phase0** 已确认是指数剔除后报价消失导致的 F1 模拟缺陷，修复后 Sharpe 从 1.276262 到 0.998878、MaxDD 从 −12.13% 到 −27.64%。另九个 phase 的停换手**尚不能全部归因于 F1**，更不能由此推出修复后的绩效方向。

原冻结 Stage-B 包含 **11 名候选 × 5 个 phase = 55 组**。单看 winner 与 baseline 的十组报告，不足以证明另外九个候选没有更好表现。旧排名不得直接作生产晋级决策。

## 单次 CPU 实验命令（不训练，不导出私人原始数据）

此 PR 复用现有 `audit/replay_stage_b_f1.py` 的相同 Qlib 模拟器与原结果逐字节/逐订单重放门禁，**不修改**原模型、权重、信号、参数、成交费用、TopK/Drop2、样本日期和 benchmark。只把报价订阅从动态 CSI1000 改为原段历史成分**静态并集**。信号仍取原本冻结的各阶段 `signal.parquet`，不能把静态并集拿来选股。

需在**授权实验环境**准备：
- 原始只读 `cn_data` provider，原始 `provider_snapshot.json`；
- 冻结 `/vol/csi1000_stage_b/51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1/` 的只读完整副本，作为 `--snapshot-root`；**不要**把其他快照、更新后的模型、phase00 的信号拿来替换；
- Qlib、pandas、numpy、pyarrow 的兼容环境；运行仅为 CPU 研究回放，**不得**启动模型训练/GPU、写原 Modal Volume。

```bash
# 快速异常验证（winner+baseline，各5轨）；输出必须是全新独立目录
python -m audit.run_stage_b_corrected_rerank \
  --scope controls \
  --repo-root . \
  --snapshot-root /readonly/frozen-stage-b-snapshot \
  --provider-uri /readonly/original/cn_data \
  --provider-snapshot-file /readonly/original/provider_snapshot.json \
  --out-dir /tmp/stage-b-controls-f1-diagnostic

# 用完全同一原始快照作所有11候选×5轨的完整诊断排名
python -m audit.run_stage_b_corrected_rerank \
  --scope all \
  --repo-root . \
  --snapshot-root /readonly/frozen-stage-b-snapshot \
  --provider-uri /readonly/original/cn_data \
  --provider-snapshot-file /readonly/original/provider_snapshot.json \
  --out-dir /tmp/stage-b-all-55-f1-diagnostic
```

**为何可能失败：** 在开始 Qlib 回放之前，脚本一次性确认范围内全部 `report.parquet`、`signal.parquet`、`decisions.json` 的文件存在和字节 SHA 与原完整结果 JSON 一致；缺 1 个即非零退出，绝不拿修复后数据/其它候选替代。特别是 baseline phase0 会从实际 `_preflight/.../repeat_b` 源路径读取。再验证 provider token/fingerprint，且对每轨都要求 **旧模拟逐日与报告、旧订单逐字段完全复现**，否则停止，不接受新的比较结果。

**成功输出：** 每轨全新的 `fixed_diagnostic_report.parquet`、`fixed_diagnostic_decisions.json`、`comparison.json`，目录顶层 `batch_result.json`；该文件只有**全范围顺利完成后**才生成。controls 范围必须标记 `BLOCKED_NOT_ALL_11_CANDIDATES`；all 范围调用**原有 `rank_stage_b_candidates` 排序契约**获得 11 候选诊断性新顺序，不能偷偷换排序规则、单看某一期选赢家或修改生产 winner。报告包含旧/新 Sharpe、CAGR、订单数、NAV 分歧日和来源 SHA。

## 正确的决策

- **旧排名失效（已确认）。** winner phase0 的旧成交口径重大失真，且十份旧报告存在普遍停交易异常。
- **是否需要重选 winner（待全量执行）。** 若全部 55 轨通过原始执行复现和修复回放，可比较诊断性新序列；这仍是已经使用过的 reserved tail，**不构成全新的未污染样本，更不自动批准晋级**。
- **是否需要重调参（现在 NO）。** 禁止为提高这个已消耗 tail 上的 Sharpe 而再次搜索参数。只有修复后原候选整体不符合**事先明确**的可执行性和稳健性标准，并有真正新的未使用验证区间与预算审批，才讨论新调参。
- **真实交易仍 BLOCKED。** 现有报告缺乏可独立证实的历史 ST、停牌、IPO 规则、开盘流动性及 20-session 标签成熟/PIT 时点信息；脚本是 Qlib 诊断性执行回放，尚非第二套独立按开盘价、费用、持仓的账本。不能仅凭旧/新指标匹配宣称实际市场收益。

**审计范围控制：** 只一份批量执行入口、负控、CPU 工作流与本简短操作说明；后续不要再因为输入门禁拆多个 PR。原始数据无法从当前公共 GitHub 仓库取得时，明确 `BLOCKED`，并在授权本地执行上面的一次性命令，而非在公共仓库提交私人 provider 或冻结快照。
