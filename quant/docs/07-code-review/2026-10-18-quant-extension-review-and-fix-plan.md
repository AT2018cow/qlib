# AT2018cow/qlib 自定义扩展代码审查与修复实施清单

> **交接用途**：交给 DeepSeek 实施修复；修复完成后由独立审查方按本文件验收。  
> **审查日期**：2026-10-18（Asia/Singapore）  
> **基准仓库**：https://github.com/AT2018cow/qlib  
> **审查基准**：`main`，commit `eb86df1978fa81ee1cef27f948a9add6eb094ee1`（2026-10-17 17:03:12 UTC）  
> **修改权限**：此文件只列建议；本轮没有更改原 GitHub 仓库或触发生产作业。

## 0. 结论和边界

本轮针对**用户自建扩展层**进行风险优先的静态代码审查。重点查阅 `quant/code/production/`、`quant/code/execution/`、`quant/code/ops/`、`quant/code/audit/`、`quant/website/`、相关 `quant/tests/`，以及专为扩展层创建的 `.github/workflows/`。研究模块与大量历史证据文件仅进行定向检查，**不是对 `quant/` 的每一个源文件逐行审计**。

明确不审查、不修改 Microsoft Qlib 上游源码，包括根目录 `qlib/`、`examples/`、`scripts/`、上游 `tests/` 和 `docs/`。扩展对上游 API 的使用可作为修复集成点，但不得将问题转嫁为修改 Qlib 本体。

**审查方式与局限**：依据上述固定 commit 从 GitHub 逐文件读取源码、测试和 2026-10-16 已公开的三指数信号元数据。尚未在目标 Modal 工作区运行生产任务，也未独立取得行情源、密钥或真实成交信息；没有执行完整 `pytest`、浏览器或部署测试。因此下文的“已确认”指**源码路径/逻辑可证实**，不表示线上事故已经发生；涉及并发、故障注入的条目明确标为“需动态复现”。不以历史文档中的旧结论替代当前代码证据。

### 问题概览

| 编号 | 优先级 | 性质 | 风险概述 | 实施顺序 |
| --- | --- | --- | --- | --- |
| R01 | P1 | 已确认逻辑缺口 | 三指数发布只验证 CSI1000 的数据日和交易日 | 第一批 |
| R02 | P1 | 需故障注入验证 | GitHub 提交与三份 Modal 账户状态无法原子恢复，两个 cron 可重叠 | 第一批 |
| R03 | P1 | 已确认逻辑缺口 | 独立净值审计过滤掉现行 canonical 账本 | 第一批 |
| R04 | P1 | 已确认危险数据路径 | 外部股票名称及 CSV 内容进入前端 `innerHTML`，存在 XSS 风险 | 第一批 |
| R05 | P1 | 已确认危险 API 用法 | 下载外部 tar.gz 后无成员校验直接解包 | 第一批 |
| R06 | P2 | 已确认逻辑缺口 | 跨市场 `paper_lineage` 仅做集合校验，不绑定对应指数 | 第二批 |
| R07 | P2 | 已确认控制流 | 同日信号文件齐全时提早返回，跳过股票名称映射更新 | 第二批 |
| R08 | P2 | 已确认独立运行问题 | 部分独立 CLI 依赖隐含 `PYTHONPATH` | 第二批 |
| R09 | P2 | 已确认 CI 覆盖问题 | 扩展执行/审计等代码修改无法稳定触发完整回归检查 | 第二批 |
| R10 | P2 | 需故障注入验证 | 模拟账户先修改内存状态再进行最终账本验证 | 第二批 |

**优先级定义**：P1 = 可能影响错误发布、资产记录正确性或安全边界，修复后再考虑部署；P2 = 稳定性/审计完备性/可维护性，建议同轮完成。没有将潜在风险夸大为已观察到的线上损失。

---

## 1. 具体发现和 DeepSeek 实施要求

### R01 — P1：CSI500 / CSI300 的数据日没有与发布日独立对齐

