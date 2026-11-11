# 唯一自建行情管道：代码审查与整改交接

> 日期：2026-11-11
>
> 审查基线：`41b1c96bd`；比较范围：`83f710a76..41b1c96bd`。行号对应审查基线，后续以函数名定位。
>
> 结论：**暂不通过验收。先整改，再验证首次真实新日期构建。**
>
> 本文记录问题、复现、修复边界与验收要求，不代表已经修复。本次只新增文档，不修改生产代码、数据或部署。

## 1. 实施上下文与约束

已批准架构不变：

```text
冻结历史锚点
  → 单一自建 provider 日更
  → 内容寻址 archive + PASS receipt
  → 本地 materialize
  → 三市场信号 + 网站增量账户
```

不要因整改恢复 chenditc-first、增加双源账户 lineage、合并生产容器或开展镜像瘦身。架构方向正确，本轮修复具体实现与测试覆盖。

先阅读：

1. 仓库 `AGENTS.md`。
2. `quant/docs/07-code-review/2026-11-10-overlay-primary-implementation-handoff.md`：已批准的完整实施方案。
3. `quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md`：实施与验收记录。
4. 本文。

实施报告最后记录两个生产 App 为 v18。这不是新会话的实时状态；整改前重新检查 Git、用户未提交修改、实际部署及活动任务，保护其他会话的工作。

### 审查验证范围

- 本地完整测试：**983 passed、2 skipped、50 subtests passed**。
- 31 个变更 Python 文件的 Black 120 列检查通过。
- 另用本地、无网络、无生产写入的最小复现或过滤规则检查确认以下问题。
- 审查未启动新的 Modal 实验，未发布信号，未修改 journal、provider 或部署。

现有测试遗漏了真实 archive 输入、builder 异常分支、停牌/复权边界和进程生命周期，不能以测试总数代替验收。

### 临时复现产物

同一环境可能仍有：

- `/tmp/opencode/overlay_primary_review_repro.py`
- `/tmp/opencode/overlay_primary_review_repro.log`
- `/tmp/opencode/overlay_review_pytest.log`

临时文件不是长期交付依赖；下文已记录重建测试需要的场景。应转为 `quant/tests/` 正式回归测试，不把私人路径、环境信息或真实凭据复制进仓库。

## 2. 整改清单

| 编号 | 优先级 | 问题 |
|---|---|---|
| R01 | P1 | CSIndex 日期滞后的 builder 恢复分支删除不存在目录，必然失败 |
| R02 | P1 | archive asset 没有 local_path，被网站增量入口直接拒绝 |
| R03 | P1 | 无有效 base 行被误判为没有历史，导致改写历史或停牌阻断 |
| R04 | P1 | hfq 因子先 reindex 后 ffill，丢失有效因子，与原语义不等价 |
| R05 | P1 | raw 日期齐全但 hfq 无效的缓存被当成完整，重试不补拉 |
| R06 | P1 | 多日补数只校验最后一天成分，中间交易日缺成分仍 PASS |
| R07 | P1 | 新探针镜像未排除 .env、私钥等敏感文件 |
| R08 | P2 | materialize 缓存命中路径写错，重复读取 archive 和解包 |
| R09 | P2 | 子进程退出后 CPU 累计丢失，可出现负 CPU seconds/平均核数 |
| R10 | P2 | 网站 worker 成功/缓存命中后未停止采样线程 |

P1 必须在验收前关闭。R08–R10 关系到成本目标和资源证据，也应修复；R07 应在再次构建探针镜像前解决。

建议先修数据正确性 R03–R06，再修 builder/消费者 R01–R02、R08，最后完善观测 R09–R10；安全 R07 可先单独提交。可调整顺序，但不要每修一行就部署、付费重跑和全量预热。

## 3. R01：CSIndex 日期滞后的恢复分支必然报错

**位置**：`quant/code/production/provider_build.py:156–171`，`build_provider_archive()`。

### 触发条件与证据

`pipeline.build()` 在成分日期检查时抛出 `PipelineError("... constituent date ... != asof ...")`。尚未调用 `build_overlay()`，因此 `out_local` 不存在。异常分支却直接调用 `shutil.rmtree(out_local)`，导致：

