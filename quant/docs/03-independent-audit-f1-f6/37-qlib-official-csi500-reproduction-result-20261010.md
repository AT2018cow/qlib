# 37 — Qlib 官方 CSI500 复现实操结果：v1 数据缺 csi500.txt，BLOCKED（2026-10-10）

> 执行 [doc 36 runbook](36-qlib-official-csi500-lightgbm-reproduction-runbook-20261010.md) 第 3 节 A→B，
> 在 C 的 `qrun` 之前按手册门禁停止。**本次未运行任何训练，未修改任何现有模型/配置/生产。**

## 1. 人工结果摘要（手册 §4 表格）

| 字段 | 值 |
|---|---|
| experiment_status | `BLOCKED`（见原因） |
| upstream_commit | `54355232463878d2eebb91fe0ee5fa7fa1f5976c` ✓（clone + checkout + rev-parse 一致；另经 `ls-remote` 确认存在） |
| yaml_sha256 | `4778b846d52834d4a848b84b7f043317df15727c874d2af269292933db6d44f4` ✓（`git hash-object` = `aa017bc9bff75961c2d2437afcd10e0ed261a4f5`，`git diff --exit-code` 通过，未修改） |
| dataset | 官方 v1，`v1/qlib_data_cn_1d_latest.zip`（196,580,168 bytes，release 资产标称 187MB 一致），2026-10-09 下载；包内时间戳 2021-04-27；`calendars/day.txt` SHA 待补（见 §2） |
| env | Ubuntu 24.04（容器 Debian 12 瘦身镜像实际为 bookworm 系，见 pip 日志）→ 更正：Modal debian_slim（bookworm）；Python **3.12.3**（手册建议 3.11，环境仅有 3.12，已记录偏差）；上游 qlib 按 pin commit 源码构建（Cython 扩展编译通过） |
| test_coverage | 未到达（停在 B 预检） |
| report_metrics | 无（未运行 `qrun`） |
| errors / evidence | `ValueError: instrument not exists: .../instruments/csi500.txt`（preflight，见 §2）；完整下载日志与预检日志见本仓库外（体积原因未入库，要点已摘录） |

## 2. BLOCKED 原因（已实证，非推测）

1. `--version v1` 生效：下载器请求的是 `v1/qlib_data_cn_1d_latest.zip`（196,580,168 bytes，与 release 标称 187MB 一致），文件名时间戳为本地保存时间，非版本错误。
2. 解包后 `instruments/` 仅含 `all.txt` / `csi100.txt` / `csi300.txt`，**无 `csi500.txt`**（`unzip -l` 全量核对，共 31,000 文件）。
3. v1 release 其余资产（`qlib_data_cn_1d_0.6.1.zip` 同体积、`simple` 子集包、1min 包）不可能含有完整包没有的成分文件；`csv_data_cn.zip` 为 0MB 空占位。
4. 官方 YAML 要求 `market: csi500`（benchmark SH000905，Top50/Drop5，close 成交，2008–2020），`D.list_instruments(D.instruments("csi500"))` 在缺文件时直接抛错——preflight 按手册预期失败。
5. 手册禁止的退路均未采用：**没有**退回 v2，**没有**拿现有 CSI1000 provider 充数，**没有**编造成分文件。

## 3. 环境偏差记录（相对手册，仅记录未放行）

- Python 3.11 不可用 → 使用 3.12.3（`python-version.txt` 如实记录；未到对比指标阶段，无影响）。
- 本地曾尝试 venv 方案：缺 `python3.12-venv` 且无 sudo → 改道 Modal at2018cow 隔离容器（CPU-only，8C/32G，不挂载任何 Volume）。本地失败目录已清理（见 §4）。
- Modal 内用 `apt download + dpkg -x` 本地解包 g++ 工具链 + `LD_LIBRARY_PATH` 解决构建依赖；**未改动** Qlib commit、YAML、数据版本。

## 4. 清理

- 已删除本地失败环境 `/tmp/qlib-official-csi500-6CTAGQwq/`（坏 venv、sysroot、debs、上游 clone）。
- 已删除本地调查用 v1 包副本（196MB；证据为上面的文件清单，原包可从公开 release 重新取得）。
- Modal 侧为一次性临时任务（ephemeral），容器退出即销毁；未创建 Volume、未部署 app、未触碰生产。

## 5. 验收对照（手册 §5）

| Gate | 判定 |
|---|---|
| G0 隔离 | PASS（新容器、无 Volume、无生产写入） |
| G1 来源 | PASS（commit/YAML blob 一致，YAML 未修改） |
| G2 数据 | **BLOCKED**（v1 真实可用但缺 csi500.txt；拒绝替代） |
| G3 运行 | 未执行（前置 BLOCKED） |
| G4 解释 | 不适用（无运行结果可比） |

**下一步（如需继续）**：只有两种合规路径——(a) 找到官方发布的、含 csi500.txt 的历史 v1 包（需记录 URL/SHA/取得方式）；(b) 修订手册，明确 v1 路线不可行并改走其他经批准的数据方案。两者都需要先决策，**不得**自行编造 csi500.txt。
