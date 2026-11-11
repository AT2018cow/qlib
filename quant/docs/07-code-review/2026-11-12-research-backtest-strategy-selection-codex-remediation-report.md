# 研究回测与策略择优 AR01–AR09 Codex 整改报告

> 日期：2026-11-12
>
> 基线：Git HEAD `4f4163bb1`，叠加此前 RR01–RR09、IR01–IR08 以及本轮 AR01–AR09 未提交工作区修改
>
> 状态：`LOCAL REMEDIATED / LOCAL INDEPENDENTLY RE-VERIFIED / COMMITTED LOCALLY / NOT PUSHED / NOT DEPLOYED`
>
> 性质：研究扩展层本地代码、证据链和门禁整改；不是策略晋级、Modal 云端执行、生产部署或券商成交认证。

## 1. 结论

Codex 已亲自关闭二次独立验收报告中的 AR01–AR09。本轮先加入 11 个能复现真实缺口的回归，修复前结果为
`11 failed`；实现整改后，同一组回归、定向测试和完整 `quant/tests` 均通过。二次验收报告中记录的
`ACCEPTED` 负向探针现均 fail-closed。

本轮没有执行 Modal、没有写生产 Volume、没有部署两个生产 App，也没有提交或 push。三市场当前 profile、
TopK/DropN、三个独立 1M 账户、费用、20-session 重训和 T-close→T+1-open 合同均保持不变。

## 2. AR01–AR09 关闭矩阵

| ID | 最终状态 | 实现与关闭证据 |
|---|---|---|
| AR01 | `LOCAL PASS` | 真实 G1 coordinator 传完整 `assembly_identities[(market, spine)]`；slice producer 使用共享 schema；job、slice、assembly identity 均重算自哈希；真实入口回归覆盖 producer、恢复校验和 assembler |
| AR02 | `LOCAL PASS` | G1/G2/PROMOTION 每个 role 均有业务语义 validator；signal 附件按实际字节绑定；report/ledger/summary 的日期、逐日 gross/cost/net/account 和总值完整对账；selection 指标由逐日报表重算；空业务 payload、伪造 summary 或伪造 selection 数值均拒绝 |
| AR03 | `LOCAL PASS` | baseline、periodic 和 overlay 实际成功路径调用共享 builder；G2 identity 绑定实际 evaluation contract；selection 强制接收已验证 G2 bundle，排名输入只来自验证后 canonical summary，不再读取固定 legacy summary |
| AR04 | `LOCAL PASS` | promotion gate、bundle、release 绑定同一 market/profile/candidate/contract/source/environment/provider/window；preregistration、六类 evidence receipt 及其底层 artifact 都校验路径、SHA、schema、kind、PASS 和 identity |
| AR05 | `LOCAL PASS` | canonical strategy contract 同时绑定规则版本与实际 board/listing/lot/liquidity/fee/account 实体；release `contract_version` 必填且严格匹配；关键 SHA 统一为 64 位小写十六进制 |
| AR06 | `LOCAL PASS` | bear block 绑定市场、profile、strategy contract、provider、calendar、窗口和 expected dates；driver 独立给出期望 identity；combine 拒绝跨市场/策略/provider 拼接并逐日复核账户 |
| AR07 | `LOCAL PASS` | correction generator 使用 versioned 13-unit registry，缺失、额外或替换单元均失败；输出绑定 registry version 与期望集合 SHA |
| AR08 | `LOCAL PASS` | Gate0 CLI 强制接收 provider/start/end，从 provider calendar 推导 expected sessions，并在真实 `main()` 调用中传给重建器；缺交易日失败关闭 |
| AR09 | `LOCAL PASS` | release、promotion receipt 和底层 evidence artifact 均在 `resolve()` 后校验 root containment；绝对路径、`..` 与 symlink 越界均拒绝 |

## 3. 关键实现边界

### 3.1 Evidence bundle 与选择入口

- `research_bundle.py` 负责各层 role 语义校验、附件字节验证、canonical summary 重建和跨 role 对账。
- `run_g1_baseline.py` 的真实 G2 成功路径构建 `G2_EVALUATION` bundle。
- `select_per_market.py` 的 `--g2-bundle` 为必选且可重复参数；没有验证通过的 bundle就不能排名。
- legacy G1/G2/bear 文件继续只作为历史清单审计，未补签为新证据。

### 3.2 Strategy、promotion 与路径

