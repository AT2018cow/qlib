# 60 — CSI1000 两轮策略择优、收益审计纠错与分层研究终局报告（2026-10-15）

> **性质：最终工作总结 / 历史实验与方法学报告 / 只读研究归档。** 贯穿 2026-10-06～2026-10-15 的首轮 Stage-A/Stage-B 选优、2026-10-09～11 的交易收益复盘及其后 P0、L1、L2、L3、L4 第二轮分层搜索。这里归纳既有合并代码、原始审计、操作者正式执行评论和审查裁决；**不是新的策略研究、统计检验、重新训练或生产切换批准**。报告写入 GitHub 不重新执行 Modal，不重写原始数据、排名或历史结果。

**最终研究状态：`TWO_ROUNDS_DOCUMENTED / OLD_STAGE_B_EXECUTION_ASSUMPTION_INVALID / SECOND_ROUND_L4_0_OF_3_QUALIFIED / RESEARCH_CLOSED_NEGATIVE / KEEP_EXISTING_PRODUCTION_WINNER`。**

## 执行摘要：做了什么，最终说明什么

1. **第一轮：固定 Alpha158、未来 20 日原始收益标签和 Top20/Drop2，寻找更好的 LightGBM 超参数。** Stage-A 在四个短折上测试 80 套模型（320 个候选×折单元）；Stage-B 把前十名和原始 Baseline 共 11 套，放进 2025-01-02～2026-09-30 的 424 个执行交易日，并扰动五个固定重新训练日历 phase（55 套完整模型×phase 结果）。原始冻结排序把模型 `4e908173…` 从 Stage-A 第 5 名选为 Stage-B 第 1 名；2026-10-08 将其发布为 CSI1000 canonical paper profile（[原选型合同](19-csi1000-stage-b-final-audit-forward-contract-20261008.md)、[上线决策](20-csi1000-stage-b-winner-canonical-promotion-20261008.md)）。
2. **复盘发现第一轮“收益计算正确”并不意味着“交易模拟路径正确”。** Qlib Exchange 对动态 CSI1000 成分取报价，持仓股票退指后失去继续报价/卖出路径，形成 22 只持仓、约 240 个连续交易日零订单的异常状态，冻结的旧净值、Sharpe、CAGR 和候选排序因而不适合作为可信的可交易策略选择依据。**主要错误是执行行情覆盖与成交/估值路径，而非单纯 CAGR/Sharpe 四则运算公式。** 把冻结预测保持不变、只修正报价覆盖，旧 Winner Phase0 Sharpe `1.276262→0.998878`，CAGR `29.3178%→23.4107%`，MDD `−12.13%→−27.64%`；完成 55/55 的历史纠错回放后，原 Stage-B Winner 从第 1 名降到第 10 名，原 Baseline 从第 5 名升到第 1 名。**这是已消费历史上的诊断性重排，不是新 OOS 排名、更不是生产自动切换许可。**（[F1 故障与修复](30-csi1000-f1-quote-coverage-fix-validation-20261009.md)、[55 格修复结果](35-stage-b-corrected-rerank-results-20261010.md)）
3. **第二轮：先锁定同一执行和账务口径，再有边界地独立探索组合、参数和结构。** P0 重放 4/4 PASS；L1 先后完成 32/32、144/144、264/264 个组合评估单元，初筛 7 套，按原合同截断为 3 套挑战者；L2 做 192 次真实 fit，但 0 套晋级；L3 做 109 次真实 fit，两个频率结构都不合格，因而按门禁跳过 raw10/raw40 标签结构；L4 冻结 **原始 Baseline 正式对照 + L1 Top3** 共 4 套配置，复用 3 个模型×5 相位的 15 份原始真实信号，完成 **20/20 个独立 F1 净值回放、0 次新增训练**。三套挑战者虽然均有 **4/5 个 phase** 的净总收益优于对照，却在最差 phase 分别落后 **6.24、2.83、3.63 个百分点**，且全相位 +10bp 成本压力条件不满足；全部 `NOT_ELIGIBLE`。终局决策为 `KEEP_BASELINE_FOR_PRODUCT_SELECTION / n_pass=0 / promotion_authorized=false`（[L4 终局报告](https://github.com/AT2018cow/qlib/pull/81#issuecomment-6173350754)、[审查结项](https://github.com/AT2018cow/qlib/pull/81#issuecomment-6174408306)）。
4. **生产结论：不切换。** 研究里的“原始 Baseline”是比较锚点，不等于当前生产运行的 `stage_b_winner`。第二轮 `KEEP_BASELINE_FOR_PRODUCT_SELECTION` 的精确含义是“没有新的挑战者达到事先登记的产品晋级门”，**不是把已经上线的 Stage-B Winner 回滚成原始 Baseline**。本轮未获任何新的生产 Winner 切换授权；生产配置、paper lineage、网站正常发布口径保持原样。原 Stage-B 实验执行假设的缺陷应在研究审计中永久保留为风险事实，也不能反过来称当前模型已被前向盈利验证。

---

## 一、项目背景、研究对象与统一口径

### 1.1 原始 CSI1000 策略结构

| 维度 | 两轮研究共同基础／说明 |
|---|---|
| 交易标的和基准 | 中证 1000 成分策略 `CSI1000`；保存的基准 `SH000852` |
| 特征和模型 | `Alpha158` 价量特征、`LightGBM` 预测得分；确定性评分降序、代码升序处理同分 |
| 预测目标 | `raw_20d`：约未来 20 个交易日的原始收益标签；验证窗口 252 个交易日 |
| 模型训练 | 从 2016-01-01 开始扩展训练；原始基准频率每 20 个交易日重新 fit |
| 信号、模拟执行 | T 日收盘可用信息形成信号；T+1 日开盘价模拟交易，不使用未来收盘价下单 |
| 组合规则 | 最初 `Top20/Drop2`：目标约持有 20 只，每交易日按评分淘汰至多 2 只并补入；第二轮显式研究不同 TopK/DropN |
| 账户、费用 | 每格独立期初人民币 1 亿元；买入 5bp、卖出 15bp、每单最低 ¥5；独立现金、持仓、费用与 NAV |
| 执行限制 | 当时的 `BoardAwareExchange`/涨跌停、停牌、成交价与因子口径；历史真实 ST/盘口容量及外部成交认证另列限制 |
| 长期历史回放窗口 | `2025-01-02..2026-09-30`，424 个执行交易日；信号起点 2024-12-31 |
| 固定重新训练相位 | 原 freq20 五相位 `0、4、6、10、15`；phase 是重训日历偏移，**不是五份相互独立的市场样本** |

**术语不要混淆：**

- `candidate_id` 是完整模型身份哈希，可能使用相同模型同时测试多个持仓组合；`config_sha256` 是模型+TopK/DropN 等完整策略配置身份。
- `fit` 是真实 LightGBM 训练；`replay cell` 是在已有冻结预测下重新计算真实 Qlib 订单、费用和 NAV，不等于新训练。
- `CAGR`、`Sharpe`、`IR`、`MDD` 的**数值运算通过**，与 PIT/报价/成交/可投资性**真实世界认证通过**是两个不同判定。相对超额 CAGR 使用同一份已保存 benchmark，必须称为 **saved-benchmark-relative**，不能偷换成第三方独立认证的收益。
- 2025–2026 的历史尾段一旦用于第一轮排序/纠错，就不再是第二轮真正未观察过的 OOS；所有第二轮产品探索必须标注 `HISTORICALLY_CONSUMED`。

研究方法的冻结来源：[第一轮 Stage-A 合同](16-csi1000-stage-a-tuning-contract-20261007.md)、[Stage-B 交接与审计](18-next-conversation-handoff-stage-b-20261007.md)、[第二轮合同 53](53-csi1000-budgeted-layered-strategy-search-contract-20261012.md)。

## 二、第一轮选优：Stage-A 超参数筛选

### 2.1 为什么先做 Stage-A，究竟搜索了什么

第一轮的问题是：**现有 Alpha158/LightGBM 策略的预测模型参数是否有更好的选择？** 因此只调整以下七个 LightGBM 参数：

`learning_rate`、`colsample_bytree`、`lambda_l1`、`lambda_l2`、`max_depth`、`num_leaves`、`min_data_in_leaf`。

**没有**在这一轮同时搜索新的特征、标签天数、模型重训频率、TopK、DropN、费用或 execution 日历；这样可以把参数归因与交易结构分离。先做独立双 fit Baseline 可复现门、12 套 smoke，再扩展为 **80 套候选×4 个约 20 交易日的短折=320 个候选×折单元**，涵盖 2021–2024 的不同时段。每折独立训练、独立验证/标签成熟 purge、独立回测账户；四折 NAV **不得直接拼接成一条“全年收益率”**。

预先固定的 Stage-A 排序是字典序，以 **最差折相对超额 CAGR** 为先，其次 Q25、中位数、正收益折比例、中位 IR、Sharpe、最差 MDD、较低换手与费用；同分按内容 SHA 决定，禁止看完结果改权重。关键工程措施：固定交易日和标签成熟边界、确定性买卖及同分次序、留存`signal / report / decisions / checkpoint`、Baseline 两次独立 fit 的逐项可复现证明。

### 2.2 Stage-A 原始结果和第一层谨慎信号

- **320/320 个短折结果已完成**。原始 Baseline 排名 **48/80**，四折最差相对 CAGR `−0.236337`、Q25 `0.204956`、中位 `1.087285`、3/4 折相对超额为正。11 个候选在全部四折上取得正相对超额 CAGR；领先者呈一个参数族群，不是孤立随机得分者。
- 当时原始 Stage-A 第一名模型 `59b93929…`，后来成为 Stage-B Winner 的 `4e908173…` 在 Stage-A 是**第 5 名**；`c98856b4…` 在 Stage-A 是**第 8 名**，不要把后来 Stage-B 或第二轮的名次倒填到 Stage-A。
- **重要先天脆弱性：80 个候选中有 78 个以 fold1 为最差折。** 后来的完整四折删一折（LOFO）诊断显示，剔除 fold1 后排名 Spearman 约 **0.393**，原 Top10 仅 **2/10** 仍属 Top10；原 Rank1 变为 #20、原 Stage-B Winner #5 变 #49、Baseline #48 变 #65。删除其余三个折时排名几乎不变。这说明最差折优先规则显著受一个 2021 折影响，不能把微弱的头部名次差距称为稳定总体排序。
- Stage-A 是模型方向性**代理筛选**，而非 424 日滚动交易的实质结论；因此需要预先隔离的 Stage-B 长期确认，不能仅凭短折就上线。

证据：[Stage-A/Stage-B 交接第 4–7 节](18-next-conversation-handoff-stage-b-20261007.md)、[Stage-A 320 格独立纠错审核](https://github.com/AT2018cow/qlib/pull/64#issuecomment-6109545893)。

## 三、第一轮长期确认：Stage-B 55 格与首次生产 Winner

### 3.1 Stage-B 是另一类实验，而非 Stage-A 延长几天

在观看长期尾段收益之前，已锁定 **Stage-A 头十套 + 原 Baseline 对照** 共 **11 套模型**，每套五个固定 freq20 phase：`0、4、6、10、15`，每个 phase 约 22 次 rolling fit，理论标准网格为 **11×5×22=1,210 个模型训练切片**（不含额外的 Baseline 双 fit 工程预检；不要将预检与网格求和冒充审计后的唯一 fit 计数）。在同一 2025–2026 的 424 日执行窗口，以五相位 **最差 → Q25 → 中位 → 正值比例 → 风险/换手** 字典序评估。五相位是模型重训时钟的鲁棒性扰动，不是五段独立 OOS。

工程上做过 Baseline phase0 的双独立 rolling 复现：**22×2=44 次 fit**，模型 best_iteration、预测 hash、订单 hash 一致；报告浮点列在冻结容差内一致，确认了引擎的**确定性**。然后完成 11×5 的冻结结果及排行榜。

### 3.2 原始冻结结论：`4e908173` 赢了，但结论依赖旧执行假设

| 原始对象 | 事实与身份 |
|---|---|
| Stage-B Winner | `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`；Stage-A #5 → Stage-B **#1** |
| Winner 模型配置 SHA | `0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88` |
| Winner 典型参数 | learning_rate=0.08、colsample_bytree=0.8、L1=10、L2=400、depth=6、leaves=63、min_leaf=50 |
| 原始 Baseline 模型 | `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`；Stage-A #48、Stage-B 冻结原名次 **#5** |
| Baseline 模型配置 SHA | `793021af66034ec508848ee032f6d309103291981abb63d11ae779da9c5ee7d3` |
| 实验底层 | Alpha158、raw20、freq20、Top20/Drop2、五相位与 424 交易日；旧 Qlib 动态指数成分报价口径 |

冻结旧报告里，Winner Phase0：Sharpe **1.276262**、策略 CAGR **29.3178%**；Baseline Phase0：Sharpe **1.122058**、策略 CAGR **23.0149%**。这是**旧模拟假设内保存的数值**，不是今天认可的可交易策略绩效。

2026-10-08，基于当时 Stage-B 冻结选择，项目通过 [PR #26](https://github.com/AT2018cow/qlib/pull/26) 将 `4e908173` 作为 canonical，标准发布日期文件名不变，建立独立新 paper lineage `stage_b_winner_canonical`，避免旧 Baseline 现金、持仓和净值混入新策略；见[上线记录](20-csi1000-stage-b-winner-canonical-promotion-20261008.md)。**这是第一轮发生过的生产研究转接事实；复盘发现其历史比较前提有缺陷，不代表后来的 L4 自动追溯批准或撤销了一次生产迁移。**

## 四、复盘与关键错误：旧收益为什么不可信

### 4.1 先验证“数值自洽”，再追查“钱从何处来”

2026-10-09 起以独立实现读取**原始冻结** Parquet、报告摘要、交易信号和决策。最初 GitHub 无法取得真实 Parquet 时，Sharpe/CAGR 的独立复算被明确标记 `BLOCKED`，未凭摘要冒称验算完成；取得十份原始 Winner/Baseline×五相位报告后，完成 **10/10 字节 SHA、一致的 424 天日期、独立 Sharpe/IR/CAGR/MDD 和账户内部现金/估值/费用平衡**的复算。比如 Winner Phase0 仍得到 Sharpe `1.2762619476`、CAGR `29.317814%`；这说明报表**按旧数据算术没有错**，不是证明下单模型具有现实盈利能力。

进一步做独立逐日决策、订单及成交账本核验：Winner Phase0 全部 424 日订单决策可重建；旧报告可严格对账，但识别出**退指后报价缺失造成的不可交易伪影**。这一步改变了问题：关注点由“指标公式是否有 bug”转向“交易所到底能否持续读取已经买入股票的报价、能否真正卖掉退指持仓”。

### 4.2 被证实的 F1 根因和因果链

**具体错误：** 旧 `Qlib Exchange(codes='csi1000')` 把“动态 CSI1000 指数当前成员”同时当作**行情报价订阅范围**。个股一旦从指数剔除，就不再出现在动态成分 `D.features(codes, ... )` 的未来日期报价里；但它可能仍是策略此前买入、在真实市场继续上市且可交易的股票。模拟器把“缺行情”解释成“不可交易/持仓价格沿用最后一笔”，导致无法按策略卖出，且估值路径扭曲。

Winner Phase0 的冻结轨迹已查明：

1. `SH603301`、`SH688066` 于 **2025-06-30** 退指，随后旧报价表不再提供正常退指后的 open/close/factor。
2. 到 **2025-07-01** 原模拟持仓达到 **22 只**；Top20/Drop2 的买卖槽位因“无法卖出+超过目标仓位”受到阻塞。
3. **2025-07-02 至 2026-06-29 连续约 240 个执行交易日无订单**。这种“冻结”并非模型自动发现稳定持仓，而是数据访问和换仓机制的产物。`SH603301` 于 2026-06-30 重新入指时才恢复部分报价/交易机会；`SH688066` 到回测结束仍留在组合中。
4. 旧轨虽然内部 cash/position/NAV 可以完全对平，却不能等同正常持股、报价、开盘成交的现实路径。**因果顺序**是：动态成分报价范围错误 → 缺价/假停牌 → 无法卖出与估值延续 → 订单数、费用、换手、收益路径变化 → Sharpe/CAGR/MDD/排名被扭曲。

**F1 修复原则：** 在**同一原 provider、同一日历、同一冻结预测、同一历史 PIT 可买池和同一交易费用**下，仅把 Exchange 订阅的报价股票改成**该执行期间历史曾属于 CSI1000 的代码静态并集**。股票只有符合当天的既有预测/资格才能进入买入候选；静态并集**只扩大可查询的持仓后续报价**，**不能**拿未来才入指的股票提前选股。缺真实 open/close/factor 时 STOP，不填假价格；旧报告原样保留、不覆写。

### 4.3 Winner Phase0：旧执行与 F1 修正的数字差异

| 指标 | 旧冻结执行 | F1 静态报价回放 | 变化或解释 |
|---|---:|---:|---|
| Sharpe | 1.276262 | **0.998878** | 约 −0.277 |
| 年化策略 CAGR | 29.3178% | **23.4107%** | **−5.91 个百分点** |
| 保存基准相对超额 CAGR（小数） | 0.142516 | **0.090327** | 下降约 5.22 个百分点 |
| Information Ratio | 0.687576 | **0.482338** | 下降 |
| 最大回撤 MDD | −12.1268% | **−27.6412%** | 回撤明显加深 |
| 年化波动率 | 0.214666 | **0.233173** | 增大 |
| 平均换手率 | 0.056751 | **0.201329** | 恢复较正常的交易活动 |
| 累计成本率（冻结摘要口径） | 0.023593 | **0.084912** | 交易恢复后费用增加 |
| 旧停摆期间订单 | 0 | **950** | 相同冻结信号下恢复了交易 |
| 完整 424 日订单 | 旧 482 | **新 1,696** | 数量口径来自独立运行记录 |

这是一份**非常重要的纠错案例**：账本一分钱不差，仍可能因为报价/持仓路径错误而产生无法解释的“高收益”。修复后的 Winner Phase0 通过同 provider 的第二套现金/持仓/NAV 算术对账；但历史 ST、停复牌、IPO、外部盘口真实成交与订单容量独立认证仍有限，不能因回放修好便称“已证实真实成交”。[完整结果与界限](30-csi1000-f1-quote-coverage-fix-validation-20261009.md)、[第一阶段独立审计](29-winner-phase0-execution-audit-20261009.md)。

### 4.4 把 F1 修复扩展到全部 Stage-B：55/55，旧排名被推翻

随后在不重训、不动参数、不碰旧 signal、旧模型/旧 Volume 的前提下，对 **11 候选×5 相位=55 个**原始 Stage-B 单元做：

- Legacy 精确复演 **55/55 PASS**，确保真的比较同一批旧结果；
- F1 固定历史报价静态并集（记录为 1,307 只代码）**55/55 完成**，全部 424 日同频同参数；
- 重新用原有冻结 `rank_stage_b_candidates` 方法排序，并保存旧/新订单、NAV、来源 SHA、费用和否决原因，绝不因结果而重写规则；
- 因为同一已被看过的尾段已经参与了历史选择，这次只能叫**诊断性历史重排**。

| 关键候选 | 第一轮原 Stage-B 名次 | F1 纠错后诊断名次 | 含义 |
|---|---:|---:|---|
| **原 Baseline `23b92de0`** | #5 | **#1** | 在修正交易模拟下反而领先 |
| `c98856b4` | #2 | **#2** | 一直是有竞争力的历史候选 |
| `a419047a` | #8 | **#3** | 修正后显著前移 |
| `e74b3bbe` | #3 | #4 | 相对位次下降 |
| **原 Winner `4e908173`** | **#1** | **#10** | 旧赢家的选型优势丧失 |

该 55 格工程与数值诊断已完成，**不是结论“Baseline 应立刻替换生产 Winner”**，因为：使用的是看过且消费过的测试尾段；现实成交约束和独立外部市场数据仍有未闭合问题。此时正确治理动作是**保留当前 canonical 不自动切换**，并另开经过事先登记的研究协议验证替代方案。（[纠错全表](35-stage-b-corrected-rerank-results-20261010.md)、[后续审计与选择边界](45-csi1000-audit-residual-strategy-decision-handoff-20261011.md)）

### 4.5 反查 Stage-A：320/320 纠错也完成，但原排名保持不变

为避免只审查 Stage-B、放过 Stage-A 初筛的历史错误，又以**原来 80 模型×4 折的冻结信号**开展一次性零训练修复回放：

- **320/320 Qlib 回放 PASS，0 BLOCKED，原 80 名次与四组 LOFO 全部可复算**；原 Stage-A 第一名 `59b93929`、原 Winner `4e908173` 在 Stage-A #5、Baseline #48 **均未因本轮执行修复而改名次**。
- 只有 **69/320** 个 candidate-fold 的订单/NAV 改变，**全部位于 fold4、2024-12-31**，69 个修复后 20 日累计收益都降低；最差折优先排序中的首要 fold 对 78/80 个候选是 fold1，故本次 fold4 改动没有触及首要排名向量。
- 复核再次证实**LOFO 不稳**：剔除 fold1 的相关系数约 **0.393**、Top10 重叠仅 2/10，绝不能以 Stage-A 排名未变作为“修复后模型泛化已认证”。
- 证据审计还纠正了运行历史计数：成功批次 **103 个已验证复用、217 个本次新算**，不是最初口述“只复用 16、重新计算 304”；其中 87 个复用来自被中断运行已完成的不可变单元。合计 30,565 笔订单开盘价未出现缺失；存在在同一 provider 下已被逐条解释/保留局限的停牌缺收盘日。未恢复的旧 `--detach` 调用日志明确标注，不补造。
- 这次 Stage-A **不重新 fit**，不覆盖原始结果。它只证明同一冻结预测下的数值敏感性和源一致性，不提供新的 OOS，更不授权生产晋级。

证据：[PR #64 全量结果](https://github.com/AT2018cow/qlib/pull/64#issuecomment-6109489561)、[独立复核含排名/LOFO/103 复用纠错](https://github.com/AT2018cow/qlib/pull/64#issuecomment-6109545893)、[append-only 审计补件](https://github.com/AT2018cow/qlib/pull/64#issuecomment-6110491306)。

## 五、由复盘转入第二轮：不“回滚旧实验”，而是重新登记可审计的产品研究

### 5.1 为什么第二轮不只是一遍新的 Stage-A 调参

第一轮揭示：光调模型参数、光看平均收益会误判。第二轮必须首先锁住**正确交易、基准、净值与源快照**，然后拆成相互可归因的四个方向，而不在看完结果后无限扩搜：

- **P0 控制重现**：原 Baseline、原 Winner 的 Top20/Drop2，Phase0/10，逐格恢复旧执行轨迹并复现 F1 纠错参考，确认账务工程可用；
- **L1 投资组合结构**：不重训，只变 TopK/DropN；寻找集中度和换仓节奏上的改善；
- **L2 LightGBM 参数**：以独立真实 fit 检验既有 Stage-A 备选和少量事先提出的新配置；
- **L3 模型结构**：在单一原 Baseline 锚上测试重训频率 10/40 日，只有频率先通过才测试 10/40 日标签；
- **L4 五相位决赛**：从三层合格候选中一次性冻结最多五个挑战者 + 原 Baseline，对相同真实 424 日区间用五个固定 phase、**七项预注册净收益/风险/成本/数据门**给出最终研究裁决。

**明确执行边界：** 不改原 Stage-A/Stage-B 数值、仍用同一既有 provider、已消费历史只用于探索、不把 GitHub CI 误作私有 Modal 真实回测；任何阶段没有挑战者通过，允许**成功完成一个阴性实验**。数据源只读，研究结果 Volume 独立，按 run/code/方案/source/quote/候选/相位全 SHA 绑定，发生未知失败即停。L3/L4 根据 2026-10-14 用户授权取消**运行期 USD 预算拦截/账本工作量**，但绝不取消预注册方案、次数上限、证据校验或产品审核。相关变更见[合同 53 顶部修订](53-csi1000-budgeted-layered-strategy-search-contract-20261012.md)。

### 5.2 关键身份：同一个 Baseline 模型不等于同一个策略组合

| 概念 | 模型候选 ID / 策略配置 SHA | 在第二轮角色 |
|---|---|---|
| **原始正式 Baseline** | 模型 `23b92de05cf36c82998de684d0fbf64d81ee54490d755bd3cf96311c00286785`；**Top20/Drop2** 配置 `e7d1fab6c5e8309c54bbc34bd7741151421eb67ce447579a61d8cfe72d548191` | **全部层共用的唯一正式对照** |
| 已上线生产 Stage-B Winner | 模型 `4e908173705c76fee3782be37068672a3a845bd61894202f3152afe3b9d81ef2`，模型配置 `0f2cf94d179e7b9982f873588a3afe25fa02991c873954fea11d2db0e025cb88` | 研究和部署史上的参考；**不是**本轮 P0 |
| Baseline 模型 + Top10/Drop1 | 同一个 Baseline 模型；**新组合** SHA `95ae81ba885dad4e6eb31838e0bbea55d9281807289efe9ce150cbc12edc74b6` | L1/L4 **挑战者**，不能假装是正式对照 |
| `c98856b4` + Top10/Drop1 | 模型 `c98856b460aba687640d422e85a82a545bf5b6ca932e906a180699e0f6cad19b`；配置 `1ca6fae56e8c4e40c73b1a45866825930aecba36ec529544a50da5408c93d317` | L1 历史两相位第一名 |
| `a419047a` + Top10/Drop1 | 模型 `a419047ab97ec0273ebe67791b6e225e824b2764f5932d73651efd89add5a677`；配置 `3ddff2cd6138f0adf003ff6d899a496ed2393ba26dddccc6ce7af274532535e0` | L1 第三名 |

报告后文所有 `Δpp`（百分点改善）均指**相对同相位、原正式 Baseline Top20/Drop2** 的扣费、保存基准相对超额 CAGR 差；中位统计与最差统计不能相互替代。

## 六、P0：先让控制组重现，再谈第二轮收益

**Run `p0_20261013_c1`，代码 `cffb9cd56d5259dbb8efa7afacfca15c3ae0f1b8`。** 使用原 Stage-B 55 份完整冻结来源清单，在同一 provider/source/quote 身份和静态历史报价并集下，独立研究 Volume 执行 4 个已注册对照单元（Baseline/Winner×Phase0/10，固定 Top20/Drop2），**4/4 PASS、0 新训练、0 BLOCKED**。四格共用原始 424 日窗口，逐日旧执行/F1、新账务、订单、费用与参考哈希经过门禁；操作者导出 25 份签署校验材料。

| 对照格 | 修正后期末 NAV（约，元） | Sharpe | 修正后相对超额 CAGR |
|---|---:|---:|---:|
| Baseline / Phase0 | 153,638,597 | 1.1921 | 13.06% |
| Baseline / Phase10 | 167,295,247 | 1.3716 | 18.73% |
| 原 Stage-B Winner / Phase0 | 144,235,985 | 0.9989 | 9.03% |
| 原 Stage-B Winner / Phase10 | 156,672,462 | 1.2221 | 14.34% |

P0 两个相位的原 Baseline 均胜过原 Winner，进一步支持修正旧交易口径的必要性；但 P0 的作用是**验证对照和打开 L1 工程门**，不是凭两个相位发布新 Winner。原源卷只读，未修改生产模型。[P0 执行报告及 SHA/费用](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6137061458)、[P0 审查放行 L1-A](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6139622313)。

## 七、L1：TopK/DropN 组合择优，A→B→C 三阶完成

### 7.1 为什么选组合：训练不变，收益/换仓却可能大变

在同一原始模型得分、同一个当天 PIT eligible 集合、同一 F1 执行行情与费用下，调整 `TopK`（目标持仓数）和 `DropN`（每日渐进淘汰只数）。一套预测可以生成许多不同交易路径：持仓集中度、频繁换仓、买入金额、手续费、MDD 都必须重新算，**不能拿同一 signal 就直接复用另一套组合的 NAV**。模型信号冻结后可**零 fit**做独立 replay，节省大量付费重训，并能归因于组合参数。

### 7.2 L1-A：32/32，单一方向出现亮点

- **2 个模型×8 组合×2 相位=32 个逻辑格**，Run `l1a_20261013_a1`，`32/32 PASS`；4 个 Top20/Drop2 控制格按 SHA 严格与 P0 复用；新增模型 fit=0。15 个非自身 Baseline 组合进入粗筛。
- **唯一初筛合格：原 Baseline 模型 + Top10/Drop1**：最差相位 Δ **+10.44pp**、中位 **+16.12pp**、MDD 改善 4.32pp、相对换手率约 1.002。
- 提前受到关注的 **Drop3 方向全部失败（已测 6 种 Drop3 均未过粗筛）**；例如 Baseline Top10/Drop3 最差 −15.78pp、换手比 2.836，说明“多换股就赚得多”在纠错口径下不成立。
- 32 单元指标从 parquet 独立重算、165 份源/产物 SHA 通过操作者本地检验；**只有 1 个配置出线，还不足以直接满足进入 L1-C 的扩算门**，因此先做 L1-B。

依据：[L1-A 执行评论](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6140381979)、[独立产物复算](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6140838535)。

### 7.3 L1-B：144/144，覆盖更多模型与组合，严格复用

- 扩至 **6 个代表模型×12 组合×2 phase=144 个累计格**，其中 **32 格逐字节验证并复用 L1-A**、理论最多 **112 个增量回放**；仍为零新 fit。
- 以真实 Smoke、同 SHA 续批、源/quote/费用/P0 完整性校验执行；曾遇到一个**已存在复用目标时旧 `src` 变量未随格更新**的确定性代码错误，导致错误地报告文件漂移，而非文件真的变了；通过 [PR #73](https://github.com/AT2018cow/qlib/pull/73) 修复并补“多格已存在+再次物化”测试，新代码 SHA 下重新 Smoke。
- 执行记录中一度出现“32 派发却新增 64 格”的计数疑问；后来证明**同前缀另有一个先完成的 32 格 App，观察窗口之前的起点值已过时**，并非凭空生成 32 格。纠错后保留 App ID、来源与顺序，不删除疑问记录；最终完整 144/144 后进行独立核验再进入 L1-C。
- 此处的 `144/144` 是**评估完整性 PASS**，不是 144 套挑战策略合格；L1-B 让组合方向中多个配置具备两相位可比较的证据，才满足 L1-C 扩展条件。

依据：[L1-B 新版本 Smoke](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6145241128)、[异常报告](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6147533040)、[计数解谜更正](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6147818013)、[执行合约与复用](53-csi1000-budgeted-layered-strategy-search-contract-20261012.md)。

### 7.4 L1-C：264/264 完整两相位组合筛选，最终 Top3

- 扩到既有 **11 个 Stage-B 模型×12 portfolio×2 相位 = 264** 个累计格。其中完整 SHA/版本/来源等价性复用 L1-B **144 格**，剩余 **120 格**真实回放；**全程零新模型 fit**。
- Run `l1c_20261014_a1`：最终 **264/264 PASS、0 BLOCKED**。来源 55 格与 provider 身份一致，全部报告日度账户/费用可对账，同相位原 P0 都能配对；全量含 131 套挑战者配置（132 含正式对照）。
- **7 个配置通过“两相位粗筛”，来自 5 个不同模型**。粗筛通过仅表示值得进入五相位研究，不是 L4/产品 PASS：

| L1-C 两相位通过者 | 组合 | 最差 Δpp | 中位 Δpp | MDD 恶化 Δpp（负值更优） | 相对换手比 |
|---|---|---:|---:|---:|---:|
| **c98856b4** | **Top10/Drop1** | **+21.63** | **+22.74** | −3.85 | 0.996 |
| **原 Baseline 模型** | **Top10/Drop1** | **+10.44** | **+16.12** | −4.32 | 1.002 |
| **a419047a** | **Top10/Drop1** | **+7.85** | **+9.97** | −0.55 | 0.995 |
| 17dbfe28 | Top10/Drop1 | +3.77 | +7.33 | −1.77 | 0.979 |
| e74b3bbe | Top10/Drop1 | +3.60 | +10.22 | −0.68 | 0.998 |
| c98856b4 | Top20/Drop2 | +2.11 | +2.52 | −0.65 | 0.999 |
| c98856b4 | Top20/Drop1 | +1.48 | +3.48 | −1.84 | 0.523 |

按冻结排序与 **L1 最多 3 个 L4 提名名额**，严格选定表内前三名。可解释的方向：**Top10/Drop1 的集中持仓、缓慢换仓在这两个历史 phase 中占据主导**；Drop3 与 Top30+ 未进入出线名单；c98856b4 有三种组合在初筛通过。**但两个 phase 不能代替五个 phase，更不能证明真实投资稳定性。**

终局证据为 [L1-C 实际执行报告](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6156598244) 和 [独立排名审查、Top3 SHA 确认](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6159611769)。操作者记录曾出现 **“6/7 个 App”** 和 **“下载 1063 文件/1336 条 SHA 清单”**的统计口径差异；审查要求 append-only 解释而不是凭描述推断完整字节复核。本报告只引用已确认的 `264/264`、`144 reused / 120 computed` 和出线配置，不擅自把不同口径归并成确切 App/文件总数。

## 八、L2：真正重新训练新/备选 LightGBM 参数，阴性结项

L2 不再改变组合，保持原始 Baseline 对照的 **Top20/Drop2、raw20、freq20**，只比较事先指定的备选模型超参数；目的在于避免把 L1 组合盈利误认成模型学习率/正则贡献。

- 已有 **4 个来自 Stage-A、但未进入 Stage-B 11 人的备选**：分别在 Phase0/10 做完整滚动、每 phase 22 次真实 fit → `4×2×22=176 次 fit`。两相位 worst/median Δpp 依次为：`−7.84/−2.82`、`−7.87/−4.14`、`−11.17/−5.15`、`−11.45/−9.59`，全部未过粗筛。
- 另有 **4 套预先提出的新超参数**，先做 **4×4=16 个短折 fit**，短折中位改善全部为负；按事先的多保真门禁，**不再给它们补长期滚动 fit**。不能称这些未经长期验证的新参数已经被五 phase 证明劣于 Baseline。
- Run `l2_20261014_b1`，6 段前台可恢复工作流，**192 次真实 fit = 176 长期 + 16 短折**（包含完整尝试计数）；`qualified_configs=0`、`top_two_for_final_pool=[]`、研究执行状态 `PASS`，结果状态**阴性**。操作者报告约 USD **6.44** 付费，非独立账单签证；保留历史账单，不再要求扩造费用系统。
- 实施中识别出“长 phase receipt 的签名 quote 键被细节重复覆盖”的全量专有 bug，先由 [PR #76](https://github.com/AT2018cow/qlib/pull/76) 修复，再将完整真实作业交给自动安全暂停/续跑；不能把首轮两次 fit Smoke 直接等同长 phase 的终局路径通过。

结论：**L2 实验执行成功，但没有新模型合格进入 L4，L2=0 提名。** [L2 操作者终局](https://github.com/AT2018cow/qlib/pull/75#issuecomment-6165275411)、[审查结项](https://github.com/AT2018cow/qlib/pull/75#issuecomment-6165538082)。

## 九、L3：重训频率/标签期限结构搜索，阴性结项

L3 固定**原始 Baseline 模型 + 原始 Top20/Drop2**，不沿用 L1 Top10/Drop1 优胜者，以便只归因于模型重训时钟或标签成熟期。阶段设计：

| 层次 | 候选 | 预注册两相位 | 是否真实运行 |
|---|---|---|---|
| Stage1 频率 | raw20 / freq10 | 0、5 | **已运行** |
| Stage1 频率 | raw20 / freq40 | 0、20 | **已运行** |
| Stage2 标签 | raw10 / freq20 | 0、10 | **未触发；不是被证伪** |
| Stage2 标签 | raw40 / freq20 | 0、10 | **未触发；不是被证伪** |

L3 代码独立在 [PR #77](https://github.com/AT2018cow/qlib/pull/77) 实现：真实按 as-of fit、标签成熟和 purge、执行锚 T→T+1 前一交易日信号保留、phase 不同频率的 ordinal 配对、同一天 424 日基准相等门禁、原子 job+字节 SHA receipt、Stage1/终局独立不可变 marker、单前台自动分波及 safe pause。数据与原 P0 只读，结果另存 Volume v2，最多 80 容器是配置上限，不是必须占满；**250 次 fit 是科学实验范围上限，非运行期 USD 费用门**。

Run `l3_20261014_a1` 经**两次真实训练 Smoke**、4-worker 首波 3 次新 fit 校验、再单前台全量工作后，最终 **109 次真实 fit**，其中前 5 次为 Smoke/pilot、后续 Stage1 **104** 次；四条 phase 线全部产生 424 日汇总：

| L3 Stage1 | 最差相位 Δpp | 两相位中位 Δpp | 粗筛结果 |
|---|---:|---:|---|
| raw20 / freq10（0、5） | **−5.03** | −0.42 | FAIL |
| raw20 / freq40（0、20） | **−3.13** | −0.37 | FAIL |

两结构都没达到 **worst 和 median ≥ +0.5pp** 的预设门槛，**正确跳过 Stage2 label raw10/raw40**，最后 `L3_COMPLETE / L3_DONE / screened=2 / 0 nominee`。不因二者性能不佳就事后改阶段依赖或扩大 250 fit；**L3=0 L4 提名**。证据：[L3 确认阴性及 109 次 fit](https://github.com/AT2018cow/qlib/pull/77#issuecomment-6170790390)。

## 十、L4：五相位完整决赛，最重要的阴性结果

### 10.1 一次性冻结最终四套策略，而不是继续筛历史最优

L1 提名 3；L2=0；L3=0。于是最终名单为 **4 套完整策略（3 挑战者+正式原始 Baseline 对照）**。机器可读冻结文件：[58-csi1000-l4-finalists-frozen-20261014.json](58-csi1000-l4-finalists-frozen-20261014.json)，名册 SHA：

`b6bd3b3d687d017fb6586d68e88ab71691c2225eb4545e853560a776c9cae1c8`。

所需 **freq20 五相位 [0,4,6,10,15]**，同一个固定 424 日回放窗口。三个底层 LightGBM 模型在原 Stage-B 已分别经历全部五相位的**真实训练**；因此 L4 **只复用 15 份逐件核验的冻结模型×phase 信号**，却要为四套不同 TopK/DropN 分别跑 **4×5=20 份真实 F1 订单/成本/NAV**。这样 **新增 LightGBM fit=0**，而非拿“只做两相位”冒充完整决赛；同模型的 Baseline Top20/Drop2 与 Baseline Top10/Drop1 允许共享同一信号，但不能共享交易报告。

完整执行器：[L4 代码 PR #78](https://github.com/AT2018cow/qlib/pull/78)，[运行方法](59-csi1000-l4-five-phase-runbook-20261014.md)。来源和 P0 只读 Volume；独立 `qlib-csi1000-l4-v2` 储存新策略产物。两次初始 Smoke 失败来自确定性**代码**问题，不是数据或模型失败：首次 inventory 把“尚待派生的 quote SHA”与空占位串强行相等（[PR #80](https://github.com/AT2018cow/qlib/pull/80) 修正）；其次把远端 `/root/qlib` 路径错误地交给本地协调器（[PR #81](https://github.com/AT2018cow/qlib/pull/81) 修正）。两次均在真正 replay/落盘前停止、保留事故日志；新增**真实函数的离线`.local()`路径测试**防止只测最终模拟摘要却漏掉派生与主机路径。

### 10.2 真实 L4 b2 完整执行及证据

最终以**干净 main** `a1878566946f3bf69d7d4c2577c4bf4b0f1e0934`、Run `l4_20261014_b2` 执行：

- Smoke App `ap-o933Bkwros475Kd3QwBAqY`：**2/2 真实 F1 回放 PASS**，先核验全部 **15/15** 旧信号，再回放原 Baseline Top20/Drop2 Phase0 与 c98856b4 Top10/Drop1 Phase0。原 Baseline 逐日账户收益与签署 P0 最大差 **1.1×10⁻¹⁶**，没有新训练。
- Complete App `ap-5o92fcjq4onAXuE5FNcc8k`：单前台协调者，`--max-workers 16`，已保存 Smoke 2 格直接复用，后续 14+4 格两波共完成其余 18 个回放。终局 `L4_COMPLETE 20/20 verified replay cells 0 new fits`、`L4_DONE`，真实 `EXIT=0`，无 traceback/STOP/BLOCKED。
- 全部 **20 个不可变 receipt**，每个绑定 `fixed_report.parquet`、`decisions.json`、`signal.parquet` 三个 F1 产物，即 **80 个 cell 文件、60 个被 receipt 绑定的策略产物 SHA**。操作者在本地完成 **60/60 SHA MATCH**，保留整包约 **66 MB** `/tmp/l4-b2-final/` 及终局标记 `smoke_pass.json`、`l4_final_decision.json`、日志。**这里的 60/60 是操作者独立重算后提交的执行声明；本总结编写方尚未亲自下载私有 66 MB 包再做第三次字节哈希**，不得把报告审阅等同原始字节独立复核。
- `final_remote` 在决定赢家前逐份读入 20 个真实结果，重新算账户指标与签名，并再次核对 P0 Phase0/10、五相位同日 benchmark、同相位组合成本以及七项门禁。`L4_RESEARCH_COMPLETE_NOT_RELEASED` 不代表已批准上线。

### 10.3 事先冻结的产品门槛为何必须坚持

合同 53 §1.2 要求三名挑战者相对**同相位原始 Baseline Top20/Drop2**：

1. 五相位**最差**净相对超额 CAGR Δ **≥ +1.0pp**；
2. 五相位**中位** Δ **≥ +1.0pp**；
3. 至少 **4/5 phase** 的净总收益高于同相位 Baseline；
4. 五相位最深 MDD 相对 Baseline 最深 MDD 恶化 **≤ 3.0pp**；
5. 五相位中位换手率比例不超过合同上限；
6. **所有五相位**在额外 +10bp 交易费用压力下仍须较 Baseline 有正收益优势（另报告 +20bp 作为补充，不事后改变门）；
7. 原始信号、日历、基准、PIT、费用/账本、覆盖、SHA 和跨相位配对证据合格。

执行器按 **worst-phase** 优先进行 FAIL/NOT_ELIGIBLE 判定；后面的条件即便单独不错，也不能弥补第一个坏相位。

### 10.4 最终五相位排行榜：三位挑战者均失败

| 策略/配置 SHA 前缀 | 五相位最差 Δpp | 中位 Δpp | 净总收益胜出 phase | MDD 恶化 Δpp | 换手比 | +10bp 所有相位通过？ | 终局 |
|---|---:|---:|---:|---:|---:|---|---|
| **c98856b4 / `1ca6fae5` / Top10 Drop1** | **−6.24** | +21.63 | 4/5 | −2.51 | 0.998 | **否** | **NOT_ELIGIBLE：worst_phase** |
| **Baseline 模型 / `95ae81ba` / Top10 Drop1** | **−2.83** | +10.44 | 4/5 | −2.84 | 1.001 | **否** | **NOT_ELIGIBLE：worst_phase** |
| **a419047a / `3ddff2cd` / Top10 Drop1** | **−3.63** | +7.85 | 4/5 | +0.92 | 1.016 | **否** | **NOT_ELIGIBLE：worst_phase** |

**这张表是两轮研究最关键的策略解释：**

- 只看 L1 两相位时，`c98856b4/Top10 Drop1` 最差改善还是 **+21.63pp**，L4 五相位后最差却 **−6.24pp**。这不是报告算术矛盾，而是**新增三个此前未纳入 L1 粗筛的 phase 中存在非常不利的结果**；不能把两 phase 的最差值和五 phase 的最差值混称。
- 三套策略**均有 4/5 个 phase 的净总收益胜出**，且五相位中位 Δ 均为正，说明它们在已消费历史的某些重训起点具有亮点；但三者都暴露**一个可使优势逆转的重训时钟坏相位**，缺少本轮合同要求的最坏情境可靠性。
- **+10bp stress 全部为 false** 进一步强化成本压力风险；尽管程序首先以 `worst_phase` 记录不合格原因，也不能省略压力失败事实。
- 本轮真正通过**所有**产品门槛的挑战者：`0/3`。最终 `l4_final_decision.json` 为：`decision=KEEP_BASELINE_FOR_PRODUCT_SELECTION`、`n_pass=0`、`promotion_authorized=false`、`historic_data=HISTORICALLY_CONSUMED`。**产品逻辑最终是“保留现有生产配置、不做发布”，不是强制把产品改成研究 Baseline。**

原始证据：[L4 b2 五部分终局报告](https://github.com/AT2018cow/qlib/pull/81#issuecomment-6173350754)、[独立代码/合同审核后采纳阴性结论](https://github.com/AT2018cow/qlib/pull/81#issuecomment-6174408306)。

## 十一、工程建设与重大事故复盘：为何这两轮不只有一个排名表

### 11.1 逐阶段构建、审查、运行、核验

第一轮建立了确定性 TopK-dropout、Alpha158/LightGBM 配置哈希、训练成熟窗口、固定相位、滚动 fit 和多层审计；第二轮把**模型身份 → 预测 SHA → 交易执行 → 日度 NAV → 统计排名 → 产品门 → 单独发布审批**做成严格链路。

| 容易出错的点 | 实际出现的问题/改进 | 长期教训 |
|---|---|---|
| 看模型收益时不看实际成交 | 动态 CSI1000 成分用作报价列表，退指后持仓被错误冻结、约 240 日零订单 | 回测须独立复核**订单、持仓、open/close/factor、成本/NAV**；公式 PASS≠经济路径 PASS |
| 只看平均值或一个好运 phase | Stage-A fold1 主导；L1 Top3 两 phase 很强，但 L4 都有一个严重坏 phase | **事先固定 worst-phase 门**，宁可接受阴性结论，不事后换权重/去掉坏 phase |
| 只有旧摘要、没有底层证据 | 初期 Stage-B 指标复算 BLOCKED；补齐 Parquet、signal、decision 和数据日历后才推进 | 原始不可变字节、SHA、来源 snapshot 和失败原因比漂亮截图更重要 |
| 回放复用的多格恢复 | L1-B `src` 指向错误使旧目标字节比较假冲突（PR #73） | 测试必须有**多个缓存单元、已有目的文件、半完成恢复**分支 |
| 模型训练 Smoke 不覆盖 phase 终局 | L2 full-phase receipt 重复 quote 字段（PR #76）；L3 前期曾有阶段 marker、日期锚、决策字段等 P0 问题（PR #77 修复） | 同时测试**实际 fit → phase → 筛选 → 冻结决策**，不能用手造 flat PASS 行替代真实接口 |
| 本地与远端初始化顺序 | L4 a1 将待派生 quote 与空串强制比对（PR #80）；b1 将容器 `/root/qlib` 路径用在本地协调器（PR #81） | 不仅测试辅助函数，还要真实调用**`inventory_remote.local()`、`run_l4` 本地入口**并审查 host/container 路径 |
| 断点续跑与计数可信度 | Stage-A 103 复用/217 新算勘误；L1-B 先行 App 引发 32→64 表面跳数；OpenCode `--detach` 与终端超时不兼容 | 单前台协调者、真实 App ID、带 SHA 的 receipt，`SAFE_PAUSE` 不冒充 `DONE`，失败日志 append-only |
| 高并行写入安全 | L3/L4 同时有多容器产物，不能随意让多个 v1 Volume 写者竞争 | 原始数据/P0 只读，结果写独立 v2；完整 job SHA + 原子发布 + 复验 |
| 费用记录和报告本身也可能写错 | P0 失败镜像费用误写成全部 P0 费用、L1-C App/校验文件数有口径差 | 将**运营消耗、逻辑评估格、唯一 fit、复用格、文件数**分开计数；不可精确核验就标注 |

### 11.2 资源使用与费用：仅报告已存在的量，不造新财务数字

本实验运营中多次通过**冻结预测复用、只做 F1 回放**避免重复训练。付费训练主要来自第一轮 Stage-A/B、第二轮 L2（192 fit）与 L3（109 fit）；L1 和 L4 是更便宜但仍需真实执行的回放工作。

有可追溯的**操作者账单估算/记录**如下：

- 第二轮 P0：成功重放 App 约 **USD 0.02641287**，含两次失败构建约 **USD 0.03078**；报告中曾误把 **0.00437** 的失败构建费用当全部 P0，已在[更正评论](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6140880812)澄清。
- L1-A：约 **USD 0.06532158**；L1-C：约 **USD 0.2691**（不能擅自把历史多段回放费用合计为同一个未审计发票）。
- Stage-A 320 格历史纠错回放：部分应用程序报告全量尝试合计约 **USD 0.71174**，包括中断/恢复等既有账务；**不是第一轮 80×4 的真实模型训练总成本**。
- L2 新训练：实际报告约 **USD 6.44**；历史 L2 smoke 单 fit 约 USD 0.0267；真实月账单未由本报告编写人核验。
- **L3 与 L4 的准确合计账单，本报告没有独立一致的全量来源，不填数字，不杜撰“研究总成本”。** 按用户修订，L3/L4 不再实现每 wave USD 运行时拦截；**科学 fit 次数、SHA、PIT、配置与证据门仍有效**。

这里的**经济效益**来自方法：同一冻结模型的 20 套组合可以重新计费回放而无模型 fit；L4 又利用 15 份已证明真实训练的信号，**20 个组合相位回放却零新 fit**。这不是减少实验严谨性，而是避免重复支付相同训练。

## 十二、最终边界、残余风险和下一代研究应如何做

### 12.1 这份报告已经证明什么

**已经有足够证据记录以下工程/研究事实：**

- 原 Stage-B 冻结交易模拟的报价覆盖假设有实质性错误，旧指标内在算术吻合却不能保住原 Winner 的历史选型可信度；修复后的同信号诊断排序发生显著变化。
- 第二轮 P0/L1/L2/L3/L4 各自按合约**完成允许执行的评估**；L1 两相位筛出了有兴趣的 Top10/Drop1 候选，但 L2/L3 没有结构/超参数补充候选，L4 五相位**没有一个候选满足完整产品门槛**。
- 错误机制、修复 PR、原始回放哈希、阴性试验、被跳过的条件分支以及生产零变动都有来源可查；没有为了“必须找到赢家”而修改阈值或删掉坏 phase。

### 12.2 尚未被这两轮证明的事情

1. **未证明最优性。** 不能声称“原 Baseline 一定是全市场最佳模型”，也不能声称“当前 Stage-B 生产 Winner 经过新历史实验被重新认证为最优”。本轮零合格候选只是**预设范围/当前历史样本/当前模拟方法**下的产品筛选结果。
2. **未证明独立未来收益。** Stage-A（2021–2024）已被用来选参，Stage-B/L1–L4（2025–2026）也已多次用于修复和择优。五相位只是**同 424 个交易日的模型年龄/训练起点敏感性测试**，不是五份独立 OOS。
3. **未完全证明现实成交与交易容量。** 同一 provider 的静态报价修正、独立内部账本、订单数量校验不等于交易所外部逐笔确认；历史 ST/IPO/停牌/涨跌停的外部证据以及开盘深度、实盘容量、资金冲击成本仍有 `BLOCKED/DEFERRED` 项。用于投资产品时还需独立可成交性验证。
4. **未进行 L3 标签 Stage2 的独立实验。** 因 Stage1 两个频率失败而不触发 raw10/raw40，是遵守合同，不是证明标签期限在任何场景都无效。
5. **用户侧私有证据仍需妥善保管。** L4 操作者对 66 MB 包 60/60 SHA 重新校验已提交评论；本总结本身**不是**私有原始交易产物的第三次独立重哈希。真正的长期保全需要保留 Volume 导出、日志、source manifest 和 SHA256SUMS 的不可篡改副本。
6. **现有生产处置不是研究结论自动执行。** 历史旧模拟路径的 F1 错误被记录，不表示今天的 canonical 发布/前向 paper NAV 一定重现该错误；也不表示生产现在拥有真实 broker 成交认证。当前 `KEEP_STAGE_B_PRODUCTION_WINNER` 是**不自动变更**的治理动作，未来需单独审批与前向数据。

### 12.3 若未来重开：应该是什么条件

本轮正式 **CLOSED**，不再追加 Stage-A 候选 81、不重启 L1 次佳组合、不为了一个失败 phase 换掉五相位集、不为 L2/L3 不合格者降低门槛，也不继续消费相同 2025–2026 数据宣称新 OOS。下一轮如确有业务/科研需求，应：

- **另立时间戳可证的新研究合同/PR**，事前固定候选、特征、目标、TopK、重训频率、成本、交易与 benchmark、样本切分、风险门与多重比较方法；
- 优先建立真正**在结果发生前固定预测与订单哈希**的前向独立 paper/shadow 观察，而不是回填历史。既有 [47 号前向协议](47-csi1000-forward-shadow-strategy-decision-protocol-20261011.md) 为**原 Baseline vs 现役 Winner 两条对照**，预注册 120/240 个合格配对交易日和固定统计检验，不能擅自塞入 L1/L4 其他模型声称同一前向统计实验；
- 需要研究 Top10 集中度风险、单个坏 phase 的市场机制、板块与退指/复牌、真实成交量和成本敏感性，但此类复盘应与**已经冻结的 L4 是否通过**严格隔离；研究解释可以继续，历史判定不能改；
- 任何新产品候选必须先通过新的独立证据、完成产品门、另开 release/decision PR 并明确批准；默认保持现有发布链路与回滚边界。

## 十三、实验、代码、证据的索引（留给未来复现者）

| 工作节点 | 冻结材料、代码或正式证据 | 关键结论 |
|---|---|---|
| Stage-A 方案 | [16-Stage-A 合同](16-csi1000-stage-a-tuning-contract-20261007.md)、[18-Stage-B 交接](18-next-conversation-handoff-stage-b-20261007.md) | 80×4 短折、七参数、LOFO、最差优先 |
| Stage-B 55 格旧结果 | [冻结 55 结果 JSON](../../results/csi1000_stage_b/stage_b_full_51756897fc752304.json)、[19-Stage-B 选优](19-csi1000-stage-b-final-audit-forward-contract-20261008.md)、[20-生产迁移](20-csi1000-stage-b-winner-canonical-promotion-20261008.md) | 旧 Winner #1，后被执行审计推翻 |
| 算术与执行审计 | [27-原报告独立复算](27-csi1000-stage-b-raw-report-independent-recheck-20261009.md)、[29-订单与利润形成](29-winner-phase0-execution-audit-20261009.md) | 旧公式算得一致但交易状态错 |
| F1 根因、修正 | [30-F1 报价修正实验](30-csi1000-f1-quote-coverage-fix-validation-20261009.md)、[PR #45](https://github.com/AT2018cow/qlib/pull/45) | 动态报价“退指出价缺失”→静态历史报价并集 |
| 55 个 Stage-B 修正 | [35-修正排行榜](35-stage-b-corrected-rerank-results-20261010.md)、[审计证据目录](../../audit/evidence/stage_b_corrected_rerank/) | Baseline #1；原 Winner #10，诊断性 |
| 320 个 Stage-A 修正 | [PR #64 全量审核](https://github.com/AT2018cow/qlib/pull/64#issuecomment-6109545893)、[补件](https://github.com/AT2018cow/qlib/pull/64#issuecomment-6110491306) | 69 个 fold4 变更；排名不变、LOFO 脆弱 |
| 第二轮方案 | [53-分层合同](53-csi1000-budgeted-layered-strategy-search-contract-20261012.md)、[54-操作说明](54-csi1000-layered-search-operator-readme-20261012.md)、[PR #68](https://github.com/AT2018cow/qlib/pull/68) | P0、L1–L4 搜索空间与产品门冻结 |
| P0、L1-A | [P0 4/4](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6137061458)、[L1-A 32/32](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6140381979) | 纠错对照成功、Top10/Drop1 初显 |
| L1-B、L1-C | [PR #72](https://github.com/AT2018cow/qlib/pull/72)、[PR #73](https://github.com/AT2018cow/qlib/pull/73)、[PR #74](https://github.com/AT2018cow/qlib/pull/74)、[L1-C 264/264 及 Top3](https://github.com/AT2018cow/qlib/pull/68#issuecomment-6156598244) | 144/144、264/264，7 通过两相位粗筛，Top3 |
| L2 | [PR #75](https://github.com/AT2018cow/qlib/pull/75)、[PR #76](https://github.com/AT2018cow/qlib/pull/76)、[L2 终局](https://github.com/AT2018cow/qlib/pull/75#issuecomment-6165275411) | 192 fit，0 晋级 |
| L3 | [PR #77](https://github.com/AT2018cow/qlib/pull/77)、[L3 终局](https://github.com/AT2018cow/qlib/pull/77#issuecomment-6170790390) | 109 fit，频率结构 0 晋级，标签按条件跳过 |
| L4 冻结 | [58-最终名单 SHA](58-csi1000-l4-finalists-frozen-20261014.json)、[PR #78](https://github.com/AT2018cow/qlib/pull/78) | 3 挑战者 + 正式 Baseline；五 phase |
| L4 事故修复 | [PR #80](https://github.com/AT2018cow/qlib/pull/80)、[PR #81](https://github.com/AT2018cow/qlib/pull/81) | quote 派生顺序、host/container 路径 |
| L4 终局、数据保全 | [操作者终局 20/20](https://github.com/AT2018cow/qlib/pull/81#issuecomment-6173350754)、[审查采纳阴性结论](https://github.com/AT2018cow/qlib/pull/81#issuecomment-6174408306)、[59-运行手册](59-csi1000-l4-five-phase-runbook-20261014.md) | 20/20、0 fit、0/3 挑战者通过 |
| 生产与剩余风险 | [45-残余研究交接](45-csi1000-audit-residual-strategy-decision-handoff-20261011.md)、[47-前向协议](47-csi1000-forward-shadow-strategy-decision-protocol-20261011.md) | KEEP_CANONICAL、待真正前向与实盘认证 |

## 十四、最终结论与正式收工记录

### 第一轮究竟得到了什么？

Stage-A/B **确实完成了约定范围的 LightGBM 调参与五相位历史候选选择**，且原阶段内的报告计算、模型身份与可复现性有相当的工程证据。当时结论是 `4e908173` 历史排名第一，随后将其上线为 canonical。但是，后来的独立利润形成审计**揭示报价覆盖/交易执行口径的系统性缺陷**：退指后持仓被错误模拟成长期停牌并不能卖，进而影响收益、回撤和排名。修复后 55 格的诊断性排序由**Winner #1→#10、Baseline #5→#1**，使旧“性能最优”的原始证据基础失效；不能用旧图表继续宣传其经历史收益证明更优。

### 第二轮究竟得到了什么？

在**纠正执行范围、固定唯一正式 Baseline、按层归因、证据不可变、先粗筛再五 phase 严格决赛**的设计下：
- P0 成功证明原始对照的 corrected F1 回放可复现；
- L1 表明组合结构值得研究，历史 Top10/Drop1 三套挑战者在双相位上很强；
- L2 没有新的超参数合格；
- L3 没有新的频率结构合格；
- **L4 最终五相位拒绝全部三名挑战者**，尤其是每套均出现一个严重坏相位，不能用其余 4/5 优势掩盖。

两轮得出的**更成熟的结论**不是“找到了一套绝对最赚钱策略”，而是：**方法上识别并纠正了会制造虚假回报的模拟缺陷；科学上在更完整、稳健性优先的合同中接受了“暂时没有新的产品级赢家”的结果；工程上建立了可复验、可恢复、不会因阴性结果擅改生产的研究流水线。**

**收工状态（截至 2026-10-15）：**

`FIRST_ROUND_STAGE_A_B_COMPLETED → EXECUTION_BUG_CONFIRMED_AND_CORRECTED_DIAGNOSTICALLY → SECOND_ROUND_P0/L1/L2/L3/L4_COMPLETED → L4_0_OF_3_QUALIFIED → RESEARCH_CLOSED_NEGATIVE → KEEP_EXISTING_STAGE_B_CANONICAL → NO_AUTO_RELEASE`。

保存原始旧结果、修正后诊断、所有失败/阴性指标、App/日志/哈希和本报告。**不再付费重跑本轮，不改五相位门槛，不自动切生产。**
