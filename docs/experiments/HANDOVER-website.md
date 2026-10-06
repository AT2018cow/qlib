# 网站交接文档——每日选股展示站（后续优化/扩展用）

> **2026-10-07 研究证据更新**：网站的双池展示/历史信号留痕是已部署工程状态，不等同于当前 alpha 认证。最新 corrected audit 以 [14-pre-tuner-stage-summary-20261007.md](14-pre-tuner-stage-summary-20261007.md) 为准：CSI1000 为当前主调优线；ChiNext frozen baseline 在 deterministic 4-phase screen 中全部负相对超额，仅作为研究/救援线；STAR corrected alpha 因 reproducibility gate 未通过而未知。网站方法论页不得继续把旧 +11.6%/+11.5% 或“STAR 已证伪”作为当前证据。

> **Research-status note (2026-10-07):** Website/cron support for multiple pools is publication infrastructure, not a statement that every displayed pool is currently production-approved. Current research authority is [14-pre-tuner-stage-summary-20261007.md](14-pre-tuner-stage-summary-20261007.md): CSI1000 is the primary validated baseline; ChiNext's corrected frozen baseline is weak; STAR is reproducibility-blocked.


> 面向新对话：网站已上线且全自动运行。本文档说明当前架构、已实现功能、已知设计决策、以及后续优化的起点。

## 一、当前系统状态（2026-09-20 上线；2026-10-02 双池化）

### 访问地址

```
https://at2018cow.github.io/qlib/          # 中证1000 主池（默认）
https://at2018cow.github.io/qlib/?pool=chinext   # 创业板卫星池（或首页顶部 tab 切换）
```

### 自动化全链路（零人工维护）

```
每个交易日 07:00（北京时间，Modal Cron nonpreemptible）
  → 下载 chenditc 最新数据（566MB 全量包）
  → 双池顺序训练/复用缓存模型（每 20 个交易日重训一次；chinext 容器内自动构建等权合成基准）
  → csi1000 + chinext 各自 top20 排名 CSV（ranking_only，板块感知涨跌停过滤）
  → 两池 60 日走势 JSON（停牌 NaN 已 sanitize 为 null）
  → akshare 名称映射（有变化才推送）
  → GitHub API 一次 commit 推 4 文件 → Actions 自动重建 → Pages 自动发布
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
├── index.html          # 主页面（桌面表格 + 移动卡片 CSS + 版本号 v=15）
├── app.js              # 全部 JS 逻辑（无框架，原生 fetch + DOM；双池 POOLS 表驱动）
└── methodology.html    # 策略方法论页（独立静态页，同主题风格；双池口径）

results/signals/
├── <日期>_top20_lgb158.csv            # csi1000 信号（rank, instrument, score）
├── <日期>_chart.json                  # csi1000 走势
├── <日期>_top20_lgb158_chinext.csv    # 创业板信号
├── <日期>_chart_chinext.json          # 创业板走势
└── code_name_map.csv                  # 代码映射（code, name）
```

## 三、已实现功能清单

| 功能 | 实现 |
|---|---|
| 双池切换 | 顶部 tab + `?pool=` URL 参数（可分享）；池后缀驱动所有文件名 |
| 代码+名称+新浪链接 | `sinaUrl()` 函数，仅代码列有链接 |
| 每日变化标签 | 新进（蓝色）/ ↑（红色）/ ↓（绿色）/ —（灰色） |
| 走势 sparkline | 60 日收盘价 SVG 折线，红涨绿跌；**停牌 NaN/null 容错**（parseJSONLoose + 有效点过滤） |
| 响应式布局 | ≥680px 表格 / <680px 卡片式 |
| 历史日期切换 | 水平滚动条，最近 20 个交易日，当前日期高亮 |
| 创业板历史回填 | 9 个交易日（09-17~09-30）as-of 无前视 backfill，与 csi1000 日期对齐 |
| 策略方法论页 | 6 个板块：Alpha 模型 / 双池 Universe / 执行协议 / 双池回测基准 / 风险 / 管道 |
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
| 低 | 信号前端 NaN 防御 | 已修（2026-10-02）：源数据 NaN→null + 前端 parseJSONLoose 双层防御；sparkline 有效点 <2 显示 — |
| 低 | 卫星池执行协议 | 创业板的门控基准（等权 MA20）与止损细则未单独确认，methodology 已标注 |
| 完成 | 批量历史回填工具 | `backfill_signals --market chinext` 已可用（含容器内基准构建 + 池后缀文件名） |
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
# 2. index.html 版本号 +1（如 v=15 → v=16）
# 3. git add + commit + push → 自动部署

# 生成额外历史数据（新池必须带 --market；nd 用该池终审口径）
modal run modal_qlib_cn_a10g.py::backfill_signals --dates "2026-10-09" --market chinext --nd 3
# 然后从 Volume 取回并 git push（这些文件也会触发网站自动更新）
```

## 七、关联文档

- 系统总交接：`docs/experiments/HANDOVER.md`
- 终审结论：`docs/experiments/05-final-audit.md`（+11.6% 基线）
- 新增股票池交接：`docs/experiments/HANDOVER-universe-expansion.md`
- 上游 fork 说明：`AGENTS.md`（本 fork 扩展层段落）
