# 22 — 独立对话交接：科创板与创业板模型研究（2026-10-08）

> **本文件是“新股票池 / 新模型”对话的唯一启动入口。**
> 网页设计另见 [23-web-ui-handoff-20261008.md](23-web-ui-handoff-20261008.md)。
> 上一份综合交接 [21-next-conversation-handoff-star-chinext-20261008.md](21-next-conversation-handoff-star-chinext-20261008.md) 是研究背景资料，不再作为两条并行工作的共同执行入口。

## 1. 状态与边界

- 仓库：`AT2018cow/qlib`，撰写基线 `main` merge commit `68cfde375dd29f70fb94a901bc2152ff750bc4d4`（PR #34 已合并）。新的对话必须重新检查实时 `main`，不能假设此 SHA 永久有效。
- **CSI1000**：唯一当前 canonical production；Stage-A / Stage-B 已结束并冻结。模型为 Alpha158 + LightGBM，Top20/Drop2，20-session retrain，`T close → T+1 open`。
- **ChiNext / 创业板**：旧模型研究基础设施仍保留，但旧模型每日 cron 更新和公开页面入口均已暂停。
- **STAR / 科创板**：corrected prediction reproducibility gate 未过，alpha 结论仍为 **unknown**；禁止把历史负收益误写成已经证明无 alpha。
- 生产 Modal workspace 的部署状态没有从本次对话直接核验；GitHub 合并或 CI 绿色 **不代表**线上 Modal 状态已被验证。

## 2. 冻结的 CSI1000，不在此对话重做

- Stage-A 搜索 80 个候选；最终 winner 是 Stage-A rank 5、Stage-B rank 1。
- Stage-B 使用 `2025-01-02` 至 `2026-09-30` 的 424 execution sessions；11 candidates × 5 retraining phases，phases `[0,4,6,10,15]`。
- Winner 最差 relative excess CAGR `+9.16%`、中位数 `+14.25%`、5/5 phases 为正。这是历史确认数据，不是未来收益保证。
- 已使用过的 Stage-B confirmation tail 不能再次当成新 OOS 调参或挑选门槛；只有具体实现/数据完整性缺陷才能启动重新审计。
- 冻结生产协议：2016-01-01 起 expanding train，252-session validation，20-session purge，每 20 sessions retrain、原点 2026-09-18；确定性 tie-break、board/date-aware 限价、CNY tick=0.01、无 5% high-open overlay。
- 生产配置和精确 hash 以 `csi1000_production_config.py`、研究文档 19/20、冻结结果清单为准。不要改写已存在的 canonical artifacts。

## 3. 科创板：第一优先级是 reproducibility

**已知状态**

- Corrected STAR prediction reproducibility gate：**FAIL**（prediction-chunk equality / deterministic lineage）。
- 现阶段不能可信地比较候选模型的 alpha；不要先做大规模模型搜索。
- 旧研究中涉及 STAR 的负收益/其他数字只能作为历史诊断线索，不能用作正式否定结论。

**推荐第一轮任务**

1. 阅读 `docs/experiments/14-pre-tuner-stage-summary-20261007.md`、`12-pre-tuner-audit.md` 和相关 audit 脚本；列出失败 gate 的精确定义。
2. 在可追溯、固定的 provider snapshot / universe / runtime 下重新复现 mismatch；明确对比训练标签成熟度、universe membership、特征数组、seed、worker/chunk 合并和输出排序。
3. 找到最小触发条件，增加失败前就能复现的 regression test；只修与 mismatch 有因果关系的实现。
4. 相同 frozen 输入独立重复运行至少两次，验证 chunk prediction / full-signal hash 与 manifest 一致。
5. **gate PASS 后**再制定 STAR 模型/portfolio 候选计划，不要以收益高低代替 reproducibility 证据。

## 4. 创业板：新一代模型研究，不恢复旧策略

