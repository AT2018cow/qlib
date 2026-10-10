# 39 — CSI1000 Stage-B 审计收尾：一次性 Modal 验证与证据提交（2026-10-10）

> **范围冻结：1 个小型收尾 PR、1 次 baseline phase 0 真 provider 实验。** 这是停止扩张历史审计的决策关口，而非声称所有历史订单均可实际成交。无新增模型/参数、无重训、无生产或 paper 变更、无 55 组重放。前次 PR #52 的 Modal 环境修复及冻结 SHA 继续沿用。

## A. 已核实的事实和本 PR 的精确边界

1. 原始 corrected Stage-B 记录：2025-01-02～2026-09-30，424 个交易日；冻结 baseline ID `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`，对照 ID `c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b`。55 组 corrected replay 已生成；**其排序是在已消耗测试区间上的诊断排名**，不提供新样本外证明。
2. [深交所存档的晨鸣纸业公告（公告编号 2025-006）](https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-19/b0f90573-61df-4bcc-953f-edf8738c84e1.PDF) 明确：`SZ000488` **2025-02-20 停牌一天**，**2025-02-21 复牌并开始 ST 风险警示**，A 股当日起采用 **5%** 涨跌幅限制。
3. PR #52 的独立账本先前在持仓估值缺失 `SZ000488/2025-02-20` 处拒绝继续。Qlib `Account.update_current_position` 对停牌持仓跳过当日价格更新，保留上一次持仓价。本 PR 只为**上述代码+日期**添加外部公告明确支持的估值例外，**禁止当日成交**；账户仍用独立现金/股票数量/手续费计算。
4. 对 `SZ000488/2025-02-21` 冻结的卖出订单只输出 `HISTORIC_ST_5PCT_LIMIT_SELL_NOT_EXTERNALLY_VALIDATED` 警告；**不判定必然不能卖、不修补订单、不重算另一套虚构成交收益**。历史 ST/真实订单簿、涨跌停成交量仍记为 `BLOCKED`。
5. 所有其它未知的缺失持仓收盘价继续 fail-closed；不做一般性的 `ffill()`，不从今天的 ST 名单倒推过去；不改变 `report.parquet` / `decisions.json` / signals 的 SHA 或 Qlib 交易规则。

## B. PR 代码验收（GitHub Actions，无私有 provider）

```bash
python -m unittest discover -s tests -p 'test_verify_stage_b_fixed_ledger.py' -v
python -m py_compile audit/verify_stage_b_fixed_ledger.py audit/modal_stage_b_ledger.py
```

预期合成测试至少涵盖：正常账本、篡改、factor 不符、未知缺价拒绝、已核实停牌估值、停牌日订单拒绝、与停牌公告矛盾的有效 close 拒绝、2 月 21 日 ST 卖出明确标注 BLOCKED、负现金/数量错误、输入哈希及失败结果保留。**CI 通过不等于真实 provider 审计通过。**

## C. 唯一真 provider 实验：baseline phase 0

由拥有 `qlib-cn-data` Modal Volume 权限的实验人员在**仓库根目录**执行。仅在本 PR 合并完成后更新 `main`，或清楚标记所用 PR HEAD 执行；实验的 Git SHA 必须附在结果里。

```bash
set -o pipefail
git fetch origin
git checkout main
git pull --ff-only
git rev-parse HEAD | tee /tmp/csi1000-stageb-closeout-head.txt
test -f audit/modal_stage_b_ledger.py
test -f audit/verify_stage_b_fixed_ledger.py
test -f audit/evidence/stage_b_corrected_rerank/batch_result.json
modal --version
# 手动确认有授权访问现存 Volume qlib-cn-data。
# 使用全新本地输出文件；重复运行请使用新名字，不能覆盖上一次。
modal run audit/modal_stage_b_ledger.py \
  --candidate baseline --phases phase0 \
  --output /tmp/csi1000-stageb-closeout-baseline-p00.json \
  2>&1 | tee /tmp/csi1000-stageb-closeout-modal.log
```

**注意：** 若 `modal run` 退出码非 0，首先检查本地 JSON；审计器在预检中断时产生 `audit_failure.json` 的等价本地返回，或在账本对不齐时保存 `independent_ledger.json`。只有镜像/账户授权级别的失败才可能尚无 JSON；这种情况提供脱敏 CLI 日志并记录 `BLOCKED_ENVIRONMENT`。不为了通过而修改 provider、冻结报告、阈值或快照指纹。

**资源：** PR #52 已有 Modal 入口，沿用其镜像构建与原始 volume 挂载（`/vol/cn_data`、`/vol/csi1000_stage_b/provider_snapshot.json`）；**仅一台 4 CPU / 16 GiB / 0 GPU 的一次性 worker**，最长 60 分钟，不重试、不部署、不 `vol.commit()`。同一候选只读 1 次需要的字段，不启动 10 worker / 不 GPU fit。若原始私有数据或 Volume 不可用，结果 `BLOCKED`，不替换成 Qlib 官方演示数据。输出写容器 `/tmp` 并返回发起端的本地 `/tmp/*.json`，不向 /vol 写入文件。

### 结果校验

