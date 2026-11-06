# 统一每日调度验收证据

用户批准 07:00 主跑、08:00 补跑并合并网站收益更新；网站应用保留为无独立定时器的工作函数。

`source-manifest.json` 绑定共享实现，`execution-register.json` 记录启动前固定行情和资源；`pipeline-smoke-final.json` 验证容器实际导入及部分失败恢复，`return-parity.json` 验证三个市场全部 60 个净值点与此前正确账本完全一致。

`daily_cron-verification.json`、`daily_cron_second_chance-verification.json` 是实际部署函数的手动调用结果；自然定时触发需下一工作日单独验收。`schedule-verification.json` 记录云端调度，`after-state-check.json` 核验原账户完整状态未改变。

最初的隔离协调 smoke 因测试脚本启动路径错误被主动停止，修正脚本后通过；详见 `pipeline-smoke-initial.log` 与最终 `pipeline-smoke-final.log`。生产实现与模型核算未因此改变。所有研究调用均使用有界前台持久会话。
