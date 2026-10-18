# R02 修复上线执行记录（2026-10-18，B06 修复后的五项操作员任务）

> 执行对象：`fix/quant-extension-review-20261018`（PR #83），锁定提交 `9ae29e75813d6868712a0b7aaf34aa0acbbc0622`
> 执行日期：2026-10-18（Asia/Singapore）
> 关联文档：`2026-10-18-r02-serialization-and-bootstrap-runbook.md`（操作手册）、`2026-10-18-quant-extension-review-and-fix-plan.md`（原始审查）
> 范围：仅自定义扩展层与自定义 workflow；未改动微软上游 Qlib、冻结策略参数或已发布历史工件。
> 结论：五项任务完成。生产 Modal app 已更新到修复版本 v19（同一工作区仅此一个 app），三市场账户已完成一次性锚定，负向/正向冒烟与 Volume 受控演练通过，真实行情包哈希固定回归通过。**PR 仍未合并**（等待明确授权）。

## 1. 写者与平台配置清查（任务 1）

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 工作区部署的 app 数量 | **1 个**（`qlib-cn-daily-a10g`，状态 deployed，运行任务 0），无其它已部署写入者 | `modal app list` |
| GitHub 推送凭据 | secret `github-push` 存在，最后一次使用 2026-10-16 07:00 +08（上次生产发布） | `modal secret list` |
| 旧版本 → 新版本 | v18（`fa4c846`，2026-10-17 17:52）→ **v19（`9ae29e7`，2026-10-18 13:34 +08）** | `modal app history` / `app info` |
| 调度保留 | `daily_cron` = `0 7 * * 1-5` Asia/Shanghai；`daily_cron_second_chance` = `45 7 * * 1-5`（仍仅转发 `daily_cron.remote()`） | `modal app info --json` |
| **平台有效并发限制** | `Function.update_autoscaler()` 读取部署后**实际生效**配置：`max_containers=1`；随后显式 pin `max_containers=1` 并回读一致（`PLATFORM_MAX_CONTAINERS_OK`） | 平台 API 调用输出 |
| 其它并发途径 | 工作区 Volume 按工作区隔离；第二调度入口自身无独立发布逻辑；未使用任何 `--detach` | 源码 + 部署清单 |

说明：平台读回的 `max_containers=1` 来自本次部署的静态装饰器，**不是**仅凭源码断言；同时已用 `update_autoscaler(max_containers=1)` 在平台侧显式固化该上限（注意：下一次 App 重新部署会把平台 override 重置为装饰器静态值，二者当前一致）。

## 2. 现存账户审阅与一次性锚定（任务 2）

### 2.1 锚定前的只读审阅

只读下载三份 canonical 状态文件（`modal volume get`，不写 Volume），与 2026-10-16 已发布工件 `quant/data/signals/2026-10-16_paper_portfolio*.json` 逐字段比对：

| 市场 | cash(round2) | positions | 待执行单(日期/买卖) | paper_lineage | 结论 |
| --- | --- | --- | --- | --- | --- |
| csi1000 | 一致 | 一致 | 一致 | 一致 | **MATCH** |
| csi500 | 一致 | 一致 | 一致 | 一致 | **MATCH** |
| csi300 | 一致 | 一致 | 一致 | 一致 | **MATCH** |

锚定哈希（SHA256，与审阅对象逐一对应）：

```
csi1000  7c8d54d57806c54960e8a6f66a5fb70d03e380cf4fef6bbb4f979c10da092e74
csi500   01a7f0e47333ba57dd97dfc8e7e675bd4f48e7fc7f0a4bbf99e0c0132f37d5a0
csi300   e415c20d5674214c44c4800d6adb21fcdb36d37ab706db6b337028c4987f2081
```

（同目录另存在历史/变体文件——`_bak-20261017` 备份、单下划线旧命名、`stage_b_winner_canonical`、`csi300__c98856b4_...` 等——均不是当前 canonical 路径，新流程不会读取，未做任何改动。）

### 2.2 锚定前负向冒烟（生产门禁的失败关闭验证）

在**未锚定**状态下直接运行部署后的 `daily_cron`：

```
publication_journal.JournalCorrupt: BLOCKED_MANUAL_RECOVERY: unanchored existing paper state;
run the approved bootstrap attestation before trading on it:
paper_portfolio/csi1000__c98856b4_top10_drop1_canonical.json
```

退出码 = 1；失败发生在加载/训练/发布任何纸面交易之前（调用栈停在 `check_paper_state_entry`），未产生任何副作用。

### 2.3 一次性锚定执行

```
modal run quant/code/production/modal_qlib_cn_a10g.py::attest_paper_state_bootstrap \
  --approve --note "2026-10-18 pre-deploy review: SHA256 verified against published 2026-10-16 ledger artifacts (cash/positions/pending/lineage match)"
```

