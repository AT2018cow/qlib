// CSI1000 production signal dashboard.
// Historical/research pool artifacts remain in the repository but are not part
// of the public production surface.
const CSV_BASE = 'signals';
const DATE_INDEX_URL = CSV_BASE + '/available_dates.json';

const PRODUCTION = {
  pool: '中证1000',
  profile: 'Stage-B winner',
  model: 'LightGBM · Alpha158',
  target: '20 日前瞻收益',
  portfolio: 'Top20 · Drop 2',
  execution: 'T close → T+1 open',
  retrain: '每 20 个交易日',
};

const qlibCode = inst => inst.replace(/^(SH|SZ|BJ)/, '');
const sinaUrl = inst => `https://finance.sina.com.cn/realstock/company/${inst.toLowerCase()}/nc.shtml`;

async function fetchText(url, fresh = false) {
  try {
    // Revalidate changing snapshots; let the browser cache older history.
    const r = await fetch(url, { cache: fresh ? 'no-cache' : 'default' });
    if (!r.ok) return null;
    return r.text();
  } catch (_) {
    return null;
  }
}

function parseCSV(text) {
  if (!text) return [];
  const lines = text.trim().split('\n');
  const header = lines[0].split(',');
  return lines.slice(1).map(line => {
    const cols = line.split(',');
    const obj = {};
    header.forEach((h, i) => { obj[h.trim()] = (cols[i] || '').trim(); });
    return obj;
  });
}

function parseJSONLoose(text) {
  try { return JSON.parse(String(text).replace(/\bNaN\b/g, 'null')); }
  catch (_) { return null; }
}

function formatDate(date) {
  if (!date) return '—';
  const parts = date.split('-');
  return parts.length === 3 ? `${parts[0]}.${parts[1]}.${parts[2]}` : date;
}

function formatScore(value) {
  const n = Number(value) * 100;
  if (!Number.isFinite(n)) return { text: '—', klass: '' };
  return {
    text: `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`,
    klass: n >= 0 ? 'positive' : 'negative',
  };
}

function calendarAlignChartValues(values, dates) {
  if (!Array.isArray(values)) return [];
  const count = Array.isArray(dates) ? dates.length : 0;
  if (!count) return values;
  // Before the calendar-aware producer was deployed, shorter chart arrays
  // represented the most recent available closes. Preserve their missing
  // leading calendar slots instead of stretching them over the full window.
  if (values.length < count) {
    return Array(count - values.length).fill(null).concat(values);
  }
  return values.slice(-count);
}

