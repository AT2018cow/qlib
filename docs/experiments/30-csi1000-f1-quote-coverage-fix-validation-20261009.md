# 30 — CSI1000 Stage-B F1 修复与冻结信号执行对照验证方案（2026-10-09）

> **本 PR 是独立修复 / 诊断 PR，尚未在原始 Modal 实验 provider 上完成回放。** 旧报告及指标原始数值不改写；不做模型重训、参数调优或 Stage-B 候选选择变更；不部署 `modal_qlib_cn_a10g.py`、不改生产 cron、paper state、网站或历史信号。**已确认旧执行假设 FAIL，修正版性能仍 INCONCLUSIVE，端到端整体仍非 PASS。**

## 1. 原始证据及已确认 F1

- 冻结 result commit：`7a2676397b0f8e6f69c0bffc98d1764647f644ac`；snapshot：`51756897fc75230493194aef2e48815e4e9f3cc7426cc135f47bb8ba8e0d21e1`。
- Winner candidate `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`、phase 0；冻结 `Sharpe=1.276262`、`CAGR=29.3178%`。原报告计算 SHA/账务自洽并不能保证现实可成交。
- 2025-06-30 生效的 CSI1000 退指，使 `SH603301` 与 `SH688066` 在旧 `Exchange(codes='csi1000')` 的 `D.features()` 日度报价表中消失；Qlib 把缺报价格视作不能交易并保留最后可得的市值。
- 2025-07-01 出现 22 个持仓，2025-07-02～2026-06-29 **连续 240 个执行交易日零订单**；2026-06-30 前者重入指数后才获卖出机会；后者到回测末仍在组合内。冻结 `decisions.json` 的 424 decisions、482 orders 和 provider 的 `csi1000.txt` membership spans 已支持该时间线。报告 29 的 `+242,577.44` 元分解属于其脚本陈述，**尚未作为第二套独立计算复核的 F1 影响数字**。
- 报告 29 中 F1 段误将 baseline phase0 `Sharpe=1.122 / CAGR=23.01%` 写为 winner phase0；本修复审计使用 winner 的 `1.276262 / 29.3178%`，不回写历史审计证据。

## 2. 最小修复原则与代码变更

Qlib `Exchange.get_quote_from_qlib()` 将 `codes` 交给 `D.features(codes, start, end)`。原先传入 **dynamic** `D.instruments('csi1000')`，在个股退出指数后切断该股报价，即使它仍然是已经持有、交易所正常上市的证券。

**修复**（只改 Stage-B 研究执行时的 *quote coverage*，不改 signal/交易资格）：

1. `execution_quote_universe.execution_quote_codes(MARKET, start, end)` 使用原 provider 的 `D.list_instruments(D.instruments('csi1000'), start_time=start, end_time=end, as_list=True)`，固定获取本段**曾属于 CSI1000 的股票代码并集**，排序去重。
2. `csi1000_stage_b._run_signal_backtest()` 调用 `research_exchange(..., codes=<static member union>)`。传 **list** 避免 `D.features()` 再次按成分日期缩短某只股票的报价区间；退指后仍能查询其原始每日 open/close/factor 用于持仓估值及正常卖出。
3. **策略仍然只读取冻结历史 `signal.parquet` 的每日评分**，并按 `shift=1` / Top20/Drop2 交易。union 仅是报价订阅范围，**不能**作为择时/买入候选集（提前知道未来入指股票会造成未来数据泄漏）。
4. 交易手续费、最小佣金、复权/整手、价格限制及 Modal/production config **没有在本 PR 改动**。`csi1000_stage_b.py` 原来的 worker 会有代码指纹漂移；**严禁用原 snapshot 覆盖写入新的 Stage-B 结果**。修正后的收益是独立版本研究结果，不替换 canonical Stage-B。
5. 若旧数据只有动态成分内的行情，而底层 `$open/$close/$factor` 在退出指数后本就不可取得，**停止验证（BLOCKED）**，不能静默把 NaN 填成市场价格，也不能反向按当前列表回填历史持仓。

## 3. 零训练、隔离验证入口

附加 `audit/replay_stage_b_f1.py` 是**使用原 Qlib 模拟器进行的对照诊断**，不是第二套独立市场可交易性证明。只读原始 signal/report/decisions/provider 并严格核验每份冻结 SHA、原 provider `provider_snapshot.json` 的 token 和 fingerprint，再执行两次、使用**完全相同的冻结 signal**：

| 轨道 | 交换机报价 | 结果要求 |
|---|---|---|
| **Legacy** | 原 `codes='csi1000'` 动态成分范围 | 先复现冻结 424 天 report 的账户/每日费率和 424 decisions/482 orders；不符立即中止，不可拿新旧比较结论 |
| **F1 fixed** | 原执行区间曾入指代码的静态并集 | 需实际读到 `SH603301`、`SH688066` 退指后的原 provider 开/收盘价和因子；固定信号与日历不变；在旧 240 日停摆段恢复至少一笔订单并改变净值，否则终止 |

**已冻结的基础配置**：100,000,000 元初始账户、`2025-01-02..2026-09-30`、基准 `SH000852`、T-close/T+1-open、Top20/Drop2、研究交易费 5/15 bp、最低 CNY 5。不得自行更改信号、初始现金、费用、选股条件、样本起止。

需要具备：Python/Qlib 与原 provider 可读路径、`pandas`、`pyarrow` 和冻结包版本相容的依赖。使用**授权实验环境**的只读 provider；无需重新下载数据或启动 Modal GPU。命令形态：

