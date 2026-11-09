# ST 风险快照备份源（2026-11-09）

状态：`IMPLEMENTED / VERIFIED`

## 1. 背景

2026-11-09 19:00 T-close 运行时，东方财富 `push2` 对当前 IP 临时限流：

```text
[risk] ST snapshot attempt=1/2 TRANSPORT: Push2RateLimitError ...
[risk] ST snapshot attempt=2/2 TRANSPORT: Push2RateLimitError ...
```

当时按 `TRANSPORT` 语义降级为 `ST_IGNORED_REFERENCE_ONLY`，没有阻塞信号发布，但 ST 风险边界变成 reference-only。

## 2. 双源设计

优先级：

1. **主源**：`akshare.stock_zh_a_st_em()`，东方财富风险警示板。
2. **备源**：`akshare.stock_zh_a_spot_tx()`，腾讯全 A 股列表，按名称含 `ST` 过滤。

两者都做严格校验：

- 非空；
- 代码必须是合法 6 位数字（兼容 `sh600000` / `600000` / `600000.0`）；
- 备源必须至少包含一条 ST 记录；
- 任一源返回 malformed / 空数据 → `INVALID_RESPONSE`，保持 fail-closed；
- 仅当所有源都是传输故障 → `TRANSPORT`，才允许 reference-only fallback。

真实网络验证（2026-11-09 22:02）：

- 东财连续两次 `TRANSPORT`；
- 腾讯备源成功，197 只 ST；
- `SH600079`（ST人福）、`SH688622`（*ST禾信）均正确命中；
- `SH600000`（非 ST）不误报。

## 3. 契约调整

`csi1000_mvp_contract.VERIFIED_ST_SOURCES` 白名单新增：

```python
("akshare.stock_zh_a_st_em", "akshare.stock_zh_a_spot_tx")
```

`validate_verified_st_snapshot` 现在接受两个合法时间窗：

| 时间窗 | 约束 |
|---|---|
| 执行日 pre-open | `fetched_at_cst` 在执行日 09:30 之前 |
| T-close -> T+1 pre-open | `fetched_at_cst` 在信号数据日 15:00 之后，且 `signal_data_date` 早于执行日 |

T-close 快照会冻结 `signal_data_date`，供 `build_mvp_metadata` 和 `paper_execution_context` 在结算时复核；不满足任一窗口或 `signal_data_date >= execution_date` 一律拒绝。

## 4. 测试

- `test_cron_gate.py`：
  - 东财 TRANSPORT → 腾讯备源成功；
  - 东财 INVALID → 腾讯备源成功；
  - 腾讯备源无 ST 行 → `INVALID_RESPONSE`；
  - 两个源都 TRANSPORT → `TRANSPORT`。
- `test_csi1000_mvp_contract.py`：
  - 备源 + T-close 18:30 快照通过；
  - 14:59、执行日 09:30、错误日期、`signal_data_date >= execution_date` 均拒绝；
  - 原有 pre-open 快照行为不变。

完整 quant 测试：`935 passed, 1 skipped`。

## 5. 部署注意

该改动会改变 `source_manifest("training")`，主应用与网站应用必须同时重新部署，保持 source identity 一致。部署后的第一次 07:00/18:00 网站阶段会为新 source 生成一次新的 receipt/replay；之后恢复幂等短路。
