# 上线后审查逐项复核与处理

状态：`LOCAL_ACCEPTED / NOT_COMMITTED / NOT_DEPLOYED`。对应[审查原文](2026-11-06-post-rollout-review-findings.md)。已完成确认代码缺陷的本地修补、行为回归和现有工件对账；不将 F19 外部证据、F20 正式发布管理记作已完成。原审查文档保留原样。

## 逐项结论

| ID | 独立复核结论 | 本轮处理与证据 |
|---|---|---|
| F01 | 原审查的 `audit_repair_impact` 必读旧 prefix 判断不准确；账户边界与 canonical 审计入口问题成立 | 用户已批准网站从 10/08 开盘、每市场 1M 现金起算。每市场的 website/forward/backfill 三份派生文件是**同一网站账户的命名别名，不是 canonical live-paper 合同**；该合同变更已在 `quant/data/README.md` 明确。增加独立 Decimal 网站账本审计入口；canonical 审计收到网站账户时明确报 `ACCOUNT_SCOPE_MISMATCH`。`audit_paper_nav` 现默认从第一条非 `BACKFILL_RESEARCH` 工件起算，也可用 `--start YYYY-MM-DD`，因此 canonical live 段可正常对账；`audit_repair_impact` 既有 schema-3 专用分支。三个网站账户各 20 份原始账本均对账一致。 |
| F02 | 成立，已修复 | 三市场先验证市场身份、数据日、provider 日历末日与 archive/calendar SHA，再共同允许显式 lag=1。删除第三道门禁的字符串匹配放行。15 个破坏单一市场身份的 override 反例均拒绝。 |
| F03 | 成立，已修复 | 排名参考必须已有尚无执行行情的旧 pending，且在核实日历上满足 T+1。无 pending、可结算、未来订单、当日新订单或不满足 T+1 均拒绝。合法旧订单的持仓、现金和 pending 原样保留，不查询新 ST、不新建/执行订单、不写账户状态。 |
| F04 | 缺少模式披露成立；自动覆盖不可变排名不应实施 | 新 journal 增加 publication context；恢复旧记录时从哈希校验后的 metadata 推导模式、数据日和交易日滞后数，拒绝 context 不一致。已发布滞后排名明确不可自动覆盖，同日修正必须另有显式版本化 amendment；当前未提供静默替换入口。网站收益的行情补算继续独立执行，不等于允许改写历史排名。 |
| F05 | 成立，已修复 | 新 metadata 使用 `PASS_OPERATOR_OVERRIDE`、`publication_mode`、`lag_sessions`。前端也识别旧 `PASS` 中的 override gate，显示“滞后排名 · 订单冻结”、实际数据日、原执行日及“本日未生成新订单”。实际 11/06 三市场工件均经过前端行为测试；该 UI 改动尚未部署。 |
| F06 | 成立，已修复配置 | 保留快速组，新增自动发现的完整 `pytest -q quant/tests` CI job，安装 Qlib/Modal 所需依赖及编译扩展。前端自动逐文件执行全部五个 `.cjs` 文件。已本地验证同一测试入口和 workflow YAML；未声称远端 Actions 已执行本轮未提交的修改。 |
| F07 | 基准语义披露缺陷成立，已修复 | forward 窗口明确记录 `nav_basis` 的实际重基准金额，并单列按原始 1M 本金计算的 `account_cumulative_return`。新生成的重基准 forward 缺少 prefix 时拒绝发布为完整账户收益。网站当前使用的独立 10/08 账户不受此窗口基准影响。 |
| F08 | 不成立，已补反例证据 | gap 列表实际只遍历开盘已有持仓及收盘实际持仓；`active` 候选用于可交易性掩码。独立 toy replay 证明未持有候选缺价会阻断买入并保留全部现金，`gaps=[]`；已有持仓缺价会产生 gap，并被网站拒绝。保留持仓估值的严格检查。 |
| F09 | 不属于当前页面的缺陷 | 当前市场页面只接受用户批准的 schema-3、10/08 独立账户，避免重新展示别的账户的旧收益。schema-2 解析仍供显式历史读取（market 为空）使用，已有历史解析测试；增加代码说明和当前页面拒绝旧账户的回归，不新增自动回退。 |
| F10 | 成立，已修复 | model/data SHA 必填；fit 日期必须有效、有序、无重叠且满足训练因果顺序。每个执行点必须由前一交易日及唯一 fit 覆盖。新 `fit_receipt_version=2` 将 signal date、fit asof、model SHA 写入每个 point；前端同样验证。旧 schema-3 receipt v1 也执行 SHA/覆盖验证，不免检。现有六个 fit receipt 均通过，新 v2 本地派生的所有财务字段与原序列逐项完全一致。 |
| F11 | 成立，已修复 | null/空日期单独处理，非空日期严格解析 ISO 日期，再比较是否更新。用真实发布函数测试空序列能更新、未来数据不能回退、坏日期拒绝及丢响应后幂等恢复。 |
| F12 | 成立，已修复 | 先过滤起算前 usage date，再区分同市场 legacy 与真实跨市场 lineage。同市场旧记录跳过并记录 `skipped_lineages`；当前窗口内的真实跨市场错配继续拒绝。 |
| F13 | 成立，已修复 | 审计接受合法冻结 pending 并单独披露；有外部日历时额外验证原订单 T+1，不把旧执行日改写成今日。实际 11/06 工件得到 `PASS_STATIC_ONLY / FROZEN_AWAITING_EXECUTION_DATA`；伪造跨两交易日订单会失败。 |
| F14 | 成立，已修复 | stale skip、override 及未验证 legacy 恢复均不能让整条流水线显示完成；仍尝试网站阶段，最终抛出保留各阶段结果且可序列化的 `DailyPipelineError`。缺失、未知或 FAILED 返回值同样不能被标为成功。交易日休市跳过有独立正常状态。 |
| F15 | 成立，已修复预算 | 信号子任务各 90min；网站覆盖 smoke 加三市场最坏全部串行的 4×1h，再留 1h，阶段上限 5h；主入口覆盖三信号加网站及余量，共 10.5h；补跑等待主跑加本次执行及余量，共 22h。移除长任务自动重试的预算倍增，保留 08:00 恢复入口。性质测试核对预算与装饰器；外部无限排队或数据缺失仍不能由超时配置保证成功。 |
| F16 | 成立，已修复 | methodology 及 journal 调度说明更新为 07:00 主跑 / 08:00 补跑，区分原生产冻结 ST 快照与网站未核实历史 ST，说明显式滞后参考模式及原生产账本/网站账户的关系。 |
| F17 | 成立，已修复 | 全仓 Python Black 25.1.0、120 列检查通过（534 files unchanged；`.ipynb` 因未安装 Jupyter 依赖跳过）。`quant/code`、`quant/tests` 及 `quant/website/build_index.py` 均已格式化；未改写上游 Qlib 语义。 |
| F18 | 证据范围缺口成立，已按 legacy 方案处理 | 增加[机器可读执行证据状态](../../data/execution_evidence_status.json)、数据字典与策略目录说明，并在 Strategy Atlas 顶部标记旧榜单 `LEGACY_EXECUTION_CONTRACT_NOT_REPLAYED`。已复验单元只按各自冻结 receipt 的范围认定；519 份报告的指标算术审计不等于 519 次成交回放，不宣称全部历史候选已重新排名或现任策略全局最优。 |
| F19 | 外部证据缺口成立，仍未认证 | 新网站输出明确 `official_reference_price_verified=false`，方法页补充官方除权日参考价未核实；逐日历史 ST、集合竞价流动性与券商成交仍未取得独立证据。现有九份网站 JSON 仍是 v1（`fit_receipt_version` 缺省，无 `official_reference_price_verified` 字段）；前端同时兼容 v1/v2，下一次网站发布的新输出才带 v2 字段。账本对账结果因此保留 `PARTIAL_NOT_CERTIFIED`，不伪造 PASS 认证。 |
| F20 | 发布管理待办；数字比较本身不能判断源码错配 | Modal v30 与 Git `quant-v1.2.0` 是不同版本序列；应核验提交标记及完整源码清单。不过 Git release 确实落后。已准备[下一版本发布候选说明](2026-11-06-post-rollout-release-candidate.md)；本轮未提交/部署/打 tag，不把发布候选当作正式 release。 |