```bash
python - <<'PY'
import json
p='/tmp/csi1000-stageb-closeout-baseline-p00.json'
r=json.load(open(p))
print('command_exit=',r.get('modal_command_exit_code'))
print('status=',r.get('status'))
print('accounting_pass=',r.get('all_research_accounting_pass'))
print('cell_count=',len(r.get('cells',[])))
for cell in r.get('cells',[]):
    print('phase=',cell.get('phase'), 'ledger=',cell.get('ledger'))
    print('fail_fields=',cell.get('fail_fields'), 'first_diff_date=',cell.get('first_diff_date'))
    print('max_abs_diff=',cell.get('max_abs_diff'))
    print('metrics_comparison=',cell.get('metrics_comparison'))
    print('suspension_marks=',cell.get('verified_suspension_marks'))
    print('st_execution_review=',cell.get('known_st_execution_review'))
print('market_execution=',r.get('market_execution'))
print('error=',r.get('reason') or r.get('diagnostic_output_tail','')[-900:])
PY
sha256sum /tmp/csi1000-stageb-closeout-baseline-p00.json
```

**验收门槛保持 PR #52 原值且不调小：** 逐日 cash/value/account/total fees/turnover 最大误差 ≤ ¥0.01；cost_rate、turnover_rate、net_return ≤ 1e-10；headline 指标差 ≤ 1e-6。真实 provider 的日期、文件 SHA、snapshot token 和 provider fingerprint 必须完全匹配。如果 `ledger` = `PASS_RESEARCH_ACCOUNTING_ONLY`，也仍然 **不代表 2025-02-21 ST 卖单可以真实成交**；`market_execution` 必须仍为 `BLOCKED`。

## D. 必须交给下一次审核的实验结果

仅提交**脱敏、派生**材料。建议优先**在聊天中上传文件**，不要把敏感原始行情、Volume 数据或 Modal 访问凭据推送到公开仓库。

| 文件或记录 | 必要性 | 内容 |
|---|---|---|
| `csi1000-stageb-closeout-baseline-p00.json` | **必需**，如果已生成 | 原始 JSON 原字节；包括 `modal_command_exit_code`、每格 `ledger`、所有差异、`verified_suspension_marks`、`known_st_execution_review` 与 BLOCKED |
| `csi1000-stageb-closeout-modal.log` | **必需** | 实际运行日志；先对用户名、token、路径、凭据等脱敏，保留 traceback 和错误日期 |
| `csi1000-stageb-closeout-head.txt` | **必需** | 运行的 Git 全 40 位提交 SHA；另请附对应 GitHub Actions CI URL |
| JSON 的 SHA256 | **必需** | `sha256sum` 原样输出，便于确认提交后未改写 |
| 输入身份 | **必需** | 只列冻结 batch SHA、snapshot token / fingerprint 是否通过以及 provider 所用的实验代号，**不上传原行情** |
| 2025-02-20/21 定点摘要 | **必需** | 是否出现一次受证实停牌的上一价格估值、是否记录 2/21 ST SELL 警告；不提交交易所盘口或原价格序列 |
| `audit_failure.json` / CLI 错误 | 有失败时必需 | 没有正常 JSON 时提交错误类型、首次失败日期与分类 `BLOCKED` |

完整 JSON 由 Modal 启动器带回本地；如未经人工检查，不要把全量日志直接上传公共 GitHub。

## E. 收尾判定：**一次实验，无论 PASS 还是 BLOCKED，都形成决定**

- **PASS_ACCOUNTING_ONLY**：价格到账本核对通过，公告日期估值可解释，ST 卖单可执行性仍 BLOCKED。**结束本轮历史收益审计**，保留研究候选，进入新对话 MVP；不因为这个结果自动晋升 baseline。
- **FAIL_DIFFERENCE**：发现明确日期/字段差异，留存 JSON、记录影响，**不得认证原收益**；若影响当前实时信号数据时点、排名生成或生产风险控制，MVP 中应先隔离相关功能；否则作为风险条目交接，不扩成另一个历史审计 PR。
- **BLOCKED_MISSING_DATA_OR_EXTERNAL_EXECUTION**：真实的其他停牌日、历史 ST/涨跌停/流动性证据不足，明确未完成范围并交接；**不无限追加市场数据采购与全历史重建**。
- **BLOCKED_ENVIRONMENT**：授权 Volume/镜像构建/路径问题，记录、交接，不默默使用替代 provider。
- **禁止自动进入 `--candidate both --phases all`**：用户先审查本轮结果再决定；不自动扩大资源消耗。

本 PR 的代码合并不等于真实 Modal 结果已生成。只有实验人员明确运行并交回这些证据后才能撰写“已完成 baseline 复核”报告。结束审计工作与认证历史收益必须分别记录。

## F. MVP 转场

下一对话统一从 [40 — CSI1000 MVP 启动交接](40-csi1000-mvp-handoff-20261010.md) 开始。不要自动改生产 `CANONICAL_PROFILE`：它目前是历史旧 winner `4e908173`，而纠错后 Stage-B 诊断排名 baseline 第一、旧 winner 第十；这**不是**自动换模型或证明 baseline 的依据。MVP 优先验证每日信号/数据新鲜度/发布链路和模型身份，冻结候选前向观察，只有新证据才讨论下一轮调参。