输出（与 2.1 复核哈希完全一致）：

```
[attest] anchor paper_portfolio/csi1000__c98856b4_top10_drop1_canonical.json sha256=7c8d54d5...
[attest] anchor paper_portfolio/csi300__59b93929_top10_drop1_canonical.json  sha256=e415c20d...
[attest] anchor paper_portfolio/csi500__raw10d_c98856b4_top10_drop1_canonical.json sha256=01a7f0e4...
```

锚点文件回读（`publication_journal/bootstrap_anchors.json`，updated_at 2026-10-18T05:53:23Z）：三条 anchor 与此前记录一致，note 记录了审阅依据。

## 3. 受控冒烟（任务 3）

### 3.1 正向冒烟（锚定后，真实生产环境）

`modal run ...::daily_cron`（2026-10-18 为周日）：

```
[cron] 2026-10-18 非 A 股交易日（假日），gate 前移零成本跳过；最近排名见最近一份已发布信号
```

退出码 = 0。该路径证明：日历获取 → Volume reload → `verify_chain_integrity()`（空链通过）→ `check_paper_state_entry()`（三市场锚点一致）→ 假日跳过；未发布任何内容。

### 3.2 Volume 层受控演练（真实 Volume 的 scratch 前缀，已清理）

在真实 Volume 的隔离前缀 `/vol/publication_journal_smoke_20261018`（**不触碰生产账户路径、不推送 GitHub**）演练：冻结 → 标记 GITHUB_PUBLISHED → 模拟崩溃 → 新视图仅凭冻结载荷恢复 → 提交 → 篡改探针：

```python
{'stage': 'VOLUME_COMMITTED', 'recovered_missing_before_write': True,
 'live_before_state_write': None, 'live_after_commit': '{"version": 2, "cash": 1.0}\n',
 'committed_prefix_ok': True, 'tamper_blocked': True, 'scratch_removed': True}
```

说明：真实 GitHub 推送路径**故意未演练**——向不可变历史写入测试工件不可接受；该路径由 CI 故障注入（挂起/丢回包/中途断点恢复）覆盖。

## 4. 真实行情包哈希固定回归（任务 4）

```
qlib_bin_2026-10-15.tar.gz  (release tag 2026-10-15, 568,113,367 bytes)
sha256 = efb13fc72f6295932413cbfb1814198a4ac6f8c077538d899037b20a23c04ed4
        （与 quant/data/signals/2026-10-16_mvp_metadata.json 记录的
          provider_identity.archive_sha256 完全一致）

QLIB_PROVIDER_ARCHIVE=/tmp/.../qlib_bin_2026-10-15.tar.gz \
  python -m pytest -q quant/tests/test_provider_archive_real.py
→ 1 passed in 18.10s
```

该包同时被完整安全解压验证：67,860 个成员全部通过（无越界路径/链接/重复成员），唯一根 `qlib_bin/`，`calendars/day.txt` 至 2026-10-15，`features/` 存在。

## 5. 合并与部署分离（任务 5）

| 动作 | 状态 | 说明 |
| --- | --- | --- |
| Modal 生产部署 | **已执行**（为满足次日发布要求） | v19 自分支提交 `9ae29e75` 部署，43.8s；调度与 secret 保留；平台并发已核验/固化 |
| PR #83 合并 | **未执行** | 等待明确授权；`main` 保持 `bfd5e63a` |
| 自动发布风险 | 无新增 | 部署后的定时任务即原有生产 cron；未新增任何调度 |

一致性提示：当前**线上代码 = 分支 `9ae29e75`**。若随后批准合并，建议合并后从 `main` 再执行一次 `modal deploy`，使「线上版本 ↔ main」完全一致（本次分支与合并后的 main 差异预计仅为文档）。

## 6. 次日（2026-10-19，周一）运行预期与监控

1. **07:00 `daily_cron`**：日历确认交易日 → 链/锚点门禁通过（空链 + 三锚点）→ 三池 `daily_standalone` 拉取同日数据包（lag=0、三池 archive/calendar SHA 一致）→ 生成 paper 提案（结算 10-16 挂单并生成 10-19 新挂单）→ journal 冻结 → GitHub 推送 → Volume 状态提交（VOLUME_COMMITTED）。
2. **07:45 补跑**：若首跑失败/超时，转发调用在 `max_containers=1` 队列中等待并幂等补完；若已提交则快速路径仅校验后返回。
3. **预期关键日志行**：`multi-market canonical freshness PASS`、`journal ...: stage=PREPARED`、`✅ 已推送 … canonical 文件`、`✅ paper state 已按冻结载荷提交`、`✅ … 三池 canonical 已按 journal 发布并提交`。
4. **失败关闭项与处置**：数据包漂移（"market snapshots disagree"/"lag=0"）→ 等待数据源稳定后由 07:45 补跑或人工 `modal run ...::daily_cron` 重跑；链/锚点/状态不符（`BLOCKED_MANUAL_RECOVERY`）→ 按运行手册第 2 节处置，禁止绕过。
5. **网站**：信号提交推送到 `main` 后，`website-deploy` workflow 自动构建并发布（GitHub Pages + Cloudflare Pages 镜像），无需人工步骤。