```bash
python -m audit.replay_stage_b_f1 \
  --provider-uri /path/to/read-only/original/cn_data \
  --provider-snapshot /path/to/read-only/original/csi1000_stage_b/provider_snapshot.json \
  --output-dir /path/to/new/local/audit-output/f1-winner-phase00
```

**安全规则**：输出目录必须全新；脚本拒绝已有目录以及 `/vol` 下的写入；不调用 `stage_b_phase_worker`、不运行训练、不提交 `vol.commit()`、不重写原始 `report.parquet`、`signal.parquet`、`decisions.json` 或原 provider。示例路径是占位符，不能将私人 Modal profile / token / workspace 名称提交 GitHub。

保存于新目录：`legacy_diagnostic_report.parquet`、`fixed_diagnostic_report.parquet`、`fixed_diagnostic_decisions.json`、`daily_legacy_vs_fixed.csv` 和 `comparison.json`。JSON 要包含：
- 原三份文件 SHA256、provider fingerprint、静态 quote code 个数与集合 SHA；
- legacy 完全重放的 PASS 证据（含当日仓位/订单数/报告口径门禁）；
- 原先 240 日停摆段的旧/新订单数，第一笔差异订单和净值差异日期；
- **两条可复算账户 NAV 的** Sharpe、IR、CAGR、MDD、成本、日收益；逐日账户/现金/估值/换手变化及差额；
- 最新状态只能写 **diagnostic execution replay**，不能把这个新 Qlib 重放结果写成完成真正成交的独立审计。

## 4. PR 合并前验收 Gates

| Gate | 验证动作 | 阈值 / PASS 条件 | 当前 |
|---|---|---|---|
| F0：代码隔离 | diff 只涉及 quote union、小规模研究 runner、审计回放、测试、文档/CI；主流 signal/模型不变 | 零生产/Modal deploy、零调参、旧报告 SHA 不变 | **待 PR 复核** |
| F1：纯逻辑单测 | 不连接 Modal/数据；模拟 `SH688066` 退出后不再入指、`SH603301` 退出又重入 | union 仍包含两者，排除未入指股票；错误配置空集合 fail-closed | **测试代码已提交** |
| F2：原始数据身份 | 使用同一 frozen report/signal/decision SHA 和原 provider snapshot fingerprint | 全部 SHA 和 fingerprint 完全相等；原报告日期与 provider 日历一致 | **待实验环境运行** |
| F3：Legacy 复现 | 完全不变的 frozen signal + 原动态指数 quote 重新仅执行交易回放 | 424 日一致；account/cash/value/cum-fees/cum-turnover 每日最大差 <= **CNY 0.01**；return/cost/bench/turnover 最大差 <= **1e-10**；原 decisions/订单日期、方向、数量、成交量一致 | **待运行** |
| F4：修复有效性 | 使用完全相同 frozen signal + 固定 quote union 只修改报价范围 | 退指后股票 `$open/$close/$factor` 非空；原 240 日区间旧轨 0 单、新轨 **>0 单**，账户 NAV 发生差异；始终 424 日，样本/初始资本/手续费/选股不变；无新漏报日 | **待运行** |
| F5：独立现实执行 | 不用同一 Qlib 回放为自证；从固定轨订单和原价重新构建 424 天日账与可交易性，使用真实涨跌停、历史 ST、停牌、IPO 及必要盘口 | 内部独立 daily return 差 <= **1e-10**，cash/account/fees 差 <= **CNY 0.01**；无实质违规成交、未来数据/训练标签泄漏，否则 FAIL/BLOCKED | **待额外证据** |
| F6：范围扩展 | 同样的只读操作覆盖 winner 4/6/10/15 与 baseline 0/4/6/10/15；baseline phase0 按 preflight `repeat_b` 路径 | 每个 phase 独立 SHA、同口径 delta、异常日期和费用表；报告受影响范围 | **BLOCKED / 不应预先宣告修复完成** |

**应输出的回测对照矩阵（由实际输出填写，不允许猜数字）**：

| 组别 | 指标 | 冻结旧路径 | 静态 quote 修复路径 | 变化 |
|---|---|---:|---:|---:|
| winner phase0 | Sharpe | 1.276262 | **待实测** | 待实测 |
| winner phase0 | CAGR | 29.3178% | **待实测** | 待实测 |
| winner phase0 | IR / MDD / total cost / turnover | 见 doc27 原始复算 | **待实测** | 待实测 |
| winner phase0 | 2025-07-02..2026-06-29 订单数 | 0 | **待实测（预期 >0）** | 待实测 |

请保存 `calendar_compare.csv`、`execution_orders.csv`、`account_rebuild_daily.csv` 的**固定轨二次独立实现**证据，和上述诊断性 Qlib 轨道区分开来。原始已冻结 tail 被消费，只做缺陷归因，不进行改进收益的事后择参。

## 5. 当前审计结论和决策条件

- **确认：** F1 旧口径产生虚假不可交易的持仓与长期停换仓，这是结构性模拟缺陷，不是指标计算本身错误；原 winner phase0 的 Sharpe 1.276262/CAGR 29.3178% **不可直接解释为可交易真实路径**。
- **未证实：** 修复后收益率或 Sharpe 会提高/降低；原 provider 的行情是否真的支持退指后逐日定价；历史 ST、真实一字板对手盘、成交量容量及 20-session 标签 mature/PIT 证据。
- **保持：** `legacy_metric_arithmetic=PASS`, `legacy_market_execution=FAIL`, `fixed_execution_replay=BLOCKED`, `overall=BLOCKED`, `future_returns=INCONCLUSIVE`。**修复 PR 可以在完成代码审阅后合并为一个研究实验能力，但不得据此自动认可或更换生产模型；生产提升必须走独立版本的验证与授权。**
