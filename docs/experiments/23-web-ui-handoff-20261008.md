# 23 — 独立对话交接：网页体验与数据展示优化（2026-10-08）

> **本文件是“继续优化网页”对话的唯一启动入口。**
> 新模型研究另见 [22-handoff-star-chinext-research-20261008.md](22-handoff-star-chinext-research-20261008.md)。
> 对话的职责是“已有数据和已批准的模型怎样准确、舒适、专业地展示”，而不是重新研究模型或扩大 production 范围。

## 1. 当前代码和实际验证边界

- 仓库：`AT2018cow/qlib`。交接基线：`main` merge commit `68cfde375dd29f70fb94a901bc2152ff750bc4d4`（PR #34 已合并）。新对话先查最新 `main` 和相关 Actions，不假定基线永远不变。
- PR #29 重做 Dashboard/方法论；PR #30 采用用户选择的**深色、克制的金融终端风格**；PR #31 修复 GitHub Pages 过去遗漏 CSS 的发布错误；PR #33 引入 forward cumulative paper return；PR #34 修复近60日走势时间轴、桌面/手机布局并添加 favicon。
- PR #34 合并前验证过 **104 项 Python 测试 + 4 项 Node 前端测试 PASS**；这不是“真实浏览器所有屏幕尺寸无 bug”的证明。每次修改后须同时验证 GitHub Pages build artifact 与实际浏览器呈现。
- 当前公开生产线为**CSI1000 Stage-B winner**。创业板旧模型 daily update 和网页入口暂停；STAR 未进入 production。
- 本次交接**没有**直接核实最终 Modal workspace 运行状态或全部真机浏览器截图；必须区分 `main` 源码、GitHub Pages 已发布版本和 Modal 已部署版本。

## 2. 用户已明确的视觉偏好

- **保留深色背景**，特别关注夜间和 OLED 屏幕观看舒适度；不要又改成白底。
- 采用克制、专业的数据产品风格：稳定的视觉层级、合理留白、细分隔线、低眩光，不要夸张渐变、霓虹装饰或大量冗余卡片。
- 首要信息是日期、数据有效性、Top20 排名、近60日走势图和 forward 累计收益；不要让营销式大标题挤占榜单区域。
- 桌面端使用易读表格；手机端使用独立紧凑卡片，保持文字、tag、走势图不互相挤压，避免横向溢出。
- 风险提示需克制但完整：预测分数用于排序，不是保证收益；历史记录不得误标当前 winner。
- 优化时优先提供真实桌面和窄屏截图/可运行页面验证，不要只依靠测试字符串断言宣布“完美”。

## 3. 当前页面结构与数据路径

| 文件 | 职责 |
|---|---|
| `website/index.html` | 首页壳、CSS/JS/favicon 引用与缓存版本 |
| `website/app.js` | 信号 CSV/JSON 加载、日期切换、桌面表格/移动卡片、走势图、forward 收益模块 |
| `website/styles.css` | 深色设计 token、desktop/table、mobile/card、methodology 共用样式 |
| `website/favicon.svg` | 浅蓝 Q + 深色底的标签页图标 |
| `website/methodology.html` | 冻结 CSI1000 方法、Stage-B 证据、执行规则、forward paper 定义、风险与治理边界 |
| `chart_series.py` | Qlib bin 的 `start_index` 对齐最后 60 个交易日，并将缺失/无穷数变为 `null` |
| `forward_performance.py` | 从 winner paper execution reports 构造 **2026-10-12 起**的前向累计收益 |
| `scripts/build_forward_performance.py` | Pages build 期间聚合历史 paper artifacts，生成公开 performance JSON |
| `.github/workflows/website-deploy.yml` | `_site/` 静态站构建、HTML/JS/CSS/SVG 打包校验、Pages 发布 |

页面相对数据路径（重要）：

```text
signals/YYYY-MM-DD_top20_lgb158.csv
signals/YYYY-MM-DD_chart.json
signals/YYYY-MM-DD_paper_portfolio.json
signals/csi1000_forward_performance.json   # Pages build 产生
signals/code_name_map.csv
```

仓库内原始信号位于 `results/signals/`。不要误以为页面上的 `signals/` 是同一个物理目录；它是 Pages `_site/signals/` 部署路径。

## 4. 不可误改的数据展示规则

### 4.1 历史版本和 metadata

- 旧 `2026-10-08` 等记录可能是旧 lineage：如果 artifact 没有当前 winner 的明确标识，应显示**历史/未核验**，不假称 Stage-B winner。
- 缺少 `signal_data_date` 或 `model_fit_asof` 时显示缺失状态，不得用 usage date 自动冒充数据日。
- 不编辑或删除已存在的历史 signals，也不重新计算 Stage-B backtest 来填网页。

### 4.2 近60日走势

- Qlib bin 带有 global calendar `start_index`，必须对齐 **60 个真实交易日槽位**。停牌/短历史/缺失应保持 `null`，不得删掉后把剩余点平均拉伸。
- `website/app.js` 的 `calendarAlignChartValues` 仍需兼容旧长度不足的图表数组，正确填补缺失前缀。
- DOM 桌面 `<td>` 必须保持 `display: table-cell`，不要给通用 `.spark` 设置 `display:flex` 破坏列布局；移动卡片可以使用独立 flex 规则。
- 当前 sparkline 逻辑尺寸：桌面约 `128×34`、通常手机 `104×27`、很窄屏幕 `78×22`。这些是起点不是不可变的审美指标，若调整需视觉验证。
- 色彩语义注意 A 股惯例：上涨红、下跌绿；缺失数据不可把 `null` 当作 0 绘图。