**定位**：[`quant/code/production/modal_qlib_cn_a10g.py:L4262-L4311`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4262-L4311)，[同文件 L4320-L4325](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4320-L4325)、[L4377-L4414](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4377-L4414)，[同文件 L3676-L3709](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L3676-L3709)。

**证据**：cron 并行分别调用三次 `daily_standalone.remote`；每个 worker 自己下载 `latest` 行情包。随后 `data_date = res['date']`、`_publication_decision(...)` 和 `_csi1000_canonical_publication_decision(...)` 仅检查 CSI1000 的 `res`。CSI500 / CSI300 返回值在检查数据新鲜度之前就按同一个 `signal_date` 组装发布文件，缺少对各自 `date` 与数据来源版本的等价检查。`build_mvp_metadata` 只要求 `signal_data_date < usage_date`，不能替代“紧邻的上一交易日”验证。

**影响**：三份信号可能来自不同行情版本或不同最近交易日，却同时显示为同一个执行日；这会损害 T-close → T+1-open 的时序合规性，并使账户状态和元数据的比较产生误导。此项为可确认的校验缺口，**并未确认现有 2026-10-16 发布的数据真的错位**。

**实施**：将发布检查抽成纯函数 `validate_multi_market_publication(results, signal_date, verified_calendar)`（名称可改）。逐市场强制：预期 market、数据日属于已核实交易日、数据日恰为执行日前一交易日、与统一 `expected_data_date` 一致；源包 `archive_sha256`、`calendar_sha256` 及必要的数据快照标识一致，且 `provider_identity.calendar_end == res.date`。发生任一不一致必须阻止受影响的整体 canonical 发布，不能只打印警告。更稳妥的结构是同一次任务固定一次可验证的不可变行情快照并供三池使用，避免 `latest` 在不同容器下载时漂移。

**验收测试**：三池同日同数据包通过；CSI500 滞后 1 个交易日拒发；CSI300 提前/未来一天拒发；日期相同但 archive 或 calendar SHA 不同拒发；正常周五→周一、长假→复市通过。断言在所有校验通过前，不会调用 `push_files`、修改 PaperPortfolio 或提交 Volume。

### R02 — P1：账户状态与 GitHub 发布非原子，补跑调度可能重叠

**定位**：[`modal_qlib_cn_a10g.py:L1445-L1472`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L1445-L1472)、[L4416-L4460](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4416-L4460)、[L4203-L4215](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4203-L4215)、[L4514-L4525](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4514-L4525)；[`quant/code/production/github_commit.py`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/github_commit.py)。

**证据**：先 `push_files` 到 GitHub，之后将多个状态路径逐个 `os.replace`，最后 `vol.commit()`；没有持久的事务 ID、锁或重放协议。GitHub 不可变旧文件比较会拒绝内容变化，但若成功发布后 Volume 提交失败，第二次运行可能从旧状态生成不同的状态/时间戳而被拒绝。07:00 cron 最长 2 小时、07:45 backup 可再次远程调用 `daily_cron`；未看到跨两次任务的互斥控制。`_apply_paper_portfolio` 对 `vol.reload()` 失败执行 `except Exception: pass`，进一步放大读取旧版本风险。

**影响**：可能出现“GitHub 文件已提交但模拟账户未推进”、三池仅部分状态被本地替换、并发重试和重复建仓尝试。**需要模拟推送/提交/并发故障验证具体触发顺序**，不应在未复现时断言现有账本已损坏。

**实施**：设计跨流程可重入的提交状态机：单日+市场组合的确定性 publication ID、输入/前状态哈希、待提交提案哈希、`PREPARED → GITHUB_PUBLISHED → VOLUME_COMMITTED` 记录；重试优先读取已发布不可变文件和既有账本状态进行校验及恢复，避免重新生成不可复现的预开盘时间和 ST 快照。三池状态以可恢复的 manifest / generation 标记一致推进；使用带租约和 fencing/generation 校验的跨任务锁，backup 若已有运行则跳过或等待已有任务结果，不得无锁重入。任何 `vol.reload()` 异常直接阻止账户推进。维持 `push_files` 的非强推安全要求；不修改历史已发布文件。

