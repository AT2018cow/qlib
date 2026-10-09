# 36 — Qlib 官方 CSI500 / Alpha158 / LightGBM 原版复现操作手册（2026-10-10）

> **仅为实验执行手册；本 PR 不运行模型、不声称已经复现官方收益、不改动任何 CSI1000 Stage-A/B、生产、paper、定时任务或既有实验结果。** 请在独立实验容器/环境操作。无数据、版本不兼容、依赖或模型运行失败时记录 `BLOCKED` 或 `RUN_FAILED`，不得填入估算收益。

## 0. 目标与必须区分的三个口径

**本次唯一目标**：使用微软上游 Qlib 的**原始** `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml`，在隔离目录加载 Qlib 官方历史中国日线数据，执行 **CSI500、Alpha158、LightGBM、Top50/Drop5、close 成交**的一次端到端 `qrun`，记录原始报告及运行环境。

- **不是**将官方参数迁移到 CSI1000；不改变市场、标签、训练区间、组合、费用、调仓和成交时点。
- **不是**运行我们的 Stage-B 静态 quote union 修复、独立账本或 Stage-A 调参；官方原版自身的执行假设不代表真实可成交。
- **不是**承诺复现官网 20 个随机种子统计均值或历史时期的逐字节结果：本次先做一次原样工作流与数据身份审查；若结果不符，先排查版本/数据，再决定是否另立多种子对照。

上游配置 Git blob **SHA-1**（Git 对象 ID）`aa017bc9bff75961c2d2437afcd10e0ed261a4f5`；本仓库副本的 Git blob 相同。但**运行时优先使用真正的微软上游 checkout**，不要从我们的 CSI1000 自定义脚本启动。

