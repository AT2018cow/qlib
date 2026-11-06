# 扩展层修复、风险门禁与验收记录（2026-11-06）

基线为 `a31673670c8a32e4d7bea4e65cf8ece812583deb`，对应[原审查 Q01–Q16](2026-11-06-extension-calculation-and-logic-audit.md)。所有实现均在 `quant/`，生产尚未部署。本文保留首次修复阶段的验收记录；最新的四项复审修复、836 项回归与分本金审计见[后续验收](2026-11-06-review-followup-fixes.md)。

## 实施结果

| 编号 | 实施 | 本地证据与边界 |
|---|---|---|
| Q01 | 指标拒绝缺失/非有限交易日；账户隐含收益与 return−cost 不一致时报错；风险指标使用核对后的 NAV 收益 | 100→50→100 缺基准反例不再输出虚假的 0% 回撤；完整数据得到 −50% |
| Q02 | 独立账本检查现金、费用、开闭市 NAV、容差及累加结果为有限数；禁止负库存/透支；关键 JSON 输入输出拒绝 NaN/Inf/溢出 | 逐字段 NaN/±Inf 注入；状态、journal 和发布 metadata 共用严格 JSON 边界 |
| Q03 | 缺前价/缺开盘的普通股票禁止成交；检查输入向量长度；保留有证据的上市豁免 | NaN/Inf/0/负前价负控及新股豁免；补齐改革前创业板不适用改革后五日豁免的边界 |
| Q04 | 先还原人民币参考价 tick，再用 Decimal half-up 计算法定限价；比较时仅吸收 float32 重建误差 | 多种 factor 和半分边界与独立 Decimal oracle 比较；官方除权参考价仍需外部证据 |
| Q05 | 共用物理数量规则：主板/创业板 100 股倍数、STAR 至少 200 股且步长 1、BSE 至少 100 股且步长 1；限制单笔最大数量；不足预算拒单 | paper 和真正 Qlib Exchange 成交计算适配器均测试 199/201 股；保留整仓零股卖出；无需修改 Qlib |
| Q06 | 拼接要求 canonical 市场/profile/horizon/TopK/DropN/初始资金身份、回填末现金/持仓、live 首 opening state 和交易日连续性 | 账户重置、跨市场混接、缺边界日或缺证明均失败；真实三市场文件通过 |
| Q07 | 分段 schema v2 保留 BACKFILL_RESEARCH/LIVE_PAPER、逐日 ST 政策、fallback dates 和执行规则版本；前端校验并披露 | 混合政策经过拼接/浏览器归一化测试；不为历史回填补造 ST 证明 |
| Q08 | 趋势指标在完整历史上计算，因果 shift 后截取评估区间 | 同日信号不随回测起点改变；持续下跌且历史充足时得到 0.3 暴露 |
| Q09 | 熔断恢复使用独立 shadow NAV 的连续 MA20 条件，恢复后重置风险高水位，下一交易日生效 | 空仓后反弹可恢复、持续下跌不恢复、前缀回放与完整回放一致 |
| Q10 | 按扣费后袖资产漂移权重；再平衡费用按比例先扣；输入逐袖核对日期 | 手算 45M×1.1+50M=99.5M；另修复逆波动缺失单元填充可能使权重和大于 1，以及重复滞后一日的问题 |
| Q11 | 冻结名义金额压力模型保留原扣现金方法，return/cost 分母改为压力期初 NAV，所有指标使用同一路径 | [380 份历史账本修正指标](2026-11-06-repair-impact.json)，压力路径核对误差接近零 |
| Q12 | 切池开始立即失效旧请求、清空旧内容；返回检查 generation+pool | 实际 main() 加可控延迟的异步测试，覆盖旧 CSV 在新池 index 等待/空列表期间返回 |
| Q13 | 增加逐交易日 ST 历史 schema 和强覆盖校验；G1/G2 baseline 默认要求证据，显式 reference 模式才可缺省 | 同股普通→ST→恢复样例；缺一天证据报错；旧研究证据不自动提升为 verified |
| Q14 | 生产结算前重新读取旧估值日 close/factor，与持仓坐标锚点比较；修订时停止；保存后续坐标锚点 | 生产 provider adapter 的修订注入测试、不同 factor 下物理订单/NAV 不变性；无自动重标度或现金分红猜测 |
| Q15 | 试验登记保存开始/完成/失败与输入/执行源码指纹；晋级需预注册、成熟度 purge/embargo、至少 120 个新前向日期和执行/成本/多重检验证据 | 拒绝已消费日期、未来日期、候选/合同错配和缺失证据；新 CLI 核验本地证据文件 SHA；最终仍需独立审查外部来源 |
| Q16 | 真实 paper 回放可注入逐日开盘集合竞价金额、参与率上限、部分成交和额外冲击成本；提供多资金规模网格；算术 overlay 始终标为诊断用途 | 缺证据拒绝容量评估；买卖部分成交守恒及异常回滚；滑点作为额外名义成本，避免虚构越过法定限价的成交价 |