function makeSparkline(values, w = 128, h = 34) {
  const raw = Array.isArray(values) ? values : [];
  if (raw.length < 2) return '<span class="muted" title="近60日有效收盘价不足，无法绘图">—</span>';

  const finite = raw
    .map((v, i) => ({ i, v: Number(v) }))
    .filter(p => raw[p.i] !== null && raw[p.i] !== undefined && Number.isFinite(p.v));
  if (finite.length < 2) return '<span class="muted" title="近60日有效收盘价不足，无法绘图">—</span>';

  const min = Math.min(...finite.map(p => p.v));
  const max = Math.max(...finite.map(p => p.v));
  const range = max - min || 1;
  const xPad = 2;
  const yPad = 2;
  const usableW = Math.max(1, w - xPad * 2);
  const usableH = Math.max(1, h - yPad * 2);
  const xOf = i => xPad + (raw.length === 1 ? 0 : i / (raw.length - 1) * usableW);
  const yOf = v => yPad + (max - v) / range * usableH;

  // Preserve null/suspension gaps instead of deleting them and compressing time.
  const segments = [];
  let current = [];
  raw.forEach((value, i) => {
    const n = Number(value);
    if (value === null || value === undefined || !Number.isFinite(n)) {
      if (current.length >= 2) segments.push(current);
      current = [];
      return;
    }
    current.push([xOf(i), yOf(n)]);
  });
  if (current.length >= 2) segments.push(current);

  if (!segments.length) return '<span class="muted" title="有效价格不连续，无法形成趋势线">—</span>';
  const stroke = finite[finite.length - 1].v >= finite[0].v ? '#ff6b6b' : '#3ccf91';
  const paths = segments.map(seg => {
    const d = seg.map((p, i) => `${i ? 'L' : 'M'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round"/>`;
  }).join('');

  return `<svg class="sparkline-svg" viewBox="0 0 ${w} ${h}" aria-hidden="true">${paths}</svg>`;
}

async function loadAvailableDates() {
  // Pages publishes actual dates; never probe nonexistent calendar weekdays.
  const manifest = parseJSONLoose(await fetchText(DATE_INDEX_URL, true));
  if (!manifest || manifest.version !== 1 || !Array.isArray(manifest.dates) ||
      !manifest.dates.length || manifest.dates.length > 25 ||
      !manifest.names || typeof manifest.names !== 'object' || Array.isArray(manifest.names) ||
      manifest.dates.some((day, i) =>
        !Number.isFinite(dateKeyToUTC(day)) ||
        (i > 0 && manifest.dates[i - 1] <= day))) {
    return null;
  }
  return { dates: manifest.dates.map(date => ({ date })), nameMap: manifest.names };
}

async function loadDateContext(date, fresh = false) {
  const [chartText, paperText] = await Promise.all([
    fetchText(CSV_BASE + '/' + date + '_chart.json', fresh),
    fetchText(CSV_BASE + '/' + date + '_paper_portfolio.json', fresh),
  ]);
  return { chart: parseJSONLoose(chartText), paper: parseJSONLoose(paperText) };
}

// Frontend trust boundary: only a validated forward-only artifact may
// produce a numerical return. Never silently turn missing/null data into 0%.
const FORWARD_START = '2026-10-12';

function dateKeyToUTC(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
  const time = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : NaN;
}

function normalizeForwardPerformance(raw) {
  const invalid = { status: 'invalid' };
  if (!raw || raw.start_date !== FORWARD_START || !Array.isArray(raw.points)) return invalid;
  if (raw.status === 'awaiting_first_valuation') {
    return raw.points.length === 0 && raw.latest_date == null && raw.cumulative_return == null
      ? raw : invalid;
  }
  if (raw.status !== 'active' || raw.points.length === 0 ||
      raw.points[0]?.date !== FORWARD_START) return invalid;
  let previousDate = '';
  let previousNav = 1;
  for (const point of raw.points) {
    if (!point || !Number.isFinite(dateKeyToUTC(point.date)) ||
        point.date < FORWARD_START || point.date <= previousDate ||
        typeof point.nav !== 'number' || !Number.isFinite(point.nav) || point.nav <= 0 ||
        typeof point.daily_return !== 'number' || !Number.isFinite(point.daily_return) ||
        typeof point.cumulative_return !== 'number' || !Number.isFinite(point.cumulative_return) ||
        Math.abs(point.nav - 1 - point.cumulative_return) > 1e-6 ||
        Math.abs(point.daily_return - (point.nav / previousNav - 1)) > 1e-6) {
      return invalid;
    }
    previousDate = point.date;
    previousNav = point.nav;
  }
  const last = raw.points[raw.points.length - 1];
  if (raw.latest_date !== last.date || typeof raw.cumulative_return !== 'number' ||
      !Number.isFinite(raw.cumulative_return) ||
      Math.abs(raw.cumulative_return - last.cumulative_return) > 1e-8) return invalid;
  return raw;
}

function isPossiblyStale(latestDate, now = new Date()) {
  const latest = dateKeyToUTC(latestDate);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  // Calendar-day heuristic only: weekends and market holidays can delay updates.
  return Number.isFinite(latest) && today - latest > 7 * 86400000;
}

async function loadForwardPerformance() {
  const text = await fetchText(CSV_BASE + '/csi1000_forward_performance.json', true);
  if (text === null) return { status: 'unavailable' };
  return normalizeForwardPerformance(parseJSONLoose(text));
}

function formatReturn(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const pct = n * 100;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(digits)}%`;
}

function makePerformanceChart(points, w = 760, h = 180) {
  const rows = Array.isArray(points) ? points : [];
  if (!rows.length) {
    return '<div class="performance-empty">从 2026.10.12 开始记录，首个收盘估值将在下一次数据更新后显示。</div>';
  }

  if (rows.some(p => !p || typeof p.cumulative_return !== 'number' || !Number.isFinite(p.cumulative_return))) {
    return '<div class="performance-empty">估值点异常，无法绘制收益曲线。</div>';
  }
  const values = [0, ...rows.map(p => p.cumulative_return)];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.18, 0.005);
  const lo = min - pad;
  const hi = max + pad;
  const range = hi - lo || 1;
  const xPad = 18;
  const yPad = 18;
  const usableW = w - xPad * 2;
  const usableH = h - yPad * 2;
  const series = values;
  const pts = series.map((v, i) => {
    const x = xPad + (series.length === 1 ? 0 : i / (series.length - 1) * usableW);
    const y = yPad + (hi - v) / range * usableH;
    return [x, y];
  });
  const path = pts.map((p, i) => `${i ? 'L' : 'M'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const zeroY = yPad + (hi - 0) / range * usableH;
  const last = pts[pts.length - 1];
  const positive = series[series.length - 1] >= 0;

  return `
    <svg class="performance-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="模型累计收益曲线">
      <line x1="${xPad}" y1="${zeroY.toFixed(1)}" x2="${w - xPad}" y2="${zeroY.toFixed(1)}" class="performance-zero"/>
      <path d="${path}" class="performance-line ${positive ? 'gain' : 'loss'}"/>
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4" class="performance-dot ${positive ? 'gain' : 'loss'}"/>
    </svg>
    <div class="performance-axis"><span>2026.10.12 · 起点</span><span>${formatDate(rows[rows.length - 1].date)} · 最近估值</span></div>
  `;
}

function renderForwardPerformance(data) {
  const value = document.getElementById('forward-return');
  const latest = document.getElementById('forward-latest');
  const chart = document.getElementById('forward-chart');
  const status = document.getElementById('forward-status');
  if (!value || !latest || !chart || !status) return;

  value.className = 'performance-value';
  latest.className = 'performance-latest';
  status.className = 'performance-status';
  if (data?.status !== 'active') {
    const unavailable = data?.status === 'unavailable';
    const invalid = data?.status === 'invalid';
    value.textContent = unavailable ? '暂不可用' : invalid ? '数据异常' : '待开始';
    latest.textContent = unavailable ? '收益文件加载失败' :
      invalid ? '估值数据校验未通过' : '尚无收盘估值';
    status.textContent = 'Forward tracking · 2026.10.12 起';
    const message = unavailable ? '暂时无法读取累计收益数据，请稍后重试。' :
      invalid ? '累计收益数据不完整或不符合 forward-only 约定，已停止展示。' :
      '从 2026.10.12 开始记录，首个收盘估值将在下一次数据更新后显示。';
    chart.innerHTML = '<div class="performance-empty" role="status">' + message + '</div>';
    return;
  }

  const stale = isPossiblyStale(data.latest_date);
  value.textContent = formatReturn(data.cumulative_return);
  value.className = 'performance-value ' + (data.cumulative_return >= 0 ? 'gain' : 'loss');
  latest.textContent = '最新估值 ' + formatDate(data.latest_date) + (stale ? ' · 可能滞后' : '');
  if (stale) latest.classList.add('stale');
  status.textContent = 'Forward tracking · ' + formatDate(data.start_date) + ' 起' +
    (stale ? ' · 请核对估值时效' : '');
  if (stale) status.classList.add('stale');
  chart.innerHTML = makePerformanceChart(data.points);
}

function isWinnerCanonical(paper) {
  return Boolean(
    paper &&
    paper.paper_lineage === 'stage_b_winner_canonical' &&
    paper.production_lineage &&
    paper.production_lineage.profile === 'stage_b_winner'
  );
}

function buildRows(rows, nameMap, prev, chartData) {
  const prevCodes = new Set(prev ? prev.map(r => r.instrument) : []);
  const prevRank = {};
  if (prev) prev.forEach(r => { prevRank[r.instrument] = Number(r.rank); });

  return rows.map(r => {
    const rank = Number(r.rank);
    let tag = prev ? '<span class="tag tag-hold">持有</span>' : '<span class="tag tag-hold">无对照</span>';
    if (prev && !prevCodes.has(r.instrument)) tag = '<span class="tag tag-new">新进</span>';
    else if (prev && rank < prevRank[r.instrument]) tag = '<span class="tag tag-up">上升</span>';
    else if (prev && rank > prevRank[r.instrument]) tag = '<span class="tag tag-dn">下降</span>';

    return {
      rank,
      instrument: r.instrument,
      name: nameMap[qlibCode(r.instrument)] || '名称待更新',
      score: formatScore(r.score),
      tag,
      spark: makeSparkline(calendarAlignChartValues(
        chartData?.stocks?.[r.instrument],
        chartData?.dates
      )),
      url: sinaUrl(r.instrument),
    };
  });
}

function tableRows(rows) {
  return rows.map(r => `
    <tr>
      <td><span class="rank-badge${r.rank <= 3 ? ' top' : ''}">${r.rank}</span></td>
      <td><a class="stock-link" href="${r.url}" target="_blank" rel="noopener">${r.instrument}</a></td>
      <td class="stock-name">${r.name}</td>
      <td><span class="score ${r.score.klass}">${r.score.text}</span></td>
      <td>${r.tag}</td>
      <td class="spark">${r.spark}</td>
    </tr>
  `).join('');
}

function mobileCards(rows) {
  return rows.map(r => `
    <article class="stock-card">
      <span class="rank-badge${r.rank <= 3 ? ' top' : ''}">${r.rank}</span>
      <div class="stock-main">
        <a class="stock-link" href="${r.url}" target="_blank" rel="noopener">${r.instrument}</a>
        <span class="stock-name">${r.name}</span>
      </div>
      <div class="stock-side">
        <div><span class="score ${r.score.klass}">${r.score.text}</span> ${r.tag}</div>
        <div class="spark">${r.spark}</div>
      </div>
    </article>
  `).join('');
}

function pageShell(dates) {
  const history = dates.slice(0, 24).map((d, i) =>
    `<button class="date-btn${i === 0 ? ' current' : ''}" aria-pressed="${i === 0}" data-date="${d.date}">${formatDate(d.date)}</button>`
  ).join('');

  return `
    <main class="shell">
      <nav class="topbar">
        <a class="brand" href="./">
          <span class="brand-mark">Q</span>
          <span>Qlib Signal Lab</span>
        </a>
        <div class="topnav">
          <a class="nav-link" href="#history">历史</a>
          <a class="nav-link" href="methodology.html">方法论</a>
        </div>
      </nav>

      <section class="hero">
        <div class="hero-copy">
          <div class="eyebrow"><span class="status-dot"></span> CSI1000 Production</div>
          <h1>中证1000<span>每日选股信号</span></h1>
          <p class="hero-lede">
            基于已冻结的 Stage-B 模型，每日展示中证1000预测排名。
            可查询历史榜单、数据截止日与模型版本。信号于 T 日收盘形成，供 T+1 开盘使用。
          </p>
        </div>
        <aside class="protocol-card">
          <h2>当前生产协议</h2>
          <div class="protocol-list">
            <div class="protocol-row"><span>模型</span><strong>${PRODUCTION.model}</strong></div>
            <div class="protocol-row"><span>目标</span><strong>${PRODUCTION.target}</strong></div>
            <div class="protocol-row"><span>组合</span><strong>${PRODUCTION.portfolio}</strong></div>
            <div class="protocol-row"><span>执行</span><strong>${PRODUCTION.execution}</strong></div>
            <div class="protocol-row"><span>重训</span><strong>${PRODUCTION.retrain}</strong></div>
          </div>
        </aside>
      </section>

      <section class="status-grid" aria-label="信号状态">
        <div class="stat-card">
          <div class="stat-label">榜单日期</div>
          <div class="stat-value" id="stat-date">—</div>
          <div class="stat-sub">signal usage date</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">数据截止</div>
          <div class="stat-value" id="stat-data-date">—</div>
          <div class="stat-sub">signal data date</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">模型拟合日</div>
          <div class="stat-value" id="stat-fit-date">—</div>
          <div class="stat-sub">model fit as-of</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">记录状态</div>
          <div class="stat-value" id="stat-lineage">—</div>
          <div class="stat-sub" id="stat-lineage-sub">版本说明</div>
        </div>
      </section>

      <section class="panel" id="history">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Archive</div>
            <h2>历史榜单</h2>
          </div>
          <div class="panel-note">只读存档 · 按日期查看</div>
        </div>
        <div class="history-wrap">
          <div id="history-buttons" class="history-buttons">${history}</div>
        </div>
      </section>

      <section class="panel performance-panel" id="performance">
        <div class="performance-head">
          <div>
            <div class="panel-kicker">Forward performance</div>
            <h2>模型累计收益</h2>
            <p id="forward-status" class="performance-status">Forward tracking · 2026.10.12 起</p>
          </div>
          <div class="performance-summary">
            <div id="forward-return" class="performance-value">待开始</div>
            <div id="forward-latest" class="performance-latest">尚无收盘估值</div>
          </div>
        </div>
        <div id="forward-chart" class="performance-chart">
          <div class="performance-empty">从 2026.10.12 开始记录，首个收盘估值将在下一次数据更新后显示。</div>
        </div>
        <div class="performance-foot">
          <span>起点：2026.10.12 开盘前账户价值 = 1.0000</span>
          <span>按 paper 账户真实调仓、交易成本与不可成交约束计算 · 每日收盘估值</span>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Daily ranking</div>
            <h2>Top 20</h2>
          </div>
          <div class="panel-note">
            <div id="artifact-status"></div>
            <div class="ranking-note">预测分数用于排序，不构成收益承诺</div>
          </div>
        </div>
        <div class="table-wrap">
          <table class="signal-table">
            <thead>
              <tr><th>Rank</th><th>代码</th><th>名称</th><th>20日预测</th><th>变化</th><th>近60日</th></tr>
            </thead>
            <tbody id="signal-rows"></tbody>
          </table>
        </div>
        <div id="mobile-cards" class="mobile-cards"></div>
      </section>

      <div class="callout">
        <strong>当前范围：</strong>生产页面仅展示 CSI1000。创业板旧模型的每日更新与公开展示已暂停，
        历史研究结果仍保留；待新模型完成独立验证并获准恢复后再展示。
      </div>

      <footer class="footer">
        <span>仅供研究与工程验证，不构成投资建议。</span>
        <span><a href="methodology.html">查看完整方法论与 Stage-B 证据 →</a></span>
      </footer>
    </main>
  `;
}

async function main() {
  const app = document.getElementById('app');
  // Forward status is independent and must not delay the ranking.
  const forwardPromise = loadForwardPerformance();
  const index = await loadAvailableDates();

  if (!index) {
    app.innerHTML = '<main class="shell"><nav class="topbar"><a class="brand" href="./">Qlib Signal Lab</a><a href="methodology.html">方法论</a></nav><div class="callout" role="alert"><strong>日期索引暂不可用。</strong> 无法确认最新 CSI1000 榜单，请稍后刷新。不会以猜测的交易日或过期资料代替生产信号。</div></main>';
    return;
  }

  const { dates, nameMap } = index;
  app.innerHTML = pageShell(dates);
  forwardPromise.then(renderForwardPerformance);

  // One CSV fetch per date per session, including rank comparisons.
  const csvCache = new Map();
  function loadDateRows(date) {
    if (!csvCache.has(date)) {
      csvCache.set(date, fetchText(CSV_BASE + '/' + date + '_top20_lgb158.csv',
        date === dates[0].date).then(parseCSV));
    }
    return csvCache.get(date);
  }

  const contextCache = new Map();
  function loadContext(date) {
    if (!contextCache.has(date)) {
      contextCache.set(date, loadDateContext(date, date === dates[0].date));
    }
    return contextCache.get(date);
  }

  let latestDateRequest = 0;
  async function renderDate(dateInfo) {
    const request = ++latestDateRequest;
    const container = document.querySelector('.signal-table');
    if (container) container.setAttribute('aria-busy', 'true');
    document.getElementById('artifact-status').textContent = '正在加载日期记录…';
    const idx = dates.findIndex(d => d.date === dateInfo.date);
    const previousPromise = idx >= 0 && idx + 1 < dates.length
      ? loadDateRows(dates[idx + 1].date).then(previous => previous.length ? previous : null)
      : Promise.resolve(null);
    const ctxPromise = loadContext(dateInfo.date);
    const rows = await loadDateRows(dateInfo.date);
    if (request !== latestDateRequest) return;

    if (!rows.length) {
      document.getElementById('signal-rows').innerHTML = '';
      document.getElementById('mobile-cards').innerHTML = '';
      document.getElementById('artifact-status').textContent = '该日信号文件无法读取';
      document.getElementById('stat-date').textContent = formatDate(dateInfo.date);
      document.getElementById('stat-data-date').textContent = '—';
      document.getElementById('stat-fit-date').textContent = '—';
      document.getElementById('stat-lineage').textContent = '未核验';
      document.getElementById('stat-lineage-sub').textContent = '信号文件缺失或读取失败';
      if (container) container.setAttribute('aria-busy', 'false');
      return;
    }

    function paint(previous, ctx) {
      if (request !== latestDateRequest) return;
      const built = buildRows(rows, nameMap, previous, ctx?.chart);
      document.getElementById('signal-rows').innerHTML = tableRows(built);
      document.getElementById('mobile-cards').innerHTML = mobileCards(built);
      document.getElementById('stat-date').textContent = formatDate(dateInfo.date);
      const paper = ctx?.paper;
      document.getElementById('stat-data-date').textContent = formatDate(paper?.signal_data_date || null);
      document.getElementById('stat-fit-date').textContent = formatDate(paper?.model_fit_asof || null);
      const winner = isWinnerCanonical(paper);
      document.getElementById('stat-lineage').textContent =
        ctx ? (winner ? 'Stage-B winner' : '历史 / 未核验') : '核验中';
      document.getElementById('stat-lineage-sub').textContent = ctx
        ? (winner ? '当前生产版本' : '历史记录未包含当前版本标识') : '正在加载版本资料';
      document.getElementById('artifact-status').innerHTML = !ctx
        ? '预测排名已载入 · 正在获取走势图与版本资料'
        : (winner
          ? '<span class="status-pill"><span class="status-dot"></span> Stage-B winner · 已核验</span>'
          : '<span class="status-pill history">历史记录 / 版本未确认</span>');
      document.querySelectorAll('.date-btn').forEach(btn => {
        const active = btn.dataset.date === dateInfo.date;
        btn.classList.toggle('current', active);
        btn.setAttribute('aria-pressed', String(active));
      });
    }

    // Show real Top20 as soon as its small CSV arrives; metadata is pending.
    // The interim "无对照" tag is never misrepresented as a new position.
    paint(null, null);
    const [previous, ctx] = await Promise.all([previousPromise, ctxPromise]);
    if (request !== latestDateRequest) return;
    paint(previous, ctx);
    if (container) container.setAttribute('aria-busy', 'false');
  }

  document.querySelectorAll('.date-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const d = dates.find(item => item.date === btn.dataset.date);
      if (d) renderDate(d);
    });
  });

  renderDate(dates[0]);
}

main();
