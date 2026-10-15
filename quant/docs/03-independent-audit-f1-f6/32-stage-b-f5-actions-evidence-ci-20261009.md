# 32 — Stage-B F5 GitHub Actions 可观测性与已提交证据 CPU 回归（2026-10-09）

> **只做 CI 与只读诊断，不触碰模型、策略、Modal、生产状态及冻结工件。** 前序 PR #45 修复动态成分导致的退指出价冻结；PR #46 加固 F5 原 provider 的审计门禁，并已发布 `audit/evidence/winner_phase0_fixed/f5_hardened_manifest.json` 与 `f5_hardened_summary.json`。两者尚不等于真实市场成交认证。

## 未完问题与约束

2026-10-09 GitHub API 对最新 F5 push 提交及对照 tuner 提交均返回 **0 个 check-runs**；Actions runs 列表最新可查询记录停在 2026-10-08。工作流已监听 `push: main`，但单靠修改受监听文件不能证明 GitHub Actions 实际收到、接受或运行了事件。**原因未知**，可能涉及仓库 Actions 设置、权限/配额/策略、平台事件处理或可观察性；不能从 0 条记录推断特定根因。

本 PR 不声称可以通过提交代码自动修复仓库级 Actions 禁用、权限和配额问题，也不自行改变仓库 Settings。

## 具体变更

1. F5 工作流增加 `workflow_dispatch:` 手动入口；`pull_request`/ `push` 监听审计门禁、验证脚本、负控测试和固定轨审计证据，主干 `main` 与本 PR 分支均在 `push` 触发范围。保持 `permissions: contents: read`。
2. `audit/verify_f5_committed_evidence.py` 只读已提交的文件，**标准库即可运行**，不引入 Qlib/Modal/训练或商业行情访问：
   - 验证 manifest + summary 中对应的 frozen signal、report、decisions、calendar、index spans 的 SHA256 与 GitHub 文件实际字节，核查 provider fingerprint 一致性陈述；
   - 对 424 个决策日和 1,696 个订单做日期、代码、方向、成交量、数量完整性检查；按顺序重建持仓数量与前一持仓卖出量；
   - 独立从 `execution_orders.csv` 汇总每日费用与换手，校对逐日累计值、账户现金+持仓、独立账本账户和每日毛/净收益；
   - 重新计算 238 交易日年化、样本标准差的 Sharpe、按实际日历时长年化 CAGR、MaxDD 和波动率；核实原 240 天零成交区间在 fixed 为 950 单；
   - 强制保留 `execution_integrity=BLOCKED`、`overall=BLOCKED`。原始 `fixed_market_data.parquet` 没有提交；脚本**只核验 manifest 的 SHA 声明内部一致**，绝不假称重新下载或重算实际 provider 的哈希。
3. `tests/test_verify_f5_committed_evidence.py` 校验 committed 正例，并复制到临时目录注入 4 类负控：报告字节篡改、行情 SHA 声明不一致、订单填单量篡改、逐日 NAV/决策匹配篡改。负控应非零失败。
4. 工作流运行完成后通过 `GITHUB_STEP_SUMMARY` 公布验证摘要，并以 `f5-published-evidence-check` 为名上传 JSON artifact（保留 30 天）。只有**真实的** Actions URL、job conclusion=success 和 artifact 可以证实 CI 通过。

## 本地独立入口（现有仓库文件即可，不需权限凭证）

```bash
python -m py_compile audit/verify_f5_committed_evidence.py
python -m unittest discover -s tests -p "test_stage_b_f5_gates.py" -v
python -m unittest discover -s tests -p "test_verify_f5_committed_evidence.py" -v
python -m audit.verify_f5_committed_evidence
```

预期标准输出应有 `"status": "PASS_PUBLISHED_SIMULATION_EVIDENCE_ONLY"`、`"days": 424`、`"orders": 1696`、`"stall_fixed_orders": 950`、`"market_execution": "BLOCKED"`。**不可**把纯离线检查当成原始 provider 数据可信性或真实盘中成交的完整证明。

## GitHub Actions 操作与验收

工作流文件 [`.github/workflows/csi1000-stage-b-f5-gates.yml`](../../.github/workflows/csi1000-stage-b-f5-gates.yml)。

- PR 处于 Draft 时，检查 PR 页下方是否出现 `CSI1000 Stage-B F5 evidence fail-closed gates`。若完全没有，去仓库 **Settings → Actions → General** 检查 Actions/第三方 Action 使用策略与必要权限，再查看 Actions 运行历史和账单配额；如无管理权限则交由仓库管理员，不通过修改策略代码代替。
- `workflow_dispatch` 只有在 GitHub 默认分支存在相应工作流且仓库允许 Actions 时才会在 Actions 页显示 **Run workflow**。合并后选择 `main` 手动运行，检查日志、状态、结果 JSON artifact 的 commit SHA/时间；**不要因为按钮存在就认定通过**。
- 若测试失败，先修复可复现的文件/运行环境问题；如果仍没有 workflow run，记录仓库设置/平台阻断理由，状态标记为 `CI_NOT_OBSERVED`，而非测试通过。
- 若 GitHub 确认有稳定成功记录，考虑把 `f5-published-evidence` 配为相关 PR 的 required check。首次运行前不要盲目启用 required check 造成合并死锁。
- 本 PR 在没有 run URL 前保持 Draft；无法通过本 GitHub 连接调用 GitHub Actions 的 Dispatch API，因此**未触发手动运行**。

| 验收 | 标准 | PR 创建时状态 |
|---|---|---|
| C0 隔离范围 | 仅审计证据验证脚本、测试、Actions、文档；生产、冻结及既有审计文件未改 | 待最终 diff |
| C1 代码安全性 | stdlib only；只读，无 Qlib/Modal、无 Volume 写入 | 已实现，待 CI |
| C2 已发布证据正例 | 424/1696/950，哈希、订单、账户、费用、Sharpe 等匹配且 `overall=BLOCKED` | 脚本已提交，待真实运行 |
| C3 负控 | 报告 bytes / market claim / filled_qty / NAV / decision 标签改动后拒绝 PASS | 测试已提交，待运行 |
| C4 平台 CI | `main` 或 PR workflow run 结论为 success，有可引用日志和 artifact | **NOT OBSERVED** |
| C5 外部真实性 | provider 数据来源/历史 ST、停牌、盘口容量、PIT 特征/标签及其余 9 phase | **BLOCKED** |

无论 C0–C4 结果，现有修复版 Sharpe `0.998878` 只是被独立复算过的**历史模拟指标**，未来收益 `INCONCLUSIVE`。