**Q01–Q12 的代码缺陷已修复并有回归用例。Q13–Q16 已实现输入合同、阻断条件和验证工具；外部证据及真实前向样本尚不齐全，风险不能标成已消除。**

科创板与 BSE 数量规则分别依据[上交所规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)、[上交所问答](https://edu.sse.com.cn/tib/qa/)和[北交所投资者问答](https://www.bse.cn/uploads/6/file/public/202509/20250916143055_4sxt0kkec4.pdf)。标准路径为竞价限价订单模型，不包含盘后固定价格或市价订单的其他数量上限。

## 结构与运行改进

- `execution/order_quantity.py` 统一研究和 paper 的数量/预算规则；`st_history.py` 统一历史 ST 覆盖合同；`liquidity_contract.py` 定义开盘可用金额与参与率。
- 研究 Exchange 的复权因子保留证券/日期身份，兼容旧策略的两参数取整 API，不通过因子数值猜测板块；复制或保存因子后仍保留身份。现代策略显式传入证券代码。
- `execution/portfolio_overlays.py` 从研究入口抽离可独立测试的风控/组合算法；`production/paper_execution_context.py` 从 Modal 大文件抽离只读 provider/停牌/坐标适配器。
- `production/account_boundary.py` 集中账户身份和现金/库存边界；`artifact_io.py` 集中严格 JSON；`coordinate_contract.py` 集中复权坐标检查。
- 每份新执行报告记录 `cn_equity_execution_v2_20261106`；未携带版本的历史报告明确标记 `LEGACY_UNVERSIONED`。策略模型/profile 未因纠错自动切换。
- 新报告增加 `validation_version=complete_finite_nav_v2_20261106`；保留历史 `metric_version` 的公式语义，同时默认 `promotion_eligible=False`，避免把计算通过等同于产品准入。
- paper 工件增加现金比例、最大单股权重、有效持仓数量、模型/结算年龄、阻塞买卖数。缺行业分类时明确标记，不捏造行业暴露。
- 修复后的新 helper 加入生产和当前研究 Modal 镜像的文件清单。本次没有构建/运行云镜像，部署前需验证真实容器导入和运行结果。

## 历史影响扫描

使用只读工具对当前仓库本地证据执行扫描，完整输入 SHA 与修正后压力指标见[影响记录](2026-11-06-repair-impact.json)。不覆盖冻结原件或原排行榜。

- **380 份**本地 `*report.parquet` 在 100M 初始资金合同下通过新基础指标核对，无缺失日期值/非有限数或 account/return−cost 失配。
- 对这 380 份分别重算额外 10bp 冻结成交金额压力，全部通过压力 NAV 核对；**380 份压力 Sharpe 有变化**，最大绝对差约 **0.076603**。这不代表全部候选排序改变，排序仍需按原筛选门逐项重评。
- **6 份**本地近期执行报告通过加强后的独立账本核对，未发现不合法的新买入物理数量。
- 三个当前账户的回填末状态与真实 live 首状态匹配，离线拼接延伸到 **2026-11-04**。各市场的 11-03、11-04 都保留 `ST_IGNORED_REFERENCE_ONLY`；旧拼接会丢失这项披露。
- 仓库当前 G1/G2 目录主要有 summary；随后在本机保存的临时研究目录找到 12 份原始日频底稿及行情，已用冻结底稿完成 **11 个 G1 overlay 单元**的纠错重算。见[旧/新指标与底稿哈希](2026-11-06-g1-overlay-correction.json)。例如 CSI500 熔断组合的最大回撤由 −18.40% 变为 −37.09%，说明旧“约 18% 硬止损”结果包含无法恢复空仓造成的偏差。这里是算术风控逻辑纠错，不是新增 OOS 或可成交性认证。
- 已使用同一冻结预测、2021-02-19 至 2024-02-05 本地行情完成 **12 个基础执行单元**（三市场×两信号脊柱×两持仓规则）的纠错成交回放，真实命令退出码为 0，12 次 trial 均记录 COMPLETED；再在修正后的基础报告上重算 **11 个 overlay 单元**。[完整指标与试验记录](2026-11-06-g1-execution-correction.json)保留旧/新对比。基础单元 CAGR 最大变动为 14.1561 个百分点，属于 100M 历史参考账户，不能直接套用到当前 1M 账户。
- 已将 **46 份日频报告**复制到[独立证据目录](../../data/repair_validation/20261106/README.md)，包括 12 份原底稿、12 份修正执行报告及两组各 11 份 overlay 报告；全部 SHA 验证通过。原底稿的 CAGR/MDD/Sharpe 与仓库原汇总逐项一致，便于排除输入错配。未覆盖的其他历史候选仍需补做相同回放。
- 本扫描尚未量化 Q04/Q05 对全历史订单的影响：逐笔订单、factor、参考价和日期化状态缺少完整统一证据。380 份 report 的指标核对不替代重新成交模拟。

## 复现命令

在仓库根目录使用已配置的 Python 环境：

```bash
.venv/bin/python -m pytest -q quant/tests
node --test quant/tests/*.cjs
.venv/bin/python quant/code/audit/review_extension_boundaries_20261106.py
.venv/bin/python quant/code/audit/audit_repair_impact.py --output /tmp/quant-repair-impact.json
```

[修复后探针输出](2026-11-06-after-repair-probe-results.json)与[原始探针输出](2026-11-06-boundary-probe-results.json)分开保留。原报告中的探针数值属于修复前事实，不随修复重写。

### 有限容量回放

```bash
.venv/bin/python quant/code/production/paper_replay.py \
  --input /tmp/replay-input.json \
  --capacity-grid /tmp/capacity-grid.json \
  --output /tmp/capacity-results.json
```

网格格式示例：

```json
{"account_sizes":[100000,1000000,10000000],"participation_rates":[0.01,0.05],"slippage_bps":[0,5,10]}
```

每个输入 session 需要独立日期化 `liquidity_evidence`，格式为 `{"date":"YYYY-MM-DD","source":"来源标识","open_auction_notional":{"证券代码":可用人民币金额}}`。原始 replay 格式中的 OHLC/factor/ST/停牌证据仍必需；不能用全天总成交量冒充开盘集合竞价流动性。无证据时命令报错，不给出容量认证。

### 历史 ST 输入

`run_g1_baseline.py` 新增 `--st-history <file>`，JSON 格式：

```json
{"schema_version":1,"source":"日期化历史状态来源","dates":{"2025-01-02":[],"2025-01-03":["SZ000001"]}}
```

必须覆盖全部执行交易日。空名单表示来源确认当日无 ST；漏日表示未知。`--allow-st-reference` 仅生成显式参考研究，不能晋级。输出目录下 `trials/` 保留每次尝试，包括失败结果；相同配置的新尝试使用不同 attempt ID，已有尝试不能被覆写。

### 新研究晋级证据

`research_governance.py` 定义预注册字段、purge/embargo 和 forward 日期门禁；`research_promotion_gate.py` 接受合同及证据文件，核对证据 SHA 后输出独立审查准备状态：

```bash
.venv/bin/python quant/code/ops/research_promotion_gate.py \
  --contract /tmp/research-contract.json \
  --evidence /tmp/research-evidence.json \
  --output /tmp/research-readiness.json
```

合同字段：`schema_version=1`、有时区的 `registered_at`、`forward_start`、`registration_receipt_sha256`、`label_horizon`、`purge_sessions`、`embargo_sessions`、`min_forward_sessions>=120`、`candidate_ids`。注册必须早于前向日期。

证据字段：`candidate_id`、`contract_sha256`、唯一递增且已发生的 `forward_dates`；`frozen_predictions/order_replay/dated_st/liquidity/cost_stress/multiple_testing` 六种 receipt 各含 `status=PASS`、`sha256` 和相对于证据文件的 `path`。缺失/哈希不符则退出码为 2。本地哈希证明内容一致性，不能证明外部来源或注册时间真实；外部收据仍需独立审查，不自动部署或改变 canonical。

## 验收状态与剩余工作

最终本地回归：**788 passed, 1 skipped, 50 subtests passed**（27.96 秒，1 条既有 Modal 本地挂载提示）；前端四个 `.cjs` 文件逐个直接执行，**25 个用例通过**，包含实际 main() 的异步乱序测试，见[前端测试记录](2026-11-06-frontend-test-results.json)。最终实现文件的 SHA 见[源码清单](2026-11-06-repair-source-manifest.json)；历史回放记录中的源码 SHA 对应各次回放实际使用的版本，后续仅增加了旧取整接口的身份兼容验证。修复后探针、输入/源码 SHA、文档链接、`git diff --check` 和仅 `quant/` 的变更范围均已核验。以下项目仍需另行补证：

1. 将已完成的 12 个基础单元及 22 个风控/组合单元的纠错回放扩展到其余历史候选，版本化重评排名与风险门；不能把 380 份 report 的指标重算当作 380 次成交重放。
2. 提供独立历史 ST、官方除权参考价与开盘流动性证据；复权坐标修订时重新生成独立版本账户，不能直接覆盖旧状态。
3. 新标签目标、行业/风格约束、波动目标或组合优化器需要新预注册实验和未消费样本。当前代码未以旧历史收益为依据自动改变现役模型。
4. 云端镜像构建、线上状态迁移、部署后的真实发布验收。本次本地修复不等于线上版本已更新。