## 8. 补充验证（2026-10-18 下午追加，直接针对次日发布风险）

在完成上述五项任务后，又对"次日使用的新代码路径"做了两项额外验证：

### 8.1 当前最新行情包与严格解压器兼容性

下载当日最新 release（2026-10-18）：

```
qlib_bin.tar.gz (release tag 2026-10-18)  568,280,701 bytes
sha256 = a11eaba954f2d5658c017fa800eea85bb6277f8ffc3d7b0449f791a44e43e7dd
```

以 `provider_archive.validate_archive_members` + `safe_extract_provider_archive` 验证：

```
members validated: 67871 in 5.8s
extract root: .../qlib_bin
calendar: 2000-01-04 -> 2026-10-16 (6489 sessions)
features: True
```

结论：新增的严格成员规则（无重复成员/链接/越界路径）与当前最新发布兼容；次日数据日为 **2026-10-16（周五）**，满足三池 lag=0 门禁。

### 8.2 生产容器端到端冒烟（不发布）

以生产参数运行一次 `daily_standalone`（仅返回结果，不推送 GitHub、不写账户状态）：

```
modal run quant/code/production/modal_qlib_cn_a10g.py::daily_standalone \
  --topk 10 --nd 1 --market csi1000 \
  --csi1000-profile c98856b4_top10_drop1 \
  --paper-lineage c98856b4_top10_drop1_canonical
```

关键输出与结果（exit 0）：

```
[daily] 下载最新数据 https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz ...
[daily] 数据就绪：日历 2000-01-04 ~ 2026-10-16（6489 个交易日），archive=a11eaba954f2
[daily] CSI1000 PIT universe PASS: 1000 members asof=2026-10-16
[daily] 复用模型，训练日期=2026-10-16，距今未满 20 交易日
[daily] 20-session label maturity PASS: train_end=2025-07-29 valid_end=2026-09-09 fit_asof=2026-10-16
[daily] 2026-10-16 top10（csi1000）…（10 行排名输出）
```

证明：生产镜像内的新安全解压器可直接消费当日最新包；PIT/标签成熟度/live 模型缓存路径工作正常。该冒烟不改变持久状态（模型缓存属既有机制）。

## 9. 本轮执行后的状态总览

| 项目 | 状态 |
| --- | --- |
| 生产 Modal app | v19（`9ae29e75`），调度 07:00/07:45 保留，单 app 单工作区 |
| 平台并发 | `max_containers=1` 已读回并显式固化（`daily_cron`） |
| 三市场 bootstrap 锚点 | 已写入并与 2026-10-16 已发布账本核对一致 |
| 负向冒烟（未锚定） | fail-closed，无副作用 |
| 正向冒烟（锚定后） | 门禁全通过，假日跳过 exit 0 |
| Volume 受控演练 | 冻结→崩溃→冻结载荷恢复→篡改拒绝，scratch 已清理 |
| 真实包回归（10-15） | 1 passed（18.10s，哈希与生产元数据一致） |
| 最新包兼容（10-18） | 67,871 成员验证 + 完整安全解压通过 |
| 端到端 standalone 冒烟 | exit 0，archive=a11eaba954f2，排名正常 |
| PR #83 | 仍为 OPEN，未合并（等待明确授权） |
| main | 保持 `bfd5e63a`（未合并） |

## 10. 合并后记（2026-10-18 14:08–14:12 +08）

- **PR #83 已合并**：merge commit `9351e5abd270cf96b2e81071d619e38afd52ce42`。（合并时 `main` 已包含操作员的网站发布调整提交 `1bd8c497`，无冲突。）
- **从 `main` 重新部署**：v20（14:09 +08，49.4s），部署提交标记 `9351e5a`；`daily_cron` 调度 `0 7 * * 1-5`、补跑 `45 7 * * 1-5` 保留；平台 `update_autoscaler()` 回读 `max_containers=1`。
- **v20 生产冒烟**：假日路径（链+锚点门禁）全通过，exit 0。
- **合并触发的 main CI 全部 success**：Quant extension checks、CSI1000 MVP publication contract、CSI1000 Stage-B 独立负控制、Deploy signal website（网站构建/发布流程）。
- 至此「线上运行版本 = `main` = 复审通过的修复」完全对齐；次日 07:00 发布路径与本文第 6 节预期一致。

