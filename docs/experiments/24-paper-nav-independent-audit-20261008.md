# CSI1000 Paper NAV：独立会计审计（只读）

本审计器独立于生产计算代码：不导入或修改 paper_portfolio.py、forward_performance.py、Modal cron、模型参数与历史 signals。脚本位置为 scripts/audit_paper_nav.py。

## 四层审计证据

1. **交易及账本恒等式。** 独立重算现金：
   cash_close = cash_previous + Σ(卖出数量 × 成交价 − 卖出费用) − Σ(买入数量 × 成交价 + 买入费用)。
   重放前一快照持仓数量，加买入、减卖出，并核对费用（买入 0.05%、卖出 0.15%、每单最低 5 元）和收盘净值：
   V_close = cash_close + Σ(持仓数量 × 持仓收盘标记价)。
   不将原本的执行报告当成独立行情报价。
2. **前向净值链路。** 独立使用 Decimal 重算
   NAV_t = V_close(t) / V_pretrade_open(2026-10-12)、
   r_t = V_close(t) / V_close(t-1) − 1，首日分母使用起始交易前账户价值。
   与公开 forward JSON 的日期、逐日收益、净值及最新累计收益逐点比较。
3. **外部行情/交易日校验。** 可选提供独立版本化的开盘、收盘、factor 快照和交易所交易日历，核对实际成交价、开盘 NAV、收盘市值、停牌缺价、100 股实际交易单位与复权因子。行情 CSV 列为 date,instrument,open,close,factor（这里的价格应是与 Qlib 同坐标系的复权价格）；交易日历为每行一个 ISO 日期。应在审计记录中另存行情供应商、版本、时间与哈希，不能用“工作日”冒充“真实交易日”。
4. **资产与版本隔离。** 只处理 CSI1000、stage_b_winner_canonical 且 production_lineage.profile 为 stage_b_winner 的记录。其余研究池或老账户不能并入收益。

## 命令

在仓库根目录执行：

    python scripts/build_forward_performance.py --signals results/signals --output /tmp/csi1000_forward.json
    python scripts/audit_paper_nav.py --signals results/signals --forward /tmp/csi1000_forward.json --output /tmp/nav-audit.json
    python -m pytest -q tests/test_paper_nav_independent_audit.py

具备**独立且不可变的报价快照和交易日历**后：

    python scripts/audit_paper_nav.py --signals results/signals --forward /tmp/csi1000_forward.json \
      --calendar /path/to/versioned-calendar.txt --quotes /path/to/versioned-ohlc-factor.csv \
      --output /tmp/nav-external-audit.json

不要把私有行情、账户状态、供应商凭据或内部环境路径提交到公开仓库。

## 状态含义

- NOT_STARTED：没有首个可接受的 2026-10-12 及以后 execution report，绝不表示 0% 收益或通过审计。
- PARTIAL_NOT_CERTIFIED：公共账本关系未见误差，但缺少外部报价、权威日历或首次账户快照。
- PASS：账本、外部报价/复权因子、权威日历和 forward JSON 均完成对账。
- FAIL：发现明确差异；输出日期、错误代码和细节，命令非零退出。不静默修正生产数据。

对已四舍五入到分的资金值允许 0.035 元容差，对收益允许 1e-8 容差。这不是容许未知的账户价值偏差。

## 2026-10-08 核查结论

- 当前 results/signals 仅有旧版 2026-10-08 paper portfolio 文件，没有 execution_report，也没有已生效的新 winner 账户执行日报。**真实生产 NAV 仍处于 NOT_STARTED**，不能称审计通过。
- 生产 Paper 账户使用固定 100 单位对 Qlib 的复权 open 定价取整，而 Exchange 的复权交易单位采用 trade_unit / factor。factor 不等于 1 时存在偏离可能。只有独立报价和实际成交数据才能给出真实偏差大小；审计器提供 FACTOR_LOT_DIVERGENCE 提示。
- Paper 账户在缺少收盘价时可能沿用上次标记价，需独立核对是否为正常停牌，否则存在错误估值风险。
- Forward 生成器目前缺少官方交易日历连续性校验，可能跳过某个报告仍输出累计净值。审计器只在传入权威日历后认证交易日完整性。
- 本 PR 只提供审计与证据，不动生产模型、accounting、cron 或历史 records。生产公式、复权股数与估值策略若需修正，必须在另一独立 PR 中协调研究与网页两个工作流。

## 正式认证前的准备清单

需要保留初始 winner 账户快照、每个执行日的成交列表、开盘/收盘价格和 factor、现金及持仓快照、缺价/停牌处理、交易日历版本和行情快照哈希。每日重新计算并以差异代码报告异常。当前系统如果没有这些证据，不应凭内部 JSON 与 Python 测试给生产 NAV 签发“完全正确”的认证。


## 起点前的 Qlib 复权下单数量修复（2026-10-08）

新增独立修复 PR 将执行日 $factor 传至 PaperPortfolio，按照冻结 Qlib
Exchange 语义计算复权买入数量：

    lots = floor((target_adjusted_shares * factor + 0.1) / trade_unit)
    adjusted_shares = lots * trade_unit / factor

这里的持仓数量为 Qlib **复权股数**，可为小数；实体整手为
lots * trade_unit。现金不足时也按同一复权单位扣减整手，而不是恢复
固定 100 复权股数。若某只具备成交条件的拟买股票缺失有效 factor，
在执行任何卖出/资金变更前拒绝 Paper 状态推进。保留原有 paper
account 初始资金、交易成本、Top20/Drop2、signal 日期契约及
2026-10-12 forward inception，不改历史结果。

买入成交报告新增可选（向后兼容）取证字段 factor、physical_shares。
审计器独立核算 **拟买金额应得股数** 而不仅验证成交单位合法，避免
例如 factor=2、固定 100 复权股数也刚好构成整手却产生错误权重的漏报。

本次证明的是代码口径和合成用例的确定性一致；实际生产场景仍需在
正式 inception 后用可追溯独立行情对账。不将 CI PASS 等同于实盘式
Paper NAV 全面认证，且不自动部署 Modal app。
