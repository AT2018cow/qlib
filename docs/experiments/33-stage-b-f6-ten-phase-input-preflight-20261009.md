# 33 — Stage-B F6 十轨原始证据输入门禁（2026-10-09）

> **只读原始输入预检 / 不训练 / 不修改策略 / 不重写历史绩效。** 此阶段旨在解除 doc 30 的 F6 九轨缺失输入障碍，绝不是对原模型未来收益或已修复策略的认证。winner phase0 已有的 `Sharpe=0.998878` 历史诊断指标保持不变，不重新择参。

## 为什么现在做 F6

PR #45 的 CSI1000 F1 静态行情覆盖修复，只在冻结的 winner phase0 上回放和核算。PR #46 把该轨的 SHA、完整卖出数量、账户/手续费/换手验收变为失败即停止；PR #47 及随后的主干 F5 CI 已提供离线证据检查。

待审查的范围还有 **9 个**：winner 的 phase 4、6、10、15，以及 baseline 的 phase 0、4、6、10、15。这九轨都已经在同一个消费过的冻结 reserved tail 上生成了原始 report，但截至本 PR 尚未有九轨原始 `signal.parquet`、`decisions.json` 的仓库内完整校验与修复交易回放。**任何 F1 修复对九轨的 Sharpe、订单及真实成交影响均未知**，不能外推 winner phase0 的数值。

## 证据定位的关键风险

冻结完整 JSON 是唯一权威来源：

- 原 result commit：`7a2676397b0f8e6f69c0bffc98d1764647f644ac`
- snapshot token：`51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`
- winner ID：`4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`
- baseline ID：`23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`

两类候选都应恰有 phase **0、4、6、10、15**。**baseline phase0 不在普通 `phases/` 目录，而在 `_preflight/e56441cbfd5b4242/repeat_b/phases/`**；原始 `report_artifact.path`、`signal_artifact.path`、`decision_artifact.path` 均应直接从冻结 JSON 读取，禁止重建或推测路径。

已导出的十份报告源 SHA 另见 `results/csi1000_stage_b/audit_reports/manifest.json`。预检需要进行三个相互独立的比较：

1. GitHub 导出 manifest 的 `volume_source_path` 必须精确匹配冻结结果相同 candidate+phase 的报告源路径。
2. 冻结结果报告的 SHA 必须精确匹配导出 manifest 的 expected 和 actual SHA；导出文件也必须重新计算**真实字节** SHA，不可信任其 `match:true` 文本。
3. 若某轨的原始 signal/decisions 在已提供只读 snapshot root 中存在，就各自计算 SHA 并核对该轨的冻结 `signal_artifact.sha256`、`decision_artifact.sha256`；否则该轨标记 `BLOCKED_MISSING_ORIGINAL_SIGNAL_OR_DECISIONS`。如输入存在而 SHA 不符则 **FAIL 非零退出**，绝不按缺失跳过。

仓库已经单独保存 **winner phase0** 原始 `audit/evidence/winner_phase0/raw/signal.parquet`、`decisions.json`。仅这一个例外可被视作已验证的原始信号+决策。九轨不可使用 phase0 的文件替代。

## 新的可执行预检入口

```bash
# GitHub CPU 环境或本地：不用原 Modal，不用 Qlib/pandas
python -m unittest discover -s tests -p "test_stage_b_f6_preflight.py" -v
python -m audit.stage_b_f6_preflight

# 可选：在已取得合法授权的只读原始 snapshot 导出目录核对十轨字节
python -m audit.stage_b_f6_preflight \
  --repo-root . \
  --snapshot-root /path/to/read-only-extracted-original-snapshot

# 完整十轨所需原始信号/决策若尚缺失，必须非零退出
python -m audit.stage_b_f6_preflight \
  --snapshot-root /path/to/read-only-extracted-original-snapshot \
  --require-complete
```

`--snapshot-root` 应**对应完整原 snapshot 根目录**（也就是冻结 JSON 中 `/vol/csi1000_stage_b/<token>/` 的拷贝根）；子路径全部来自冻结 JSON，包含 `_preflight/.../repeat_b/`。不能把当前刷新过的数据包、修复版 phase0 report、Qlib 模型输出或现时 CSI1000 成分替代这些原始字节。

此工具不主动访问 `/vol`，不启动 Modal、不重训、不修改策略、无网络或私人凭据。默认在 stdout 输出完整结构化 JSON；只有用户显式指定 `--output-json` 时才会在仓库外**全新**目录写单个派生 JSON，拒绝覆盖旧输出或写入 `/vol`。原始股票信号/行情如有再分发许可限制，原件保留在授权实验环境；公开仓库只保存经允许的衍生摘要和哈希。

### 状态含义

| 字段 / 状态 | 可以证明 | 不能证明 |
|---|---|---|
| `report_hashes_verified=10` | 十份 GitHub 原始报告字节与冻结结果、导出 manifest 一致 | 十轨执行路径可交易或修复收益正确 |
| `source_pairs_ready` | 对应轨的**原始** signal、decisions SHA 均被实测核对 | 已在相同 Qlib provider 上回放 |
| `BLOCKED_MISSING_ORIGINAL_SIGNAL_OR_DECISIONS` | 原始轨输入仍未满足门禁 | 该轨策略好/坏 |
| `READY_FOR_FROZEN_SIGNAL_REPLAY` | 该轨已可进入下一步冻结信号执行回放 | F6 修复影响已测得 |
| `overall_f6=BLOCKED` | 没有得到十轨修复回放与独立执行核算证明 | 不妨碍继续研究性实验 |

当不提供额外 snapshot root 时，预期原始报告 **10/10 SHA 核验通过**、原始 signal+decisions **1/10** ready（winner phase0），其余 **9/10** blocked；准确状态由运行结果决定，**不得先行手填 PASS**。

## 此 PR 验收与下一步

- F60：只添加 `audit/stage_b_f6_preflight.py`、单测、独立 CPU workflow、本 runbook；原信号/报告/策略/生产逻辑零改变。
- F61：十份导出原始报告均逐字节验证，source pointer 与 frozen JSON 一一对应。最早发现的错误必须让 CI 非零退出。
- F62：winner/ baseline 各 5 轨，无误配或遗漏；baseline phase0 `_preflight/.../repeat_b` 特殊源路径被真实 JSON 严格核对。
- F63：缺失信号/决策保持 BLOCKED；存在但篡改的 report/signal/decision 触发 FAIL；`--require-complete` 在九轨未备齐时失败。
- F64：CI job 附来源 SHA、十轨状态 JSON artifact，明示 `overall_f6=BLOCKED`。

**下一工作项（不包括在本 PR）：** 利用此预检及只读导出的九轨原始 signal/decision，参数化 F1 old-vs-static-union Qlib 执行回放和第二套独立现金账，以每轨原始 report/decision/signal SHA 为唯一身份；每轨分别记录 legacy 重放、修复订单/NAV/Sharpe/CAGR 和市场条件。任何缺失原始 provider 交易数据或历史 ST/PIT 规则仍为 BLOCKED；不以已消费 tail 重新选择 winner，不部署生产。