```text
CSIndex snapshot ... lags ... by 1d; retrying ...
FileNotFoundError: <isolated-workdir>/work/out
```

第二次 `allow_base_membership=True` 构建根本没有发生。官方文件滞后一天是常见路径，不是罕见边界。

### 修复边界

- 只清理存在且属于本次运行的 staging。
- 清理不能掩盖原始 membership 错误。
- 保留单日、有限滞后、成员相等的证据约束，不能放宽 PIT gate 换取成功。

### 必须补的测试

- 从 **`build_provider_archive()`** 进入，而不只直接调用 pipeline 的允许延用分支。
- `out_local` 起初不存在，单日成分滞后且成员相等：进入并完成合法的第二次构建。
- 成员不同、超出滞后政策：明确 fail-closed，不被 cleanup 错误替换。
- 部分 staging 已存在时能正确恢复。

## 4. R02：archive 输入无法进入网站增量

**位置**：`quant/code/ops/website_simulation_modal.py`：

- `_latest_overlay_asset()`：约 69–84 行。
- `_incremental_prior()`：343–344 行。
- `_build_market_impl()`：约 605、648 行。
- `daily()`：约 1044–1059 行。

### 根因与影响

生产 archive asset 包含 `archive_path`、digest、calendar 和 base，但没有 `local_path`：

```python
{
    "kind": "archive",
    "archive_path": "...tar.gz",
    "digest": "sha256:...",
    "calendar": [...],
    "base": {"digest": "...", "calendar_end": "..."},
    "source": "overlay",
}
```

增量入口仍要求 `asset.get("local_path")`，所以 coordinator 和 worker 都返回无候选。worker 解包后也没有更新 asset。

**正常日实际会 smoke + 三市场 full replay，而不是增量。**

### 修复边界

- 用已验证的 archive receipt/base/calendar 支持轻量候选查询，不要求 coordinator 解包整份数据。
- 区分候选身份检查和 worker 本地物化，不通过伪造 local_path 绕过校验。
- 保留父 digest、上一交易日、同源、模型缓存、ledger 校验。
- 缺少或不兼容候选仍按合同 full；未知异常不能无差别吞掉。

### 必须补的测试

- 使用 `_latest_overlay_asset()` **实际生成**的 archive 字典，不手工补 local_path。
- 同源、父 digest 匹配、前缀完整：跳过 smoke，`replay_mode=INCREMENTAL`，仅执行新 session。
- 父 digest 不匹配、缺前缀、换源：拒绝拼接，受控全量。
- 固定输入下比较 NAV、cash、fees、positions、orders 与完整路径一致。

## 5. R03：无有效 base 行不等于没有历史

**位置**：

- `quant/code/data_ingest/pipeline.py:437–438,512–522`。
- `quant/code/data_ingest/history.py`，`build_backfill()`。
- `quant/code/data_ingest/overlay.py:302–312`，`write_feature_history()`。
- `quant/code/data_ingest/gate.py:123–138`。

### 根因与复现

`read_base_features(..., base_end)` 返回该日有有限 close/adjclose/factor 的股票，而非“有历史文件的股票”。它的补集 `missing_base` 被全部送去新成分历史回填。

已有股票只因 base 日停牌、退指后未追加或某行缺数，也会重新构造历史，使用 `tofile()` 覆盖继承进新 provider 的历史 bins。由于该股票不在 `base_features`，gate 又跳过历史比较。

两种复现均已确认：

```text
FROZEN_HISTORY_REWRITE: gate=PASS，已有历史值被改写
CONFIRMED_SUSPENSION: PipelineError ... reconstructed target rows have no finite close
```

第二种是有历史且当前确认停牌，却因回填要求 target 有有限 close 而阻断整份 provider。

### 修复边界

- 分清从未有历史、已有历史但 base 行缺失、已确认停牌、无证据缺数。
- 仅对真正缺少所需历史的对象受控回填，不能自动重建已有冻结前缀。
- 已有历史使用合法、可追溯续接锚点；不足以安全续接则明确失败，不能整段重新归一化。
- 停牌使用日期化证据，不伪造 OHLCV。provider 合法停牌不等于账户可以无证据估值/成交。
- 不用全局打开 `allow_missing_base_suspensions=True` 代替正确分类。
- 历史保护不能因为最新行缺失而被跳过。

