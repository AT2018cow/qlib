# 网站交接文档——每日选股展示站（后续优化/扩展用）

> 面向新对话：网站已上线且全自动运行。本文档说明当前架构、已实现功能、已知设计决策、以及后续优化的起点。

## 一、当前系统状态（2026-09-20 已上线）

### 访问地址

```
https://at2018cow.github.io/qlib/
```

### 自动化全链路（零人工维护）

```
每个交易日 20:30（北京时间，Modal Cron nonpreemptible）
  → 下载 chenditc 最新数据（566MB 全量包）
  → 训练/复用缓存模型（每 20 个交易日重训一次）
  → csi1000 top20 排名 CSV（ranking_only，涨跌停过滤）
  → 60 日走势 JSON（每只入选股收盘序列）
  → akshare 名称映射（有变化才推送）
  → GitHub API 推送 → Actions 自动重建 → Pages 自动发布
```

### 触发规则（`.github/workflows/website-deploy.yml`）

```yaml
on:
  push:
    branches: [main]
    paths:
      - 'results/signals/**'    # 数据变化（cron 推送）
      - 'website/**'            # 代码变化（开发推送）
```

两类变更都自动部署，无手动操作。

## 二、文件结构

```
website/
├── index.html          # 主页面（桌面表格 + 移动卡片 CSS + 版本号 v=12）
├── app.js              # 全部 JS 逻辑（无框架，原生 fetch + DOM）
└── methodology.html    # 策略方法论页（独立静态页，同主题风格）

results/signals/
├── <日期>_top20_lgb158.csv   # 信号（rank, instrument, score）
├── <日期>_chart.json          # 走势（{dates: [], stocks: {code: [closes]}}）
└── code_name_map.csv          # 代码映射（code, name）

.github/workflows/website-deploy.yml  # Pages 部署
.github/disabled/                     # 已禁用的上游 CI（6 个）
```

## 三、已实现功能清单

| 功能 | 实现 |
|---|---|
| 代码+名称+新浪链接 | `sinaUrl()` 函数，仅代码列有链接 |
| 每日变化标签 | 新进（蓝色）/ ↑（红色）/ ↓（绿色）/ —（灰色） |
| 走势 sparkline | 60 日收盘价 SVG 折线，红涨绿跌 |
| 响应式布局 | ≥680px 表格 / <680px 卡片式 |
| 历史日期切换 | 水平滚动条，最近 20 个交易日，当前日期高亮 |
| 策略方法论页 | 6 个板块：Alpha 模型 / Universe / 执行协议 / 回测基准 / 风险 / 管道 |
| 免责声明 | 每页页脚 |
| 名称映射自动更新 | cron 每日拉 akshare，有变化才推送 |

## 四、关键设计决策（后续开发不要违反）

| 决策 | 理由 |
|---|---|
| **纯静态架构** | 数据全在 GitHub，无后端、无服务器、零运维 |
| **无前端框架** | 原生 JS，无 build step——改完即推即部署 |
| **同日信号不可变** | cron 逻辑拒绝改写已存在的同日文件（paper trading 留痕纪律） |
| **Sparkline 颜色 = 红涨绿跌** | A 股惯例（不是国际绿涨红跌） |
| **历史榜单含最新日期** | 无独立"回最新"按钮——当前日期在滚动条中高亮 |
| **CSV_BASE = 'signals'** | app.js 相对路径，Pages 部署时 Actions 把 `results/signals/*` 拷到 `_site/signals/` |
| **cache-busting 版本号** | `app.js?v=N`——每次改 JS 必须递增 N（index.html 里的 `?v=`），否则 CDN/浏览器缓存 10 分钟 |
| **已禁用上游 CI** | 6 个 workflow 移到 `.github/disabled/`（fork 仅研究用，不维护 qlib 代码） |

## 五、已知问题与优化方向

| 优先级 | 事项 | 说明 |
|---|---|---|
| 低 | 历史榜单 >20 天后 | 当前硬编码 `slice(0, 20)`；更久后考虑按月分组折叠 |
| 低 | 名称映射网络依赖 | akshare 接口间歇断连（已 try/except 不影响信号）；长期可改为季度手动更新 |
| 低 | 涨跌停口径标注 | 页面未显示"T 日过滤、T+1 需人工保护"——methodology 已写但首页表头可加 tooltip |
| 可选 | 批量历史回填工具 | `backfill_signals` 函数已有（可对任意历史日期生成信号+chart JSON），如需更多历史数据可用 |
| 可选 | 实时净值曲线 | 需要 cron 推送每日模拟组合净值——当前未实现 |

## 六、开发环境速查

```bash
source .venv/bin/activate
modal profile current          # at2018cow（生产）

# 本地测试网站（无需部署）
cd /tmp && python -m http.server 8899
# 浏览器打开 http://localhost:8899（需先把 website/ + results/signals/ 拷到同目录）

# 手动触发部署（一般不需要——push 即自动）
# 到 GitHub Actions → "Deploy signal website" → Run workflow

# 修改 JS 后必做
# 1. 修改 website/app.js
# 2. index.html 版本号 +1（如 v=12 → v=13）
# 3. git add + commit + push → 自动部署

# 生成额外历史数据
modal run modal_qlib_cn_a10g.py::backfill_signals --dates "2026-09-14,2026-09-15"
# 然后从 Volume 取回并 git push（这些文件也会触发网站自动更新）
```

## 七、关联文档

- 系统总交接：`docs/experiments/HANDOVER.md`
- 终审结论：`docs/experiments/05-final-audit.md`（+11.6% 基线）
- 新增股票池交接：`docs/experiments/HANDOVER-universe-expansion.md`
- 上游 fork 说明：`AGENTS.md`（本 fork 扩展层段落）
