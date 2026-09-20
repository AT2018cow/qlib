# 网站交接文档——每日选股结果展示站

> 面向新对话：本文档说明做"每日选股结果展示网站"的全部工作基础。数据管道已全自动化运行，网站只需读取其产物。

## 一、需求

展示每日选股结果，要求：
1. **股票代码 + 公司名称**对照（当前 CSV 只有代码）
2. **每日清单变化**（今日 vs 昨日：新进/退出/留存的标记）
3. **近期走势**（个股 K 线或近期收益，加分项）

## 二、数据基础（最重要）

### 2.1 每日信号文件（已自动生成并推送 GitHub）

```
位置:  results/signals/<YYYY-MM-DD>_top20_lgb158.csv
格式:  rank,instrument,score
       1,SH600064,0.11886...
       2,SH601528,0.11380...
```

- **生成规则**：每个 A 股交易日 20:30（北京时间）Modal Cron 自动运行（at2018cow workspace，nonpreemptible）
- **推送规则**：GitHub API 写入 main 分支（不可变——同日不重复，代码在 `modal_qlib_cn_a10g.py` 的 `daily_cron` 函数）
- **信号语义**：`score` = 模型预测的"未来 20 个交易日收益率"（越高越看好）；`ranking_only=True`（排名非可执行订单）
- **目前存量**：`results/signals/` 已有 2026-09-16 和 2026-09-18 两个文件；**以后每个交易日自动新增一个**
- **股票池**：csi1000 成分股（含历史成分，真实 point-in-time 成分——无幸存者偏差）

### 2.2 股票代码 → 公司名称映射（网站需自行解决）

**信号文件不含名称**，需要建立映射。已验证的获取途径：

| 方案 | 接口 | 状况 |
|---|---|---|
| **akshare 静态对照表** | `ak.stock_info_a_code_name()` | 返回全部 A 股代码-名称（~5000只），**本地测试时网络断连**（东财接口 ConnectionReset），需重试或在云端测试 |
| akshare 新浪快照 | `ak.stock_zh_a_spot()` | 本地成功过一次（70 页分页、约 30 秒），列名 `['代码','名称',...]`，但网络间歇断连 |
| **最可靠**：一次性静态 CSV | 生成一份 `code_name_map.csv` 提交到仓库 | 之后网站直接读，无需运行时依赖 |

**推荐做法**：新对话中先尝试 `ak.stock_info_a_code_name()` 生成静态映射文件（代码格式：6 位数字 → qlib 格式转换规则：`SH`/`SZ`/`BJ` 前缀 = 沪/深/北交所；qlib instrument 如 `SH600064` → 纯代码 `600064`）。映射相对稳定（改名/新股少量变化），建议每季度更新一次即可。

### 2.3 走势数据（近期 K 线）

| 方案 | 说明 |
|---|---|
| **chenditc qlib bin**（已有管道） | Modal Volume `qlib-cn-data` 的 `/vol/cn_data/features/<股票代码>/close.day.bin` —— 全部 A 股日线 close 数据。云端 Modal 容器可直接读取；已有现成的 `_read_bin` 函数（`modal_qlib_cn_a10g.py` 搜索 `_read_bin`） |
| akshare 日线 | `ak.stock_zh_a_daily(symbol="sz000001")` —— 返回 OHLCV 日线，含日期索引 |
| 前端图表库自拉 | 如果前端直接用 ECharts/TradingView，可用 akshare 或 tushare 的公开接口（需测试网络稳定性） |

**推荐**：走势数据由网站后端（或构建时的静态 JSON）从 chenditc bin 文件预计算——信号对应的 20 只股票近 60 日的 close 序列——每次发榜时一起生成一份 `signals/<date>_chart.json`。

## 三、网站架构建议

### 3.1 最简方案（纯静态，无后端）

```
GitHub Actions（每个交易日 20:35 触发，即 cron 推送信号后 5 分钟）:
  1. 拉 main 分支
  2. 读取 results/signals/ 全部 CSV
  3. 合并静态 code_name_map.csv
  4. 计算每日变化（新进/退出/留存）
  5. 从预计算的走势 JSON 拼装数据
  6. 生成静态 HTML（或直接用 GitHub Pages + 前端框架）
  7. 推送到 gh-pages 分支
```

优点：零服务器成本、GitHub Pages 免费托管、天然与现有 cron 自动化衔接。
缺点：走势数据需要 cron 端多输出一份文件（见 3.3）。

### 3.2 进阶方案（Modal Web Endpoint）

如果需要实时性/交互查询，可加一个 Modal `@modal.web_server` 函数：
- 读取 Volume 中的信号文件 + bin 数据
- 提供 `/api/signals/<date>` 和 `/api/chart/<code>` 接口
- 前端（Vue/React）部署在 GitHub Pages 或任意静态托管

### 3.3 建议的信号端改动（最小化）

当前 `daily_cron` 只推送 CSV。可加一行（约 10 行代码）同时推送走势 JSON：

```python
# daily_cron 里追加：
chart_data = {}  # {instrument: [近60日收盘价序列]}
# 从 /vol/cn_data/features/<code>/close.day.bin 读最后 60 个值
# 写入 results/signals/<date>_chart.json 一并 GitHub API 推送
```

这样网站端无需访问 Modal/Volume——纯读 GitHub 仓库即可。**建议新对话先做这一步**。

## 四、技术栈参考

| 层 | 建议 |
|---|---|
| 静态站生成 | 任意（纯 HTML/JS、VitePress、Next.js SSG 均可） |
| 图表 | ECharts（K 线/折线）或 lightweight-charts（TradingView 出品，轻量 K 线专用） |
| 托管 | GitHub Pages（fork 已是 public） |
| 触发 | GitHub Actions cron（`on: schedule`）或直接依赖推送事件的 `on: push: paths: results/signals/` |

**免责声明别忘了**：网站每页标注"仅供研究参考，不构成投资建议"（仓库 README 已有先例）。

## 五、复现 / 开发环境速查

```bash
# 本地环境
source .venv/bin/activate
modal profile current          # at2018cow（生产）/ infi（实验）
pip install akshare           # 已装在 .venv

# 信号文件样例
cat results/signals/2026-09-18_top20_lgb158.csv

# 代码格式转换
#   qlib: SH600064 → 纯代码 600064（前缀 SH/SZ/BJ 表示沪/深/北交所）
#   akshare: 600064 直接匹配

# chenditc bin 读取（已在 modal_qlib_cn_a10g.py 中有 _read_bin）
#   文件: /vol/cn_data/features/sh600064/close.day.bin
#   格式: [起始索引(float32), 后续每日close(float32)]

# Modal Volume 数据（走势源）
#   workspace: at2018cow → Volume: qlib-cn-data → cn_data/
```

## 六、关联文档

- 系统总交接：`docs/experiments/HANDOVER.md`（推荐给新对话先读）
- 终审结论：`docs/experiments/05-final-audit.md`（+11.6% 基线、统计不显著等——网站展示数字时须引用这些口径）
- 操作手册：`docs/experiments/04-playbook.md`（信号语义、执行日保护规则——网站的"免责+执行提示"可引用）
- 免责声明先例：仓库根 `README.md` 顶部（research-only, not investment advice）