### 必须补的测试

- 既有股票 base 行 NaN、目标日复牌：历史文件前缀保持，坐标续接正确。
- 既有成分持续停牌且证据完整：不触发历史重建，分别遵守 provider/账户合同。
- 无证据缺数：fail-closed。
- 真正新成分：受控回填，锚点、单位、来源可追溯。
- 在测试中保护全部既有股票文件前缀，不以 30 只样本替代历史不变性验证。

## 6. R04：hfq 因子对齐不等价

**位置**：`quant/code/data_ingest/sina.py:156–169`，`_hfq_frame()`；同时审查 `_public_pair()`。

### 根因与复现

先 `factor.reindex(full.index)` 会丢弃不在行情索引中的因子记录，之后 ffill 无法恢复。原 AkShare outer merge + forward fill 不是这个语义。

```text
行情：11-03 close=10，11-05 close=5（11-04 无行情行）
因子：11-03=1，11-04=2
期望 11-05 hfq close=10；实际=5
```

因子最后一次记录早于行情窗口时，另一个复现返回 0 行，而期望窗口内有有效 hfq。public fallback 的 raw 已按短区间截取，更需覆盖这一情况。

### 修复边界

- 保留有效的窗口前因子、无行情日期上的因子变化，正确时间对齐。
- 明确历史首因子、停牌期间变化、非交易日记录、重复日期、空/非有限因子处理。
- 保持价格舍入、单位、缺失值合同。
- 无有效复权证据时，不静默按普通涨跌续接。

### 必须补的测试

- 上述两例纳入普通离线测试。
- fast/public 两条路径都测。
- 包含普通日、除权日、停牌前后、窗口前因子、无效因子。
- 四股普通日期的实测相等只能作为样本证据，不能扩大为全部边界通过。

## 7. R05：无效因子缓存被当作完整

**位置**：`quant/code/data_ingest/pipeline.py:214–231` 与 `quant/code/data_ingest/cache.py:190–209,229–244`。

### 复现与影响

缓存 raw 包含 base/target 日期，但 hfq 缺失。写入后 hfq_close=NaN，读取仍构造 hfq Bar。完整性判断只看 raw 日期集合：

```text
sina cache complete=1 refetch=0
hfq_close_values=[nan, nan]
```

重试不补拉；NaN 还可能绕过复权变化检测，使旧因子被错误延续。

### 修复边界

- 定义 raw/hfq 联合完整性：必需日期、字段、有限性与有效范围。
- 缺因子/坏因子不能 complete，明确补拉或失败策略。
- 区分合法停牌与未就绪/坏响应，不能“有行即完整”。
- 缓存请求合同、base/区间变化时的复用可验证，不只检查 trade_date。
- 对已有可能受影响缓存采用明确失效/重取政策，不自动清空生产数据。

### 必须补的测试

- raw 完整、hfq 空/NaN：补拉或明确失败。
- 修复后再次读缓存，得到有效因子，不被旧行覆盖。
- 空响应、坏价格、缺日期不 complete。
- 合法缓存命中不重复获取正确数据。

## 8. R06：多日补数的中间 PIT 未校验

**位置**：`quant/code/data_ingest/pipeline.py:401–410`、`quant/code/data_ingest/overlay.py:159–164`、`quant/code/data_ingest/gate.py:57–88`。

### 根因与复现

多日拒绝只在“成分文件滞后”分支。官方文件日期等于目标最后一天时，直接使用当前快照扩展整个缺口。

成分写入使旧成员结束于 base，新成员始于 asof；gate 只检查 asof：

```text
base 有 2 个成分，补数区间为 2 个交易日，目标日换入新成分
中间交易日只剩 1 个成分，gate 仍然 PASS
```

这会改变中间日 universe、预测及收益。

### 修复边界

- 每个补入交易日都需足够的历史成分/生效日期证据。
- 缺证据时拒绝自动多日补数；不要求本次顺便建设复杂历史成分服务。
- 逐日缓存或官方有效区间有证据时可据此补齐。
- gate 验证所有补数日，不只最后一天。
- 不用 base 与当前集合相等推定中间从无变化。