## 验证结果

- Python：**922 passed，1 skipped，50 subtests passed**。相较原验收的 865 项，增加 57 项行为/边界用例；全量真实退出码为 0。唯一跳过的是需要额外提供历史 hash-pinned 行情压缩包的 opt-in 测试。
- 前端：五个测试文件直接运行，共 **30 passed**；`node --check quant/website/app.js` 通过。
- 历史审计：519 份报告，指标与本金合同错误均为 0；426 份成本压力报告无审计错误。范围是算术及声明本金，不能解释为完整订单重放。
- 网站独立审计：三市场各 20 个交易日，60 份账本；现金、成交、库存、费用和公开收益一致。三个账户费用分别为 4089.78 / 3741.62 / 3881.53 元。状态均为 `ledger_status=PASS`、`status=PARTIAL_NOT_CERTIFIED`。
- canonical live 审计：`audit_paper_nav --signals quant/data/signals --market csi1000` 自动从 11/03 起算，2 条已结算报告、0 issue，状态 `PARTIAL_NOT_CERTIFIED`。
- 新 receipt v2 的本地派生验证未改变现有任何日期的财务字段、开闭仓边界或累计收益；保留现有九份网站派生文件不变。
- `git diff --check`、workflow YAML 结构和审查范围 Black 检查通过；纯格式变更另有 AST 等价记录。

