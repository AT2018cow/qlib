# 纠错后策略复验与 10/08 起模拟回填证据

主报告见[研究结果](../../../docs/07-code-review/2026-11-06-prelive-revalidation-results.md)，使用合同与正式部署边界见[具体审核包](../../../docs/07-code-review/2026-11-06-limited-use-review-package.md)。全部结果是历史参考或隔离模拟，不是券商成交、实盘批准或 120 日研究晋级。

## 内容

| 文件或目录 | 用途 |
|---|---|
| `primary/` | 10 候选 × 0/5/10bp，2018–2024 的 30 个场景 |
| `extension/` | 6 配置 × 3 成本，连续到 2026-09-30 的 18 个场景 |
| `phases/` | CSI1000 两配置 × 4 相位 × 3 成本，24 个场景 |
| `completed-scenarios.json` | 24 个已完成任务、原始 receipt 和文件哈希验证，合计 72 场景 |
| `final-analysis.json` | 正式排序、逐项条件、尾段指标和三市场均无合格替换者的结论 |
| `withdrawn-analysis/` | 已撤回的中间汇总：误用全段 Sharpe 检查尾段，不能用于决策 |
| `extension-prefix-parity.json` | 全部 18 个扩展成本场景的旧窗口前缀与主窗口一致 |
| `history-compaction-full-parity.json` | 6 个完整历史场景，账本日志精简前后每日净值与逐笔记录一致 |
| `provider-file-hashes-v1.json` | 长历史行情所用 12,556 个文件的原始 SHA |
| `benchmark-evidence-amendment.json`、`csindex*` | CSI1000 2025-04-16 基准修补的独立版本与官方原始响应 |
| `october/signals/` | 三市场 2026-09-30–2026-11-04 新重建预测，绑定训练 receipt SHA |
| `october/accounts/` | 四个独立 1M 近期模拟账户，逐日净值、逐笔订单、完整账本及曲线 |
| `october_corrected_20261106_a2_training_results.json` | 三市场六个历史截止日训练区块、配置、数据和分数 SHA |
| `october-training-repeatability.json` | CSI300 独立重训与首日/整段预测完全一致 |
| `october-independent-verification.json` | 不调用 Qlib 数据接口或共享估值实现的原始二进制行情/费用/NAV 复算 |
| `recent-csindex-*`、`recent-benchmark-source-receipts.json` | 近期三条基准的官方响应、来源 URL 与 SHA |
| `limited-review/` | 三市场人工使用参数、显式取消两项风险上限、目标日待验收状态 |
| `delivery-source-manifest.json` | 本次交付代码指纹；`source-manifest.json` 是长历史执行时的冻结记录 |
| `cloud-attempts.json`、`local-attempts.json`、`logs/` | 成功/失败尝试、真实退出状态、脱敏 CLI 输出及原日志 SHA |
| `cloud-final-state.json` | 最终快照中可见的本任务 7 个应用均停止且 0 tasks；更早未列出的应用单独披露 |
| `scoped-cloud-billing.json` | 仅本任务明确 app ID 的已结算小时；不含未闭合小时，不能当总费用 |
| `SHA256SUMS.json` | 本目录保存文件的指纹；清单本身除外 |

各长历史任务的 `receipt.json` 保存**执行时原始文件** SHA。`sessions.jsonl` 以 gzip 压缩保存；解压后的 SHA 必须等于原 receipt 对应 `sessions.jsonl` 的值，压缩文件自身 SHA 则见总清单。24 个任务的其他原文件均已逐一核对后复制，不改写失败任务为成功。输入行情、完整历史预测包较大，未重复纳入此目录；冻结 SHA 与所用文件清单保留，原输入留在操作环境。近期三份新预测已随证据归档。

完整日账本披露历史报价缺口和参考承接，不能将其认证为停牌。现有数据无法独立证明历史 ST、开盘承接或全部历史行情修订可得性，因此不宣称完全可成交。近期训练按历史模型截止日进行，但使用本次行情版本，不能当作当年实际实时记录。

## 复核

从仓库根目录使用项目 Python 环境执行：

```bash
python -m pytest -q quant/tests
python quant/code/research/regime_reselection/analyze_prelive_results.py \
  --results "$REPLAY_CONTROL_ROOT" \
  --register quant/docs/07-code-review/2026-11-06-prelive-execution-register.json \
  --output "$NEW_ANALYSIS_PATH"
```

`REPLAY_CONTROL_ROOT` 是含原有不可变批次目录与 `*_results.json` 的运行控制根目录，不能仅指向本归档目录。汇总器按登记的 engine/register SHA 选取完成任务，核对扩展报告 SHA，单独计算主窗口结束后的尾段，禁止用全段优势掩盖尾段反转。重放命令、输入哈希与资源见两份登记文件和工具的 `--help`。

近期云训练入口为 `october_backfill_modal.py`：在授权本地 profile 下先运行 `--mode smoke`，通过后使用唯一 run ID 运行 `--mode full`。使用生产镜像、每市场 20 CPU/32 GiB、最多三市场并行；按登记预留最坏 90 分钟的持续前台会话，保持附着到真正退出。实际短时结果不能代替下次运行的超时预算。禁止使用 Modal 后台分离参数、shell 后台、`nohup` 或启动确认冒充任务完成。

下载三市场小型预测后，用 `run_october_replay.py` 指向同一公开发行包的本地安全解压目录、资产身份文件、训练 receipt 和两份登记文件，将 `--out` 设置为新目录。工具不会覆盖既有正式账户、切换 canonical 或发送实盘订单。

Modal 日志中的本地路径与授权 workspace URL 段已脱敏，原始日志 SHA 在 `logs/redaction-receipt.json` 中保留。此目录不包含凭证、私有 workspace/profile 名称或券商资料。