**验收测试**：mock GitHub/Volume，分别在 `push_files` 之前、GitHub 成功之后、写入第二个市场状态之后、`vol.commit` 时注入异常并重启；结果应自动恢复到完全一致的发布/三池状态，或者明确 `BLOCKED_MANUAL_RECOVERY`，不得重复成交或更改不可变历史。模拟双 cron 并发保证同日最多一条状态迁移；测试 `vol.reload` 抛异常时 fail-closed。

### R03 — P1：独立净值审计没有覆盖当前正式策略

**定位**：[`quant/code/ops/audit_paper_nav.py:L19-L23`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/ops/audit_paper_nav.py#L19-L23)、[L69-L83](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/ops/audit_paper_nav.py#L69-L83)、[L371-L387](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/ops/audit_paper_nav.py#L371-L387)；[`quant/code/production/csi1000_production_config.py:L25-L43`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/csi1000_production_config.py#L25-L43)。

**证据**：独立审计工具固定 `LINEAGE = "stage_b_winner_canonical"`，且只接受 `profile="stage_b_winner"`。当前 CSI1000 已是 `c98856b4_top10_drop1_canonical` + `c98856b4_top10_drop1`；已查看仓库 `quant/data/signals/2026-10-16_paper_portfolio.json` 与该新标识一致。旧策略过滤完后，函数可输出 `NOT_STARTED`，看起来像无需核验。当前测试 [`quant/tests/test_paper_nav_independent_audit.py`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/tests/test_paper_nav_independent_audit.py) 也主要使用旧 winner fixture。

**影响**：该“独立净值审计”不能作为当下 CSI1000 canonical 的验收证据，可能产生审查覆盖空窗。若该工具本来专用于历史 winner，也必须通过改名/文档明确其范围，另提供现行 canonical 的独立审计；不能让旧工具的 `NOT_STARTED` 冒充现行生产 PASS。

**实施**：增加 explicit `market/profile/lineage` 审计参数或基于 `CANONICAL_PAPER_LINEAGES_BY_MARKET` 明确当前市场配置，默认审计正在上线的 canonical；保留旧模式做历史审计但清楚标记。审计模块保持与生产会计的**独立计算**，切勿直接调用生产 `verify_ledger()` 替代独立复核。把实际当前风格的 artifact 契约放进 fixtures；若目录存在当前策略文件但未产生结算，明确返回 `AWAITING_SETTLEMENT`，不要默默过滤后说通过。

**验收测试**：当前 CSI1000 输入应被计入或明确 `AWAITING_SETTLEMENT`，旧 winner 仅在指定 legacy 模式复核；错误的 market/lineage 映射拒绝；账本金额/成本/持仓注入错误被发现；无任何当前 artifacts 不得返回 PASS。

### R04 — P1：前端对第三方股票名称和 CSV 动态 HTML 缺少净化

**定位**：[`quant/website/app.js:L188-L200`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/website/app.js#L188-L200)、[L490-L542](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/website/app.js#L490-L542)、[L729-L735](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/website/app.js#L729-L735)、[L784-L785](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/website/app.js#L784-L785)；[`quant/website/build_index.py:L38-L55`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/website/build_index.py#L38-L55)。

**证据**：`nameMap[code]` 原样写入 `r.name`；`tableRows()` 和 `mobileCards()` 用 `${r.name}`、`${r.instrument}`、`${r.url}` 拼出 HTML，最后赋给 `innerHTML`。`available_dates*.json` 名称映射由外部数据 `code_name_map.csv` 构建，前端只验证整个 `names` 是 object，不验证其 value。CSV `parseCSV` 与 `loadDateRows` 在渲染前未强制校验 symbol、rank。即使正常生产 CSV 已有服务端校验，也不应信任第三方名称或历史/损坏的静态文件作为 HTML 源。

**影响**：被篡改或返回恶意字符串的名称/CSV 能在浏览器注入事件处理器或标记（存储型 XSS 条件）。该风险源于代码数据流，**未声称已实际遭攻击**。

**实施**：优先使用 `document.createElement()` + `textContent` 设置股票名称、代码和数值；链接由经正则检验的标准代码构造并设置安全 `href`，不用未净化数据构造 HTML 属性。对必须拼接的固定标签采用统一转义函数，且不把未信任数据作为“已转义 HTML”复用。`loadAvailableDates` 验证名称对象 key/value 类型；CSV 在渲染前验证 `^(SH|SZ|BJ)\d{6}$`、rank 正整数顺序、score 有限值并限制行数。仅保留固定模板生产的 SVG path/标签字符串。不要简单用全局 `replace()` 对内容做不可靠替换。

**验收测试**：名称值 `<img src=x onerror=alert(1)>`、包含双引号和 `&`、CSV instrument `" onmouseover="...` 等输入都只能显示为文本或被拒绝；不产生 `img`/脚本事件节点，正常名称/链接/响应式表格保持可用。运行前端 Node 测试并建议在 DOM 模拟环境中新增测试。

### R05 — P1：外部数据归档存在未验证 tar 成员的提取风险

**定位**：[`quant/code/production/modal_qlib_cn_a10g.py:L3676-L3699`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L3676-L3699)、[L4022-L4051](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4022-L4051)。

**证据**：两处从 GitHub release `latest` 获取外部 tar.gz，然后 `tarfile.open(...): tf.extractall(extract)`，没有验证成员路径和符号链接、文件数量或总展开大小。Modal 容器配置 Python 3.11，不能依赖较新 Python 版本的默认 tar 安全过滤。

**影响**：当数据源、传输或上游发布被污染时，恶意归档可能路径穿越写出目标目录、使用符号链接覆盖其他文件或耗尽容器磁盘。现有 `archive_sha256` 是事后记录身份，不证明下载内容被信任或安全。

**实施**：提取到新建临时目录，提取前遍历并拒绝绝对路径、`..`、越界解析路径、软/硬链接、设备文件、FIFO、非常规文件；限制总文件数、单文件和总解压大小；验证唯一根目录和 Qlib 数据包必要结构；失败时拒绝应用，清理临时目录。统一成可测试的 `safe_extract_provider_archive`，两处调用同一个函数。如果有独立签名/可信 digest 清单，验证其不可变 SHA，且不得把 `latest` 下载时自己算出的 SHA 当作信任锚。

**验收测试**：正常小型样例可用；`../escape`、`/tmp/evil`、符号链接逃逸、超限成员、数据包缺少 `calendars/day.txt` 时必须失败且目标目录外没有写入。

### R06 — P2：paper lineage 没有约束到相应市场

**定位**：[`quant/code/production/csi1000_mvp_contract.py:L341-L360`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/csi1000_mvp_contract.py#L341-L360)、[`quant/code/production/forward_performance.py:L18-L42`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/forward_performance.py#L18-L42)、[`quant/code/production/csi1000_production_config.py:L34-L43`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/csi1000_production_config.py#L34-L43)。

**证据**：`summarize_paper_state()` 仅检查 `paper_lineage in (四个允许值)`；`build_forward_performance()` 也对三种 canonical lineage 采用集合包含判断，未强制 `market ==对应 lineage`。因此 csi300 报告若带 csi1000 账本标识，同时生产配置身份为 csi300，仍可绕过这一层 lineage 检查。

**影响**：跨市场错配无法被关键门禁发现；在错误装配 artifact 或迁移时损害前向业绩可信度。

**实施**：在两处以 `CANONICAL_PAPER_LINEAGES_BY_MARKET` 作明确一一对应检查；历史 winner 单独版本化启用，不能继续通过“任何 canonical 都合法”。对 metadata/paper/report 的 `market`, `profile`, `candidate_id`, `paper_lineage` 做一致性比较，拒绝漂移。

**验收测试**：三市场各自正确 lineage 通过；市场 × lineage 共九种组合中，六种交叉错配必须失败；保留历史模式需显式指定。

### R07 — P2：所有主文件已存在时，名称映射更新被直接跳过

**定位**：[`modal_qlib_cn_a10g.py:L4427-L4450`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4427-L4450)、[L4466-L4511](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/modal_qlib_cn_a10g.py#L4466-L4511)。

**证据**：`if not missing_files: _persist_paper_states(); return` 位于股票名称映射代码之前。正常的同日重试或当日四件套已经存在时，不会执行 `stock_info_a_code_name()` 更新。所审 `quant/data/signals/` 列表只有 2026-10-16 的 12 个日文件和目录说明文件，没有 `code_name_map.csv`；但无法仅凭此归因于该提前返回。

**实施**：将名称映射更新抽成独立幂等、可选失败的任务；无论 `missing_files` 是否为空，完成主工件对账/状态一致性后都可触发。不得让非关键名称映射失败中断已通过的主信号发布。

**验收测试**：主工件全已存在时仍检查名称源并按内容差异更新；同内容时不重复提交；名称源故障不修改冻结信号或阻断主路径。

### R08 — P2：部分独立命令行程序只有在预先注入 PYTHONPATH 后才可运行

**定位**：[`quant/code/ops/build_forward_performance.py:L10-L13`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/ops/build_forward_performance.py#L10-L13)、[`quant/code/ops/accept_forward_nav.py:L18-L21`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/ops/accept_forward_nav.py#L18-L21)、[`quant/code/audit/mvp_entry_review.py:L17-L22`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/audit/mvp_entry_review.py#L17-L22)、[`.github/workflows/website-deploy.yml`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/.github/workflows/website-deploy.yml)。

**证据**：上述脚本将仓库根路径加入 `sys.path`，随后却以 `from forward_performance import ...` 或 `from csi1000_production_config import ...` 导入实在 `quant/code/production/` 下的模块。脚本独立直接执行时这些 import 无法由根目录解析。CI Pages workflow 显式设置长 `PYTHONPATH`，`quant/tests/conftest.py` 也注入多个目录，因此可掩盖独立运行问题。

**实施**：推荐采用真正的 `quant.code.production...` 包导入、`python -m ...` 入口及明确包结构；如迁移成本高，短期用统一 bootstrap 将 `quant/code/{production,execution,...}` 的绝对路径按仓库根计算且仅添加可信自有路径。避免拷贝环境变量到每个入口形成新漂移。

**验收测试**：在干净环境 `env -u PYTHONPATH python quant/code/ops/build_forward_performance.py --help`、`accept_forward_nav.py --help`、`mvp_entry_review.py --help` 必须退出 0；实际最小输入亦可跑通；CI 加独立进程测试。

### R09 — P2：PR 的 CI 触发范围和执行测试不足以覆盖生产扩展

**定位**：[`.github/workflows/csi1000-tuner-core.yml:L3-L83`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/.github/workflows/csi1000-tuner-core.yml#L3-L83)、[L99-L128](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/.github/workflows/csi1000-tuner-core.yml#L99-L128)。

**证据**：path filter 仅列部分生产/Stage-A/Stage-B 文件，未包含如 `quant/code/execution/execution_limits.py`、`board_rules.py`、`quant/code/production/paper_accounting.py`、`github_commit.py`、`paper_halt_evidence.py`、`quant/code/ops/audit_paper_nav.py` 等。测试作业只运行固定 11 个左右的测试文件，而 `quant/tests/` 实际还有大量执行、独立审计和搜索测试。

**影响**：修改执行规则或生产记账模块有机会不触发核心 CI，产生回归未检出；CI 绿色不代表全部扩展被验证。

**实施**：将相关 workflow 的 `pull_request` 路径覆盖 `quant/code/**`、`quant/tests/**`、`quant/website/**` 及自身工作流；分组执行 fast/core、execution/production/audit、Node/front 测试，安装相应依赖并避免在 PR CI 中触发昂贵 Modal 作业。建议把关键检查配置为必需状态检查；必要时主分支按较低频次做完整扩展回归。

**验收测试**：仅修改 `execution_limits.py`、`paper_accounting.py`、`audit_paper_nav.py` 或新增 `quant/tests/test_*.py` 的 PR 都触发预期测试；CI 内确认核心防泄漏/账本/前端等回归运行，输出可靠 exit code。

### R10 — P2：PaperPortfolio 的异常回滚不是类级保证

**定位**：[`quant/code/production/paper_portfolio.py:L270-L355`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/paper_portfolio.py#L270-L355)、[L391-L420](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/paper_portfolio.py#L391-L420)。

**证据**：`execute_pending` 在构造并验证最终 report 之前已经递增持仓天数、卖出/买入、修改 `self.cash` 与 `self.positions`；`verify_ledger(report)` 在最后调用。如果发生最终校验异常，本对象已被部分改变，而 `pending_signal` 仍未清除。现生产调用方通常会丢弃异常对象且先发布后持久化，降低事故概率，**但函数本身没有失败不变性保证**。

**实施**：拷贝账户状态到临时工作对象，全部订单执行和严格估值验证通过后才一次性提交到 `self`；或设计精确 rollback。统一校验输入为有限数值、合法 factor、非负现金，并保持失败时原对象 `to_dict()` 字节级不变。

**验收测试**：注入 `verify_ledger` 错误、非法后续报价、现金不足/无有效 lot 等多种路径，异常前后 `to_dict()` 完全一致；正常执行与原逻辑产生相同结果和交易手续费。

---

## 2. 给 DeepSeek 的开发约束（必须遵守）

1. **基于固定 SHA 工作**：从 `eb86df1978fa81ee1cef27f948a9add6eb094ee1` 建独立修复分支；若修复时 `main` 已前进，记录新的 base/head SHA，先比较变更，不得无审查把较新改动覆盖掉。按 R01–R10 对照完成，不能只改注释/测试让 CI 绿。
2. **只修改自定义扩展层**：允许 `quant/code/**`、`quant/tests/**`、`quant/website/**`、自定义 `.github/workflows/**`、新增 `quant/docs/**`；不要修改上游 `qlib/`、`scripts/`、`examples/`、根目录上游测试或历史配置。必要的上游接口兼容通过自定义 wrapper 处理。
3. **不得改策略研究结论**：保留 CSI300 / CSI500 / CSI1000 当前 frozen candidate ID、LightGBM 参数、label horizon、Top10/Drop1、T-close → T+1-open、20-session retrain 约定；不依据近期行情重选赢家，不偷偷调整现行历史收益口径。
4. **历史与线上工件不可改写**：不得重写 `quant/data/signals/` 已发布日期、历史回测证据、冻结研究快照及账本；不得为测试伪造“已核验”真实 ST、停牌、成交、价格或第三方数据来源。测试使用隔离 tmpdir、stub/mock，明确标注 synthetic。
5. **严守失败关闭**：行情日、跨市场快照、正式 lineage、模拟持仓估值/账本、可交易性原始证据无法核验则 BLOCK，不能默认 PASS；已定义的“ST 传输故障下 reference-only”是显式有标签的现行合同，是否收紧须先说明行为兼容影响并经人工批准。
6. **不要直接部署**：先提交 PR、变更清单与本地/CI 测试记录；未经明确人工批准不可运行 `modal deploy`、覆盖 Volume 生产状态、更新真实仓库已有信号、推送主分支，也不可访问/打印密钥。
7. **把相关测试写成行为验证**：避免只搜索某行字符串的脆弱断言；新增跨市场日期、原子恢复、XSS、恶意归档、历史/现行 lineage、CLI 子进程和 CI path guard 的实质测试。

## 3. 建议分批和交付格式

建议先做 **批次 A**：R01、R02、R03（信号时序、状态一致性和独立审计）；**批次 B**：R04、R05（浏览器与归档安全）；**批次 C**：R06–R10（合同绑定、日更行为、运行入口、CI、内存失败不变性）。R02 改动范围可能较大，至少独立 PR，避免锁/事务逻辑与策略逻辑一起更改。批次 A 和 B 可按具体可验证依赖调整顺序。

DeepSeek 每次提交须附以下内容：

- Base/head commit SHA、PR 链接、修改文件清单与每项 R 编号。
- 问题根因和修复前复现方法（无法在真实 Modal 中运行时，附最小 stub/故障注入复现）；修复后行为以及兼容风险。
- 实际执行的测试命令、退出码、通过数、跳过数；**不能把未执行测试写成 PASS**。
- 是否需要数据迁移、生产停机、状态恢复、部署/回滚步骤；如果不需要，明确写“不需要”。
- 任何未解决问题另列 blocker，不能用“已知限制”代替关键失败关闭门禁。

## 4. 独立验收清单（交付回来后执行）

- [ ] 对照本文 R01–R10 的问题逻辑逐项检查修改后的代码与对应 PR diff，而非仅相信 DeepSeek 自述。
- [ ] 对 R01 做三池日期/源包错配测试，确保发布前全部校验；正常交易日与节假日边界不退化。
- [ ] 对 R02 做失败点注入、双 cron 并发、重复调用、GitHub 有工件/Volume 无状态的重放测试；不能覆盖不可变历史或重复成交。
- [ ] 对 R03/R06 用当前三池真实 lineage 结构的**脱敏 fixture**验证，确保不会再错误返回 PASS/NOT_STARTED，历史 winner 模式必须明确。
- [ ] 对 R04/R05 做 XSS 及危险 tar 自包含样例验证，不使用真实生产密钥、数据或部署。
- [ ] 对 R07/R08/R09/R10 做幂等映射更新、无 PYTHONPATH CLI、CI 触发、账户回滚测试。
- [ ] 运行 `python -m pytest -q quant/tests`；新环境如缺依赖，提供可复制的环境安装方法并说明哪些确实没跑。
- [ ] 运行 `node --check quant/website/app.js`、`node --test quant/tests/test_sparkline_frontend.cjs`、`python -m compileall -q quant/code quant/website`；核对 CI workflow 的实际执行记录。
- [ ] 确认修改仅位于许可的自定义目录，无上游 Qlib diff，无已发布 `quant/data/signals/` 文件被改写，无凭空伪造行情/收益。
- [ ] 审核发布前后关键契约不变：各市场 candidate ID、label horizon、Top10/Drop1、交易日时序、费率/physical lot、PIT 防泄漏、前向收益不拼接历史回测。
- [ ] 验收输出 `PASS / PASS_WITH_FOLLOWUPS / FAIL`，每项 R 给出证据、残余风险与是否允许进入发布前人工审批。

**验收门槛**：P1 事项必须修复并有适当回归测试；需要外部生产环境才能证实的部分不得因为模拟测试通过就标注“线上已验证”。只有在人工核对发布前变更、密钥/Volume 隔离与回滚方案后，才能进一步决定是否部署。

---

## 5. 已有代码中值得保留的防护（避免修过头）

- 现行 [`signal_publication_gate.py`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/signal_publication_gate.py) 对 CSI1000 已采用 verified calendar 和 `lag=0` 失败关闭；R01 是要求推广到另两池，而不是删除这些门禁。
- [`paper_accounting.py`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/paper_accounting.py) 独立从 fills 核验现金/持仓/估值，缺价时非已证实停牌不准结算；R10 是加强**失败时对象的原子性**，不改其会计公式。
- [`csi1000_mvp_contract.py`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/csi1000_mvp_contract.py) 已有 PIT universe、标签成熟度、frozen model lineage 以及有限分数校验；应复用而非绕过。
- [`paper_portfolio.py`](https://github.com/AT2018cow/qlib/blob/eb86df1978fa81ee1cef27f948a9add6eb094ee1/quant/code/production/paper_portfolio.py) 对 Qlib `$factor`、物理交易手数、未成交不自动替代等规则已显式规定，修复过程中不得退化。
- 现有 GitHub 已发布 artifacts 采用同日不可覆盖校验。事务恢复应在此基础上实现，**绝不是取消不可变校验**。

## 6. 审查后续接口

DeepSeek 修复后，把 PR URL 或完整 diff 及上述测试输出交给独立审查方；验收只针对实际提交的变更和证据，按照本文问题编号逐项判定。无法访问实际 Modal 生产 Volume/交易账户时，应单独列出“需要操作员在授权环境执行的上线前核验”，不能把静态审查误写为最终生产认证。