### 必须补的测试

- 官方文件为目标日、缺口多日、成分变化：无历史证据必须失败。
- 有逐日证据：每日成分区间正确。
- 不变成分与调整生效边界分别测；保留单日滞后延用合同。

## 9. R07：探针镜像敏感文件过滤回归

**位置**：`quant/code/ops/overlay_primary_probe.py:63–82`，`_probe_image()`。

### 检查结果

镜像加入整个 checkout，但 ignore 对这些模拟文件名均返回 False，即不排除：

```text
.env
.env.local
deploy.key
```

本地若存在这些文件，会进入探针镜像。本次仅检查过滤谓词，**没有读取真实凭据，也未确认实际泄漏**。

### 修复与验收

- 与生产镜像使用一致的敏感文件排除规则，优先复用同一安全过滤实现。
- 覆盖 .env 变体、私钥/证书、凭据目录、包管理认证文件。
- 探针继续只读生产 Volume、只写 probe Volume、无发布 secret。
- 用临时模拟文件测试 `.env`、嵌套 `.env.*`、`.key`、`.pem`、`.ssh` 等被排除，正常业务代码/必要配置仍包含。
- “无 GitHub secret”不能替代本地文件过滤。若核查历史镜像，另行安全审计，不打印或提交真实敏感内容。

## 10. R08：materialize 缓存路径不一致

**位置**：`quant/code/data_ingest/store.py:259–264,281`。

缓存检查 `cache/provider/`，实际解包在 `cache/extract/provider/`。连续两次调用返回路径相同，但读取/解包两次。现有测试只断言路径相等，不能证明命中。

### 修复与验收

- ready marker 记录/解析真实相对根目录，检查身份和完整性。
- 同 digest 第二次调用不再读 Volume archive 或调用 extractor，使用 spy 计数证明。
- marker 损坏、provider 缺失、digest 改变时，按合同安全恢复或拒绝。
- 不把任意残留目录当成 ready。

## 11. R09：子进程退出后 CPU 累计错误

**位置**：`quant/code/ops/resource_sampler.py:97–114,235–238,266–275`。

当前用“存活进程累计 CPU 总和”的首尾差。子进程退出后，已消耗 CPU 从总和消失。

合成 /proc 进程树复现：

```text
cpu_seconds=-0.37
avg_cores=-0.37
```

开始后创建、结束前退出的子进程，也可能使实际消耗被算成近零。cgroup 不可用时，Modal 会依赖此路径。

### 修复边界

- 累计需覆盖进程生命周期和已退出子进程，并考虑 PID 重用。
- cgroup 可用时可采用可靠累计；不可用时明确 fallback 与测量局限。
- 无法完整观测不能伪装成精确零；不能仅用 `max(0, delta)` 掩盖。
- stage 与整体 summary 口径一致。
- RSS 汇总共享页重复计算的限制继续注明，不据此直接推断内存计费。

### 必须补的测试

- 子进程创建、工作、退出；开始时已有子进程在结束前退出。
- PID 重用/中途不可读、cgroup 不可用。
- 不为负、不丢失已观察消耗；不能保证完整时标明下限/不可用。
- 修复后重测资源选择样本。报告中的 0.68 core 等旧数字不能直接支撑进一步缩容。

## 12. R10：成功路径不停止网站采样线程

**位置**：`quant/code/ops/website_simulation_modal.py:523–553`，`build_market()`。

start 后成功直接返回 `_build_market_impl()`，只有异常分支 stop。fake sampler 复现成功结果 COMPLETED，但 `stopped=False`。缓存命中同样发生。

复用容器会积累后台线程、样本列表及 /proc 访问，干扰后续测量。

### 修复与验收

- 成功、缓存命中、异常统一清理，使用 finally 或 context manager。
- 失败指标写盘异常也停止，不掩盖原始异常。
- 资源 receipt 落盘时点与不可变 result 一致，不能为补指标覆盖已发布结果。
- 连续多次成功/命中后线程数不随任务增长；异常和写盘失败也覆盖。
- 全量/增量 result 都明确资源记录覆盖范围。

## 13. 实施报告需要修订的结论

报告承认首次全市场新日期构建尚未验收，这一点保留。但还需修正：