固定参考：
- 上游 Qlib commit：[`54355232463878d2eebb91fe0ee5fa7fa1f5976c`](https://github.com/microsoft/qlib/commit/54355232463878d2eebb91fe0ee5fa7fa1f5976c)（2026-10-08 的上游 `main` 快照；固定为复现输入而非“历史 benchmark 当时的软件版本”）。
- [官方原版 YAML](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml)。
- [官方 benchmark 指标与局限](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/examples/benchmarks/README.md)。
- [官方数据下载实现](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/qlib/tests/data.py)。

## 1. 原版配置核对表（禁止“顺手修正”）

| 维度 | 原版文件值 |
|---|---|
| market / benchmark | `csi500` / `SH000905` |
| feature handler / model | `Alpha158` / `LGBModel` |
| train / valid / test | 2008-01-01～2014-12-31 / 2015-01-01～2016-12-31 / 2017-01-01～2020-08-01 |
| handler start/end | 2008-01-01～2020-08-01 |
| strategy | 上游 `TopkDropoutStrategy`, `topk=50`, `n_drop=5` |
| execution | `deal_price: close`, `limit_threshold: 0.095` |
| costs | `open_cost=0.0005`, `close_cost=0.0015`, `min_cost=5` |
| backtest account | CNY 100,000,000 |
| model params | 保持 YAML 全部默认原值，包括 `lambda_l1=205.6999`、`lambda_l2=580.9768` |
| recorder | 原始 `SignalRecord`、`SigAnaRecord`、`PortAnaRecord` |

**注意**：2020-08-01 不是交易日。只允许框架按交易日历处理，不能擅自把 YAML 日期改成 2020-07-31。“official original”也不代表已经验证历史停牌、涨跌停或真实流动性。官方 benchmark 表中的 **Information Ratio** 不能重命名为 **Sharpe**，其 annualized return 也不能自动视作我们 Stage-B 的组合 CAGR。

## 2. 隔离与禁区

必须使用**全新**的可写临时实验目录；以下命令不需要也不得挂载写入：
- `/vol/cn_data`、`/vol/csi1000_stage_b`、Stage-A/Stage-B snapshot；
- 生产代码目录、paper/真实账户状态、MLflow 现有跟踪目录、私有凭据；
- 现有 `~/.qlib/qlib_data/cn_data`（原数据下载脚本可能删除旧目标！）。

**严禁**修改 YAML 后仍标记为“官方原版”。不要执行 `modal deploy`、现有 CSI1000 worker、重训任务或生产 cron。所有数据下载和训练产物留在隔离目录。不要把行情原文件、私有 token、绝对私有路径或体量巨大的 MLflow artifacts 推送公共 GitHub。

## 3. 在实验环境执行（Linux，建议 Python 3.11）

先确保有 `git`、`python3.11`、`venv`、编译所需系统依赖、访问 GitHub 与官方数据服务器的网络。以下命令**刻意固定上游 Git commit**，而不是可变的 `main`；如果不能安装成功，保存原始错误并停止，不要自行改模型参数或换另一个数据版本来追求接近的分数。

### A. 全新目录、固定上游代码和独立 Python 环境

```bash
set -euo pipefail
RUN_ROOT="$(mktemp -d /tmp/qlib-official-csi500-XXXXXXXX)"
export RUN_ROOT
echo "Isolated experiment: $RUN_ROOT"

git clone https://github.com/microsoft/qlib.git "$RUN_ROOT/upstream-qlib"
cd "$RUN_ROOT/upstream-qlib"
git checkout --detach 54355232463878d2eebb91fe0ee5fa7fa1f5976c
git rev-parse HEAD | tee "$RUN_ROOT/upstream-commit.txt"

YAML="examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml"
test "$(git hash-object "$YAML")" = "aa017bc9bff75961c2d2437afcd10e0ed261a4f5"
git diff --exit-code -- "$YAML"
sha256sum "$YAML" | tee "$RUN_ROOT/original-yaml-sha256.txt"

python3.11 -m venv "$RUN_ROOT/venv"
source "$RUN_ROOT/venv/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "$RUN_ROOT/upstream-qlib"
```

这一步使用上游 `pyproject.toml` 管理依赖；**不要直接安装示例目录的老 `requirements.txt`**，其中 `pandas==1.1.2` / `numpy==1.21.0` 对 Python 3.11 不合适。若构建或依赖解析失败，记录为环境 `BLOCKED`；不要静默替换 Qlib commit。

### B. 固定官方 `v1` 数据，并预检 CSI500 与基准

```bash
# 改 HOME 仅影响这个 shell 与子进程的配置位置，不触及真实用户 HOME。
export HOME="$RUN_ROOT/isolated-home"
mkdir -p "$HOME/.qlib/qlib_data"
DATA_DIR="$HOME/.qlib/qlib_data/cn_data"
export DATA_DIR

# 原始下载器默认可能删除旧目标，因此必须在目标不存在时运行。
test ! -e "$DATA_DIR"

cd "$RUN_ROOT/upstream-qlib"
python scripts/get_data.py qlib_data \
  --name qlib_data \
  --target_dir "$DATA_DIR" \
  --interval 1d \
  --region cn \
  --version v1 2>&1 | tee "$RUN_ROOT/download-v1.log"

test -s "$DATA_DIR/calendars/day.txt"
test -s "$DATA_DIR/instruments/csi500.txt"
sha256sum "$DATA_DIR/calendars/day.txt" "$DATA_DIR/instruments/csi500.txt" \
  | tee "$RUN_ROOT/dataset-key-files-sha256.txt"
```

上游下载器**默认会使用 v2**，只有显式 `--version v1` 才会请求 v1；即使如此也须检查下载成功及实际日历覆盖。若 v1 链接/下载器不可用，立即标记 `BLOCKED_V1_DATA_UNAVAILABLE`，**不能自动退回 v2、不能直接拿当前 CSI1000 provider 充当官方数据**。如使用另外获得的历史官方 v1 包，需要单独记录包 URL、版本、SHA256、取得时间及解包步骤，不得声称是上面命令下载的。

```bash
python - <<'PY'
import os
import qlib
from qlib.data import D

qlib.init(provider_uri=os.environ["DATA_DIR"], region="cn")
calendar = D.calendar(start_time="2008-01-01", end_time="2020-08-01")
assert len(calendar) > 1000, "historical trading calendar missing"
assert str(calendar[-1])[:10] >= "2020-07-31", "data does not cover test end"
codes = D.list_instruments(D.instruments("csi500"),
                           start_time="2017-01-01", end_time="2020-08-01",
                           as_list=True)
assert codes, "CSI500 universe is empty"
bench = D.features(["SH000905"], ["$close"],
                   start_time="2017-01-01", end_time="2020-08-01")
assert not bench.empty and bench["$close"].notna().any(), "benchmark missing"
print("provider_preflight=PASS",
      "calendar_rows=", len(calendar),
      "first=", str(calendar[0])[:10],
      "last=", str(calendar[-1])[:10],
      "universe_codes=", len(codes),
      "benchmark_rows=", len(bench))
PY
```

这只是数据覆盖预检，不是 v1 历史价格逐点真实性、点时成分或完整回测结果证明。

### C. 记录版本并运行**一次**未经修改的 `qrun`

```bash
python -V | tee "$RUN_ROOT/python-version.txt"
python -m pip freeze > "$RUN_ROOT/pip-freeze.txt"
python - <<'PY' | tee "$RUN_ROOT/runtime-versions.txt"
import qlib, lightgbm
print("qlib", qlib.__version__)
print("lightgbm", lightgbm.__version__)
PY

cd "$RUN_ROOT/upstream-qlib/examples"
qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_csi500.yaml \
  2>&1 | tee "$RUN_ROOT/qrun.log"

# 不修改已追踪的 YAML；MLflow 产物可能在工作目录中新增未追踪文件。
cd "$RUN_ROOT/upstream-qlib"
git diff --exit-code -- "$YAML"
```

`set -o pipefail` 确保 `qrun` 失败不会被 `tee` 的退出码掩盖。首轮只运行一次；不追加任意随机种子、不使用我们的 deterministic strategy、board exchange 或固定 quote union；也不为了让结果“接近官方”修改费用、参数和时段。

## 4. 必须带回的审计证据（保留原始输出）

在隔离目录保留 `upstream-commit.txt`、`original-yaml-sha256.txt`、`download-v1.log`、`dataset-key-files-sha256.txt`、`python-version.txt`、`runtime-versions.txt`、`pip-freeze.txt`、完整 `qrun.log`，以及该次运行新产生的 MLflow recorder/指标和 `report`/`positions` 等原始 artifacts（若有）。

整理一个**人工结果摘要**，至少包括：

| 字段 | 要填的内容（未知写 `NOT_AVAILABLE`） |
|---|---|
| experiment_status | `PASS_WORKFLOW` / `BLOCKED` / `RUN_FAILED` |
| upstream_commit / yaml_sha256 | 实际值，必须与固定 commit 和原始 YAML 相匹配 |
| dataset | 官方 v1、下载源、取得时间、关键 SHA、日历首末日期 |
| env | OS、Python、Qlib、LightGBM、依赖版本 |
| test_coverage | 实际交易日数量、首末交易日、是否缺日 |
| report_metrics | 官方 recorder 原始 `risk` 指标键及值；IC / Rank IC |
| if computed independently | 策略净收益/CAGR、Sharpe、IR、最大回撤的**单独定义与代码来源** |
| errors / evidence | 完整日志、MLflow run ID、artifact 路径、失败阶段 |

**严禁混淆指标**：官方 README 的 CSI500/Alpha158/LightGBM 行列出 `Annualized Return = 0.1284`、`Information Ratio = 1.5650`、`Max Drawdown = -0.0635`、`IC = 0.0399`、`Rank IC = 0.0482`。该表描述的是多种子 benchmark 统计与其原始报告口径；`1.5650` **不是 Sharpe**，`0.1284` 也不能未经核对当作组合 CAGR。软件版本、v1 数据与实际 recorder 度量可能导致差异；不要为追平上述数字而改参数。官方说明这些模型**不是已穷尽调优的最优配置**。

## 5. 验收状态与后续边界

| Gate | 判定 |
|---|---|
| G0 隔离 | 只在新目录写入；无 Stage-A/B / 生产 / paper / 原 Volume 写入 |
| G1 来源 | 上游 commit、原版 YAML Git blob / SHA256 一致 |
| G2 数据 | 明确 v1、CSI500 与 SH000905 可读取、日历覆盖 2017～2020 |
| G3 运行 | 一次原样 `qrun` 正常完成，有原始 recorder、日志、环境及原始指标 |
| G4 解释 | 与官方 benchmark **同名同定义**指标对照，差异显式列示，不把官方 IR 当 Sharpe，不以数值相近替代 G1～G3 |

全部 G0～G3 成立才写 `PASS_WORKFLOW`；这**不是**现实成交验证或与历史 benchmark 逐位相等的声明。G4 差异需写 `COMPARISON_INCONCLUSIVE` 并调查，不在本次实验期间改动研究协议。G0～G3 任何关键证据缺失则相应 `BLOCKED`，命令失败为 `RUN_FAILED`。

带回**可公开的摘要和日志脱敏信息**后再作下一轮审计决策：是否需要 20 种子 benchmark、是否单开 CSI1000 参数迁移实验。**本 PR 不包含第二项，也不批准重选 Stage-B 或生产晋升。**
