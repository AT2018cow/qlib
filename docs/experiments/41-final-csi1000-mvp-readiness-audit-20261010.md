# 41 — 最终 CSI1000 审计收尾 / MVP 进入门槛：一次完整盘点实验（2026-10-10）

> **本 PR 的退出条件不是“所有历史订单均真实成交”。** 目标是：对冻结 baseline phase 0 的 424 天持仓，**一次性列出全部异常**，如无未证实缺价则立即进行独立现金/NAV 核对；并只读检查已有研究型 MVP 产物的模型/日期合同。所有不能由本 PR 证明的事仍明确 BLOCKED。禁止此后因零散历史缺价无限追加小修复 PR。

## 一、修改范围与不可跨越的边界

- 从合并后的 `main` 的 PR #53 与已记录的真实 Modal BLOCKED 证据 `audit/evidence/stage_b_closeout_baseline_p00/` 开始，不覆盖或改写这些证据。
- 原始 provider、冻结 `batch_result.json`、决策/订单、55 组报告完全只读；绝不重训、调参、改动生产 canonical 或自动执行实际订单。
- 新的 `audit/stage_b_quote_inventory.py`：按 `decision_index` 逐日重建冻结订单后的**持仓集合**，遍历完整报告日历的每一天、每只持仓，独立盘点持仓缺价、停牌与当日成交报价异常、ST 卖单警示。不会在第一条异常处退出；**全量清单含全部日期和代码，不包括原始 OHLCV/资金明细**。遇到非法卖出等结构性不可能的决策仍立即拒绝，以免生成虚假的后续持仓序列。
- 只有两条**官方公告确证**的停牌日期允许采用 Qlib 既有持仓估值语义：
  - `SZ000488 / 2025-02-20`：[晨鸣纸业深交所公告 2025-006](https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-19/b0f90573-61df-4bcc-953f-edf8738c84e1.PDF)。2025-02-21 开始 ST，5%。
  - `SZ002214 / 2025-04-28`：[大立科技巨潮资讯原公告 2025-026](https://static.cninfo.com.cn/finalpage/2025-04-26/1223329081.PDF)。2025-04-29 开始 *ST，5%。
- **不能**把任何第三个缺价日自动判断为停牌；不能 `fillna`、`ffill` 所有股票；不能从今天的 ST 名称倒推历史 ST 状态；不能把公告证实的“停牌”误当成“复牌卖单必然成交”。
- 原 `audit/verify_stage_b_fixed_ledger.py` 在独立 NAV 核算前强制先运行完整盘点；有任一 `blocking=True` 项即形成 `audit_failure.json`，其中含**全量异常清单**，在 Modal 中原样带回本地；没有阻塞项才继续核对原有现金/价值/费用/净收益（原容差不变）。
- 新 `audit/mvp_entry_review.py`：只读核查 **已有 paper artifact 的候选 ID / 生产 lineage / 信号日 / 使用日 / model_fit_asof / pending 订单日期绑定**；可选择额外输入来源明确、涵盖日期的权威交易日历。静态门禁**不等于线上部署验收**，不会自动标记 `mvp_activation_approved=true`。

## 二、CI / 本机合成验证（无私有原 provider）

PR CI 将运行 `py_compile` 和两个 unittest 文件，并检查已提交的 2026-10-09 paper 产物“身份合同”，但**没有证明这份产物是最新运行的信号**。

```bash
python -m unittest discover -s tests -p 'test_verify_stage_b_fixed_ledger.py' -v
python -m unittest discover -s tests -p 'test_stage_b_mvp_closeout.py' -v
python -m py_compile audit/verify_stage_b_fixed_ledger.py audit/stage_b_quote_inventory.py \
  audit/mvp_entry_review.py audit/modal_stage_b_ledger.py

# 本地输出需使用全新文件名，禁止把运行结果写到仓库或 /vol。
python audit/mvp_entry_review.py \
  --paper-artifact results/signals/2026-10-09_paper_portfolio.json \
  --output /tmp/csi1000-mvp-static-review-UNIQUE.json

# 仅当你能证明本地日历出自相应版本的权威 provider，并覆盖 2026-10-09，
# 才可额外传 --calendar-file /readonly/appropriate-provider/calendars/day.txt 。
# 不要用 Stage-B 截止 09-30 的旧日历验证 10-09 发布。
```

### 静态结果必须区分 3 个层次

- `static_artifact_contract=PASS_STATIC_ONLY`：文件自身的身份与日期自洽，**不是**最新 Modal 生产部署通过。
- `supplied_calendar_gate=PASS_SUPPLIED_CALENDAR_ONLY`：若明确提供**真正来自所审版本**且覆盖对应日期的交易日历，仅证明 T 收盘→T+1 开盘的交易日语义；**不等于**行情数据新鲜度通过。
- 始终 `mvp_activation_approved=false`；`deployed_runtime=NOT_VERIFIED`、`training_label_maturity=NOT_VERIFIED_FROM_THIS_ARTIFACT`、`current_st_suspension_status=NOT_VERIFIED`、`real_fill_execution=NOT_CERTIFIED`。这些需要**下一轮 MVP 端到端运行证据**，不能用静态 artifact 替代。

## 三、唯一私有 provider 审计实验：Modal 单 worker

**何时运行：** PR 合并、`main` 同步后；或者 PR HEAD 运行，但必须把精确 HEAD SHA 写入结果，不能把“PR CI 绿灯”当成原始 provider 回放。实验人员应有已授权的原始 `qlib-cn-data` Modal Volume，不能用别的年份、演示数据或 GitHub Actions 模拟替代。

```bash
set -o pipefail
git fetch origin && git checkout main && git pull --ff-only
git rev-parse HEAD | tee /tmp/csi1000-final-audit-head.txt
python -m unittest discover -s tests -p 'test_stage_b_mvp_closeout.py' -v \
  2>&1 | tee /tmp/csi1000-final-audit-local-tests.log

# 最终全日历扫描 + 若扫描无 BLOCKER 再核算账本；不可复用已存在输出路径。
modal run audit/modal_stage_b_ledger.py \
  --candidate baseline --phases phase0 \
  --output /tmp/csi1000-final-audit-baseline-p00-UNIQUE.json \
  2>&1 | tee /tmp/csi1000-final-audit-modal-UNIQUE.log

# 非零退出也先检查 JSON（含 audit_failure 的全量 inventory），不要覆盖重跑。
sha256sum /tmp/csi1000-final-audit-baseline-p00-UNIQUE.json
```

**仅 4 CPU / 16 GiB / 0 GPU / 单容器 / 60 分钟；不训练、不部署、不触碰 Volume 文件、不 `vol.commit()`、不并行跑其它候选。** 延用 `audit/modal_stage_b_ledger.py` 的原始快照位置 `/vol/csi1000_stage_b/provider_snapshot.json` 和 `/vol/cn_data`。Modal 镜像如缓存未命中可重新构建；不得因此调整 provider 身份。

**请勿在终端逐字输入 `UNIQUE` 作为覆盖既有结果的固定文件名**；可使用一个可追踪且未占用的后缀，记下实际文件名。

## 四、实验必须提交供审查的证据

| 证据 | 必需条件 | 内容 |
|---|---|---|
| `csi1000-final-audit-baseline-p00-*.json` | **必需**，若有输出 | 完整 JSON 原字节；**BLOCKED 时尤其必须包含** `inventory_cells[].inventory.issues` 的完整日期/股票列表、blocking count、ST 风险和失败原因；PASS 时包含 `cells[0].full_calendar_quote_inventory`、逐日差异及指标比较。 |
| Modal CLI 日志 `*.log` | **必需** | 保留容器退出码与最后异常，去除个人路径/凭据/可疑敏感元数据；不能删去实质性异常行。 |
| `git rev-parse HEAD` 完整 SHA、PR CI URL | **必需** | 确认正在运行的审计代码身份；不要只报 PR #。 |
| 输出 JSON SHA256 | **必需** | `sha256sum` 原输出，核对收尾结果文件身份。 |
| 静态 MVP 审查 JSON | **必需** | `audit/mvp_entry_review.py` 生成，注明纸面 artefact 的日期与提供日历与否；不得将 static pass 标记为上线 pass。 |
| 实际每日运行 smoke、provider 日期/模型元数据 | **需要 MVP 阶段提交** | 当前 PR 不运行线上 Modal cron；若没有，就明确 `NOT_VERIFIED`，不凭静态合同自动启用实盘。 |
| 原行情/订单簿/Volume/凭据 | **禁止公开** | 不要求上传。该工具输出只包含派生的问题分类与指标，仍建议先人工审查脱敏。 |

#### 审核人员快速读取字段

```bash
python - <<'PY'
import json,glob
files=glob.glob('/tmp/csi1000-final-audit-baseline-p00-*.json')
assert len(files)==1, f'choose exactly one output file, found {len(files)}'
r=json.load(open(files[0]))
print('status=',r.get('status'),'exit=',r.get('modal_command_exit_code'))
print('reason=',r.get('reason'))
for item in r.get('inventory_cells', []):
    q=item['inventory']
    print('inventory',item['phase'],q['status'],'days=',q['complete_inventory_calendar_days'],
          'blockers=',q['blocking_count'],'unknown=',q['unknown_held_close_count'])
    for issue in q['issues']:
        print(' ',issue['date'],issue['stock_id'],issue['issue'],issue['blocking'])
for cell in r.get('cells', []):
    print('ledger=',cell['ledger'],'max_diff=',cell['max_abs_diff'],
          'fails=',cell['fail_fields'])
    q=cell['full_calendar_quote_inventory']
    print('inventory=',q['status'],'issues=',q['issues'])
    print('headline=',cell.get('metrics_comparison'))
print('market_execution=',r.get('market_execution'))
PY
```

**复核容差没有修改：** 账户/cash/持仓估值/累积费用/成交额 ≤ ¥0.01，逐日净收益、费率、换手率 ≤ 1e-10，存档 headline 指标 ≤ 1e-6。完整 424 天核对且容差内，最多记 `PASS_RESEARCH_ACCOUNTING_ONLY`；复牌卖出、历史 ST、盘口/流动性仍 `BLOCKED`。如果失败，保留完整盘点 JSON，不抹平缺价。

## 五、审计退出 / 是否可以进入研究型 MVP

1. **无阻塞缺价，账本 PASS：** 结束本轮独立核账审计，**不代表**回测成交可实现，旧 winner / baseline 不自动晋升。
2. **无阻塞缺价，账本 FAIL：** 输出失败字段、日期、差异和冻结身份；禁止认证历史收益。若证明该差异会影响**当前选股**而非仅历史交易模拟，MVP 发布需先阻断/修复相应功能；否则带风险登记继续开发研究型 MVP。
3. **全量扫描后仍有未知缺价：** `BLOCKED_INPUT_QUOTES`，提交**所有异常**，分类保存，不再为每个日期逐一追加 PR；带此边界进入研究型 MVP。
4. **身份/训练时点/发布日期错误：** 与第 3 类不同，可能直接影响当前的选股正确性；必须在 MVP 上线前 fail-closed 修复或暂停受影响输出。当前 PR 的**静态**检查不能替代真实部署/训练标签成熟证据。
5. **镜像/Volume 权限失败：** `BLOCKED_ENVIRONMENT`，记录错误与 SHA；不给研究收益 PASS，不移植演示数据“补过”。

### 后续交接

新对话阅读本文件、[40 — MVP 交接](40-csi1000-mvp-handoff-20261010.md)和本次实际 `*.json`/CI。MVP 第一优先级是当天信号、日期/模型身份、股票风险状态与网站发布的端到端验证；不是为新的历史停牌日继续开 PR。**任何时候不得把收尾动作解释为允许自动实盘交易。**
