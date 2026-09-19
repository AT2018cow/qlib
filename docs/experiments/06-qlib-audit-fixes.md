# Qlib 审计修复：实现状态与验收条件

本分支基于 `main` 的 `f0f770965b0ca21522b9d0df65e3130912523d85`。**两个源文件已在本分支实际修改；不可在这个分支再次运行 `--apply`。** 请先审核代码、在真实数据上测试后再考虑合并或部署。

## 检查已修复的分支

```bash
PYTHONPATH=. python -m unittest discover -s tests -p 'test_qlib_audit*.py' -v
python -m py_compile modal_qlib_cn_a10g.py scripts/check_data_health.py
git diff main...HEAD --check
git diff main...HEAD -- modal_qlib_cn_a10g.py scripts/check_data_health.py
```

2026-09-19 的临时 GitHub Actions [运行记录](https://github.com/AT2018cow/qlib/actions/runs/35410619320) 已在真实仓库 checkout 中验证：9 项单元/结构测试通过、补丁全部锚点匹配、修改后的两个 Python 文件编译及差异检查通过，随后提交源码变更。写权限临时工作流已从最终分支删除。

`apply_qlib_audit_fixes.py` 留在仓库中用于复现从**未修改的上述基线**打补丁：仅在原始 checkout 执行 `--check`，通过后才能执行 `--apply`。补丁会创建本地 `.audit-prepatch.bak` 备份，不得将备份提交到 Git；重复应用将拒绝执行。

## 已修改代码的范围

- 对近期训练/验证/测试和 Batch C、P2 滚动窗口应用按交易日历计算的标签到期日 purge。训练标签必须在验证期开始前可观察，验证标签必须在测试期开始前可观察，LightGBM 早停不得读取使用测试期收盘价构建的验证标签。
- Tune 选择只查看验证期 Rank IC，不再直接利用测试期评价选择超参数；但旧的历史测试数据已被使用过，不能据此消除此前的选择偏差。
- 修复 P2 未定义窗口、数据健康检查忽略缺失值/退出码、`debug_data` 的局部 pandas 导入、版本比较键名错误、P1 简化加权模拟的索引/换手与非超额指标命名。
- 修复增强模式与显式股票池、滚动 topk/n_drop、多个未来标签组合，以及增强冒烟模式中的早停优先级。
- 每日 CSV 明确只表示排名，**不是**带有持仓与成交约束的可执行交易单；cron 数据滞后或推送失败时显式失败，且不覆盖已有同日信号。休市日可能因严格当天检查而报警，接入权威交易日历后再优化调度。

## 尚未完成的项目

1. 从账户持仓、现金、冻结股和交易日可成交状态推导完整调仓与实际交易单，复现 TopkDropoutStrategy 的 `n_drop`；目前排名不可直接执行。
2. 真实 A 股历史涨跌停制度、停牌、开盘/收盘差价、容量与滑点、IPO/ST 处理，以及数据集的 point-in-time 财务披露核验。
3. 嵌套 walk-forward 与真正未使用过的样本外数据。既往 +7.5% 统计结果不能简单沿用；须重跑 Batch C 和 P2，保存逐日收益、费用、数据 release 与 commit SHA。
4. Batch C 年化收益与滚动跨窗口连续持仓口径的精确重算，以及数据包原子发布。

单元测试仅验证纯函数和结构补丁；不能取代 Modal 数据卷上的真实训练、回测、成交对账。用户审核前不要自动合并 PR。