- 旧 top20/nd3 corrected reproducibility：**PASS**。
- 旧模型 sampled phases `0 / 5 / 10 / 15` relative CAGR：`-1.00% / -3.72% / -8.99% / -10.82%`。
- 旧 Batch C / rolling 的 +11% 级结果已被后续 corrected 执行、metrics 和 gates supersede，不能作为当前生产准入证据。
- 下一代 ChiNext 应建立**新的有版本号的研究合同**：先明确要修改 model/label/features/window/weights/portfolio 中的哪一层，再限定候选数量和预算。
- 保留旧模型作为可选对照，而不是继续在旧冻结模型上无限 post-hoc 调参。
- 统一采用 corrected no-lookahead / T-close-to-T+1-open / account-level metrics / transaction costs / board-specific limits 语义；独立冻结选择规则，再开展跨时间和重训 phase 稳健性评估。
- 2026-09-30 及以前许多历史区间已被研究过程看过：可以用于开发/交叉验证，但**不可以改名为全新未见 OOS**。需要未来独立数据确认时，应预先保留或积累真正 forward 观测。

## 5. 两条研究线可并行，但上线必须独立审批

- STAR determinism 修复与 ChiNext 研究合同制定可以并行，彼此不要求先后完成；但 STAR 模型筛选必须在它自己的 gate PASS 以后。
- 两个股票池分别记录 universe、benchmark、cost、trading limits、labels 和 OOS 使用史；不套用 CSI1000 收益来“证明”卫星池表现。
- 先研究、后确认、再单独发起 production activation PR；任何 STAR/ChiNext 新模型不应自动进入 scheduled cron，也不应自动出现在网页。
- 恢复生产前必须与网页对话明确 artifact schema、独立 lineage、数据 freshness gate、paper state 隔离、模型版本展示和方法论文案。

## 6. 代码所有权及与网页对话协作

**本研究对话优先负责**：`board_rules.py`、`board_execution.py`、`satellite_audit_core.py`、`satellite_pre_tuner_audit.py`、与 STAR/ChiNext 有关的研究脚本、测试和研究合同。

**网页对话优先负责**：`website/**`、网页构建/展示、纯前端状态和数据可视化。研究对话不要顺手重写网站或公开开启股票池。

**共享/高风险文件须协调**：`modal_qlib_cn_a10g.py`、`paper_portfolio.py`、`chart_series.py`、`forward_performance.py`、`results/signals/**`、`.github/workflows/**`。

- 分别从最新 `main` 开 topic branch，不在同一个分支叠加两条工作的提交。
- 任何共享文件变更先说明对另一对话的影响、给出 JSON/CSV contract 与兼容策略，再独立 PR 审查。
- 不覆盖历史 artifacts；不删除旧研究记录；GitHub 合并不等于已完成 Modal 生产部署。

## 7. 推荐查阅文件

```text
docs/experiments/14-pre-tuner-stage-summary-20261007.md
docs/experiments/12-pre-tuner-audit.md
docs/experiments/21-next-conversation-handoff-star-chinext-20261008.md
docs/experiments/16-csi1000-stage-a-tuning-contract-20261007.md
docs/experiments/19-csi1000-stage-b-final-audit-forward-contract-20261008.md
docs/experiments/20-csi1000-stage-b-winner-canonical-promotion-20261008.md
board_rules.py
board_execution.py
satellite_audit_core.py
satellite_pre_tuner_audit.py
tests/
```

## 8. 第一轮验收与交付

- 先提交一份**诊断报告/研究计划**：列清 STAR reproducibility fail 的证据、ChiNext 版本化研究合同草案、预计修改文件和运行预算。
- 对缺失或无法访问的 provider/run artifacts 明确写“未核实”，不得伪造 gate PASS 或前向收益。
- 实施修改后必须有对应 deterministic/regression tests 和可复现 manifest，开 PR 给用户合并；不要未经授权部署。
- 公共仓库不得提交访问凭据、个人/私有 workspace 名称、账户细节或私有路径。

## 9. 新对话第一条 Prompt（可直接复制）

> 请在 `AT2018cow/qlib` 仓库开展 **STAR（科创板）与 ChiNext（创业板）新模型研究**。先阅读 `docs/experiments/22-handoff-star-chinext-research-20261008.md`、`AGENTS.md` 和其中列出的核心历史审计文档，检查当前 `main` 与 CI。CSI1000 Stage-A/B 已冻结，不得重新调参或修改生产信号。优先重现并定位 STAR reproducibility FAIL；并行制定 ChiNext 下一代模型的版本化、有限搜索研究合同。先交付证据清单、修复计划、验证门槛和文件改动范围，再按可验证的小步提交 PR。不要恢复两个股票池的 cron 或网页入口，不要将旧研究历史当成新 OOS，也不要触碰网页设计，除非我们明确讨论跨团队接口。

---

本交接仅为研究与工程协作文件，不构成投资建议。
