# 网站三市场累计收益修正证据

范围：三个独立 100 万元模拟账户，自 2026-10-08 开盘首次建仓，使用上一交易日收盘评分；全部收益属于因果历史重建参考。

- `source-manifest.json`、`deployment-source-manifest.json`、`implementation.json`：训练共享实现、网站代码与实际部署提交的身份。
- `execution-register.json`：启动前登记。`source_commit` 表示本轮基线提交，未提交的实施改动由完整源码清单精确绑定；最终提交见 `implementation.json`。
- `provider-release.json`：公开行情包的固定下载地址、大小和摘要。
- `backup-manifest.json`、`durable-backup.json`、`backup-and-drill.json`：86 个生产文件的不可覆盖备份及真实交易日五种中断恢复演练。原状态未覆盖。
- `accounts/`、`smoke/`：最终身份的预测、输入文件摘要、完整日账本、逐笔订单、指标和独立账户状态。
- `independent-account-verification.json`：直接读取原始 float32 行情文件，独立复算现金、股数、费用与净值；每日误差小于半分钱。
- `corrected-replay-parity.json`：与此前已纠正的研究回放逐日对比，收盘净值、现金和股数完全一致；非金额浮点差异低于 1e-12。
- `transfer-manifest.json`、`production-transfer.json`：最终 6 个模型、49 个文件的环境间复制及回读 SHA 验证。
- `final-account-audit.json`：包括本轮新报告在内的 515 份报告，本金、指标、数量、费用压力无错误。
- `logs/`：真实测试、云端 smoke/full、恢复演练和部署日志；个人路径与私有 workspace 标识已脱敏。

当前网站的 `*_website_performance.json`、`*_forward_performance.json`、`*_backfill_performance.json` 三组文件均使用纠正后的同一系列；不会继续将旧前缀与不同 bootstrap 的账户拼接。版本快照位于 `quant/data/website_simulation/`。后续上线验收、浏览器结果与校验清单在实际执行后追加。