| 报告内容 | 整改要求 |
|---|---|
| archive 后续增量 | R02 修复前不成立；补真实 asset 的测试与运行证据 |
| 缓存只重取缺失数据 | 补因子联合完整性及坏缓存恢复证据 |
| 多日缺口 fail-closed | 当前只覆盖部分分支，先关闭 R06 |
| Sina 与 public 完全等价 | 限定四股普通日期样本，补因子边界和 fallback |
| digest cache 复用 | R08 修复前重复解包，补读取/解包次数证据 |
| CPU/平均核数可选资源 | 说明生命周期累计缺陷，修复后重测 |
| 只剩首次调度门 | 先列未关闭缺陷，再做首次调度验收 |

逐项记录提交、测试、实际退出状态和样本范围；不把样本成功扩大为所有场景成功，不用测试数量代替正确性证据。

## 14. 整体验收和操作纪律

### 本地与隔离验收

- 将复现转成正式回归测试，运行 `pytest -q quant/tests`。
- 重点覆盖 data_ingest、store、website、paper、cron、sampler。
- 遵守 AGENTS 的 lint 约定，Black 120 列。make 不可用时记录环境限制，以等价命令检查受改文件；不要扫描依赖目录来代替业务代码检查。
- 不改 upstream Qlib 或生成的 `_version.py`。

必须包含一条真实 archive 形状的有界隔离集成链路：

```text
父 archive → 新日期构建（含合法成分滞后处理）
→ 新 archive/receipt → 信号消费 → 网站 INCREMENTAL
→ 成功 receipt → 重试校验后短路
```

另测失败恢复、复权边界、已有股票停牌、多日证据不足。固定输入比较预测、排名、成交、费用、持仓、cash、NAV；历史变化须解释并遵守版本化规则，不以“全量所以不同”略过。

不启动常驻 shadow。先本地收敛，再做有界云端验证，避免反复付费实验。

### Modal 与生产边界

- 先确认授权环境、实际 App/Volume、活动任务。
- 探针只读生产输入、只写隔离输出，无发布凭据；不能 monkeypatch 业务规则后写生产目录。
- `modal run` 必须 attached 前台，禁止 `--detach`，禁止 nohup/shell 后台替代。
- 启动前设置覆盖构建、排队、worker、收尾、取回结果的 timeout，保存真实日志/退出码。
- 超时/断连后先查 App 和 artifacts，再决定恢复，不能盲目重发。
- 不自动删除生产快照、journal、账户状态、旧 secret、历史缓存。
- 修复代码不等于授权生产发布或改写历史。部署/真实验收按用户授权范围执行，避开活动任务。

## 15. 整改交付物

1. R01–R10 状态表，每项对应提交、测试、证据。
2. 本地测试及格式检查结果。
3. archive → 新日期 → 网站增量的隔离集成记录。
4. 修正的 CPU/内存测量方法与结果；候选资源规格在有效实测前仍是候选。
5. 更新的实施报告、未完成生产验收与剩余风险。
6. 若有部署：两个 App 的提交/版本、实际 cron 清单、receipt、首次运行结果；未部署则明确写未部署。

不要以“全部解决”替代逐项证据。保留未知异常、数据修订、缓存不兼容和上游不可用的不同错误语义。

## 16. 给 DeepSeek 的启动指令

```text
请阅读 AGENTS.md，以及：
quant/docs/07-code-review/2026-11-10-overlay-primary-implementation-handoff.md
quant/docs/07-code-review/2026-11-11-overlay-primary-implementation-report.md
quant/docs/07-code-review/2026-11-11-overlay-primary-review-remediation.md

请整改 R01–R10。审查基线为 41b1c96bd，先重新核对工作树与部署，保护其他
会话或用户新工作。先把文档复现变成回归测试，再修复，不能以现有测试
通过否认问题。

保持唯一自建 provider、两个生产 App、市场隔离与账户合同。不恢复 chenditc
主备切换，不合并容器、不瘦身镜像、不放宽 gate。尤其要保护已有历史，
区分缺 base 行与真正新股票。

本地整改分批提交，更新逐项证据；再按用户授权范围做有界隔离验证和集中
部署。云端探针只读生产输入、只写隔离输出，严禁 modal run --detach，
不通过探针绕过单写入口修改生产目录。

验收包含真实 archive asset 的网站 INCREMENTAL、CSIndex 滞后 builder 恢复、
因子边界/坏缓存恢复、停牌股票历史保护、多日 PIT fail-closed，以及子进程
退出后的 CPU 累计和成功路径 sampler 清理。
```