[机器可读验收摘要与日志](../../data/post_rollout_review/20261106/validation.json)、[原始工件对账及输入 SHA](../../data/post_rollout_review/20261106/artifact-reconciliation.json)、[历史审计摘要](../../data/post_rollout_review/20261106/historical-audit-summary.json)、[本轮源码清单](../../data/post_rollout_review/20261106/source-manifest.json)。

本轮验证源码清单为 `1aa5400c7a670738b504ce09fa5437fa4ff343110eab4b3a008052fb67b8f00a`；被审计的既有训练/网站工件仍明确保留其原始 `0f322f8c9be595a4da2dd9a96a4ea2d3a0a677d98d6b3e6188acf275e04aa7a1`。本轮没有启动新模型训练、Modal 实验、云端重放或策略切换，不能拿旧 smoke 给新源码授权缓存复用。

## 正确的独立网站审计入口

例如核对 CSI1000 当前网站账户：

```bash
.venv/bin/python quant/code/ops/audit_paper_nav.py \
  --market csi1000 \
  --forward quant/data/signals/csi1000_website_performance.json \
  --website-ledger quant/data/daily_schedule/20261106/accounts/csi1000/ledger/sessions.jsonl
```

预期 `ledger_status=PASS`、`status=PARTIAL_NOT_CERTIFIED`。需要更完整的认证时另外提供独立报价和交易日历，但它们也不能替代逐日 ST、官方除权参考价及真实成交证据。该入口只读，不改变信号、账本或网站数据。

canonical live 段使用同一审计器，默认会自动跳过 `BACKFILL_RESEARCH` 并从第一条 live 工件起算：

```bash
.venv/bin/python quant/code/ops/audit_paper_nav.py \
  --signals quant/data/signals \
  --market csi1000
# start_date=2026-11-03, audited_reports=2, status=PARTIAL_NOT_CERTIFIED
```

也可以用 `--start YYYY-MM-DD` 显式指定 live 段起点。把网站账户序列交给该命令仍应明确失败为 `ACCOUNT_SCOPE_MISMATCH`，不能通过放松对账把两个账户说成同一个。

## 交付与仍需完成的边界

此次交付是本地修复及验收，没有提交、推送、生产部署或正式发版。正式上线需对本轮固定源码重新完成前台 cloud smoke/full、两应用相同源码清单校验、生产状态前后对账，以及主跑/补跑的真实调用验收；不得将本地测试解释为已验证未来自动 cron 成功。

F19 保持外部证据待办，F20 按[发布版本策略](2026-10-19-quant-release-versioning-policy.md)在合并、部署与复审事实齐备后发版。后续不因这轮审查自行启动全部历史候选的昂贵重选，也不改动已经批准的生产 profile。