- board/date limit、上市日豁免、lot/quantity、liquidity/missing-quote、费用和账户实体由生产共用 helper
  生成并进入 strategy SHA，不再只依赖手写版本字符串。
- `GATE_PASS` 同时核对 promotion gate result、preregistration、六类 evidence receipt、底层 artifact、
  PROMOTION bundle 和 release identity。
- 所有相对证据路径在解引用后仍必须位于声明 root 内。

### 3.3 Bear、correction 与 Gate0

- bear block 与 combine 共享完整研究身份，且每个账户点必须与逐日净收益一致。
- correction inventory 绑定固定 13 单元登记表；当前登记版本为
  `current_window_20261016_20261029_v1`，集合 SHA 为
  `2075b89b568415b7b28142f1fda88ae266c0ce7c2addacd586a2fbf4a3e5bfd9`。
- Gate0 的完整性检查已从 helper 接入实际 CLI，不提供可验证 provider/calendar/window 时失败关闭。

## 4. Release identity 更新

完整规则实体进入 canonical contract 后，三市场 strategy/manifest SHA 确定性更新如下；release 状态和业务参数未变。

| 市场 | Strategy contract SHA256 | Manifest SHA256 |
|---|---|---|
| CSI300 | `f7d507c110e5508bbbf7ca63d23349c7bd05bc5275d1ad204787f1b256dc2db2` | `8a76e344133307befe6a250db442e54f4fc8ad8f812f5be5f610695ac8fbccce` |
| CSI500 | `4758a4e05f352ad225f4798c5f081d64d7277739ab42b3227a7d4a00a01257ae` | `cdfc90d6ae46e1331e13f21d746066183e52474937ff2e09a6a193ba5100309d` |
| CSI1000 | `36fc0dade7a36fcf46103e129f76551dc722df5448215f5a34ebfe4ee6feac75` | `74e292269ce47cf9a6ab5c9e6483822a61fbb42d9ec8b21f5a3d438fa8554502` |

CSI300/CSI500 仍为 `RISK_ACCEPTED_OVERRIDE`，CSI1000 仍为 `INCUMBENT_RETAINED`；没有把任何市场伪装成
新研究 `GATE_PASS`。

## 5. 本地验证证据

修复前新增回归：

```text
11 failed in 1.39s
```

修复后完整测试：

```text
.venv/bin/python -m pytest -q quant/tests
1256 passed, 2 skipped, 24 warnings, 50 subtests passed in 43.57s
```

格式与静态检查：

```text
本轮涉及的 18 个 Python 文件逐文件 Black --check：PASS
git diff --check：PASS
```

目录级 Black 在当前受限沙箱中因其多进程 socket 创建被拒绝，故使用同一 Black 配置逐文件串行检查；这不是
源码格式失败。

独立负向探针最终结果：

```text
AR01 真实 assembly 参数/producer schema：正确
AR01 assembly identity 自哈希篡改：REJECTED
AR02 空业务 payload：REJECTED
AR02 伪造日期和逐日净收益：REJECTED
AR03 selection 无 verified G2 bundle/固定 summary 绕过：REJECTED
AR04 空 PROMOTION payload 或底层 artifact 替换：REJECTED
AR05 伪造规则实体/contract_version：REJECTED
AR06 混合 bear identity：REJECTED
AR07 缺少登记单元：REJECTED
AR08 真实 CLI 缺交易日：REJECTED
AR09 symlink 逃逸 root：REJECTED
```

提交范围 review 另外加入 4 个真实链路负控：G2 evaluation identity 不得继承错误的 G1 strategy contract、
verified G2 必须能派生 overlay G2、overlay runner 必须调用 builder、selection 数值必须从 verified report 重算。
旧实现为 `4 failed`，整改后全部通过并已计入完整测试。

## 6. 尚未完成的外部边界

- `COMMITTED LOCALLY / NOT PUSHED`：本地提交已形成，但未推送远端。
- `NOT CLOUD-REPLAYED`：未在 Modal worker 中运行真实三市场 bounded replay，也未验证云端 image/import identity。
- `NOT DEPLOYED`：未更新 Modal App、schedule、Volume、GitHub 信号或网站。
- `NOT PRODUCTION-ACCEPTED`：未做生产输入只读、输出隔离的 attached 云端验收。
- `NOT BROKER-CERTIFIED`：paper/backtest 仍不是券商成交或竞价流动性认证。

因此当前可声明的是“本地代码与回归已关闭 AR01–AR09”，不能声明“已部署”或“生产已验收”。