## 17. 整改状态更新（当前未提交工作区，2026-11-11）

本节记录 HEAD `41b1c96bd` 之上的当前工作区结果，不改变本文前 16 节在审查时的历史结论。第一轮修复没有被
reset/restore/clean；本轮在其上补了第二轮 S01–S05。所有“关闭”仅指本地代码和回归，不代表提交、部署或生产验收。

| 编号 | 当前本地状态 | 主要回归证据 |
|---|---|---|
| R01 | 保留有效 | builder 从不存在/部分 staging 恢复，违规政策不重试 |
| R02 | 保留有效 | `_latest_overlay_asset()` 真实 archive 字典无需伪造 `local_path` 即可发现增量 ancestor |
| R03 | 保留并补强 | 全 frozen prefix 不变；dated anchor 逐 session 续接，旧 anchor 数值正确 |
| R04 | 保留有效 | 非交易日/窗口前 hfq factor、fast/public 对齐和无效 factor 拒绝 |
| R05 | 保留并补强 | bad hfq cache 会补拉；补拉后 hfq 与 preclose 仍不足时 fail-closed |
| R06 | 保留有效 | 多日无逐日 membership 证据拒绝，有证据时逐日区间和 gate 校验 |
| R07 | 保留有效 | production/probe 共用敏感文件过滤；S01 修复其入口路径副作用 |
| R08 | 保留并补强 | 正常命中不读 archive/不解包；S05 增加实际 calendar 身份校验 |
| R09 | 保留有效 | 子进程退出/PID 重用/cgroup 不可用测试；指标仍声明观测下限 |
| R10 | 保留有效 | success/cache/failure 均 finally stop sampler，不掩盖原始异常 |
| S01 | 本地关闭 | 浅层 `/root/<entry>.py`、本地 checkout、全候选缺失三种入口布局 |
| S02 | 本地关闭 | 10→11→12 得到 1.2/0.1；多公司行为、停牌、缺桥接证据测试 |
| S03 | 本地关闭 | 新抓取和缓存合并后的 hfq/preclose/停牌证据逐 symbol/date 校验；gate 从实际物化 `.bin` 读取 factor |
| S04 | 本地关闭 | quote universe 含恢复持仓/pending；blocked-sell incremental 与 full 精确一致，signal universe 不变 |
| S05 | 本地关闭 | 截断、同末日中间篡改、空 calendar 均不可命中坏缓存 |

验证结果：修复前新增场景为 25 passed、10 failed，另一个物化 factor 独立性测试为 1 failed；修复后完整 `quant/tests` 为 **1062 passed、2 skipped、
50 subtests passed**，24 个变更/新增 Python 文件逐文件 Black 120 检查通过，`git diff --check` 通过。

未完成边界：当前工作区未提交/推送，两个生产 App 未部署，本轮没有 Modal 或生产 Volume 操作，也没有真实全市场
archive→新日期→三市场信号→网站增量的云端验收。实施报告第 11 节记录了详细证据和后续集中验证条件。

## 18. 最终本地验收补记（2026-11-11）

第 17 节之后，独立复审又发现小 factor 的绝对容差和 hfq 覆盖精确 preclose 两处 P1；现已修复并补齐回归。
factor/调整价/gate 均使用尺度无关的比较；有效 BaoStock preclose 优先，已知冲突后不能用 hfq-only 撤销精确调整。
详细实现、数值证据与剩余边界见实施报告第 12 节。

最终本地结果：**1097 passed、2 skipped、50 subtests passed**；Node **30 passed**；24 个受改 Python 文件 Black 120 和
`git diff --check` 通过。用户授权将累积代码、测试和文档一并提交/推送，最终 Git 版本以提交记录为准。
本轮没有 Modal、App 部署或生产数据写入，不把本地关闭等同于生产验收。