### 4.3 模型累计收益

- forward-only inception 固定为 `2026-10-12`；不能拼接 Stage-B 回测、旧 baseline paper 或更早日期的历史收益。
- 2026-10-12 的**开盘前** paper 账户价值归一为 `1.0000`；每个交易日收盘价估值包含 paper 执行、成本及不可成交约束。
- 10/12 当日收盘价值通常需等待 10/13 数据更新后才确认。首笔有效执行报告前必须显示**待开始**，不能伪造 0% 或预测值。
- 数据生成源是 winner paper execution report。不要直接从模型 `score`、估计收益或持仓表计算累计收益。
- 如果数据暂缺、schema 不符、估值日期滞后，页面应有明确 empty/stale 状态，不悄悄输出错误净值。

### 4.4 股票池启用状态

- 只展示 CSI1000。旧 `?pool=chinext` 或残留历史文件不得重新开启公共入口。
- STAR、ChiNext 新模型若未来通过验证：先由**研究对话**完成 model/production gates，确认新 artifact contract；网页对话在明确授权后再启用新页面。
- 方法论文案不能把过去研究回测、独立 OOS 和实际 forward paper 收益混成同一个时间序列。

## 5. Pages 打包与上线检查清单

曾经出现过“源码有 CSS、线上完全白底”的真实事故：Pages 构建目录没有复制样式文件。现在 workflow 必须复制 `website/*.html`、`website/*.js`、`website/*.css`、`website/*.svg` 并校验 `index.html`、`app.js`、`methodology.html`、`styles.css`、`favicon.svg` 及生成的 performance JSON。

最低验收流程：

1. 检查 `main`、合并 PR、Pages workflow 的最新成功 run，确认部署版本与源码一致。
2. 跑 `node --check website/app.js`、`node --test tests/test_sparkline_frontend.cjs`、相关 Python/production-surface 测试。
3. 核验 `_site/` 中所有 CSS/JS/SVG/JSON 资源存在；线上资源 HTTP 正常返回，不只有 HTML 能打开。
4. 实际渲染检查至少 `1440px` desktop、`1024px` laptop、`768px` tablet、`390px` 手机、`320px` 窄屏；检查 Safari/Chrome 或可用浏览器对应视口。
5. 检查桌面 table 与手机 cards 只出现对应的一套；走势图列居中不拉伸，日期选择滚动、长代码和中文股票名不溢出。
6. 检查历史旧 lineage、数据缺失、无近60日点、无 forward 估值、负收益/正收益、加载失败等状态。
7. 修改网站资产时按需更新 index/methodology 的 CSS/JS favicon 资源版本，避免浏览器缓存旧版。

不要把“104+4 tests PASS”说成已经做完真实浏览器视觉验收；如果没有可用的浏览器工具，应明确说明，还可请用户提供真实页面截图作视觉验收。

## 6. 文件所有权和两个对话的协作

**本网页对话优先负责**：`website/**`、`.github/workflows/website-deploy.yml`、网页前端测试与纯展示性修复。

**研究对话优先负责**：STAR/ChiNext 模型候选、reproducibility、研究协议、卫星池算法与研究代码。

**共享文件需先协调**：`modal_qlib_cn_a10g.py`、`chart_series.py`、`paper_portfolio.py`、`forward_performance.py`、`scripts/build_forward_performance.py`、`results/signals/**`、通用 `.github/workflows/**`。

- 如果只需调整 UI，请不要触碰 Modal、模型、配置、cron、paper account；需要改 backend artifact schema 时先说明原因和兼容测试，再起独立 PR。
- 两个对话从各自最新 `main` 拉 topic branch；不要共用分支或无说明 cherry-pick。
- 不覆盖历史 signal artifacts，不引入访问 token、私有 workspace、账户信息到 public web 或仓库。
- 在开发时维护接口兼容说明，待研究对话明确通过 gate 再共同评估 STAR/ChiNext 是否重启网站入口。

## 7. 优先级和第一轮交付

1. **实际视觉验收**：先检查已发布版本的桌面与手机视图、文件加载/字体/列布局；记录可复现的具体问题。
2. **低风险打磨**：排名表格/移动卡片间距、数据日期状态、近60日图、收益面板的 empty/stale 呈现，优先一致性与可读性。
3. **方法论可读性**：保留正确研究口径与风险说明，不泄露内部审计 identifiers 或部署凭据。
4. **产出**：问题清单、改变前后截图（若能真实渲染）、测试结果和独立 website PR。除非得到明确授权，不改 production code。

## 8. 新对话第一条 Prompt（可直接复制）

> 请在 `AT2018cow/qlib` 仓库继续优化**公开网页的深色、桌面/手机响应式设计**。先阅读 `docs/experiments/23-web-ui-handoff-20261008.md`、`AGENTS.md`、`website/` 和 GitHub Pages workflow，检查最新 `main`、Actions 和实际发布文件。用户明确喜欢深色、低眩光、专业的金融数据终端，不希望白底和过度装饰。先对 1440/1024/768/390/320px 布局、近60日走势、favicon、历史版本标识和自 2026-10-12 起的 forward-only paper 累计收益做实际视觉与数据状态验收，再提出改进方案并单独提交网页 PR。保持 CSI1000-only，创业板和科创板暂不展示；不要修改模型、cron、paper accounting 或历史 artifacts。发现接口问题先列证据，与另一个模型研究对话协调。若没有实际浏览器渲染条件，请坦诚说明，不要声称已完成视觉验收。

---

本交接仅涉及数据展示与网站工程，不构成投资建议。
