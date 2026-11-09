// CSI1000 production signal dashboard.
// Historical/research pool artifacts remain in the repository but are not part
// of the public production surface.
const CSV_BASE = 'signals';
const DATE_INDEX_URL = CSV_BASE + '/available_dates.json';

const POOLS = {
  csi300: {
    key: 'csi300',
    label: '沪深300',
    code: '000300',
    expectedPitCount: 300,
    horizonDays: 10,
    topk: 10,
    nd: 1,
    indexUrl: CSV_BASE + '/available_dates_csi300.json',
    forwardUrl: CSV_BASE + '/csi300_forward_performance.json',
    suffix: '_csi300',
  },
  csi500: {
    key: 'csi500',
    label: '中证500',
    code: '000905',
    expectedPitCount: 500,
    horizonDays: 20,
    topk: 20,
    nd: 2,
    indexUrl: CSV_BASE + '/available_dates_csi500.json',
    forwardUrl: CSV_BASE + '/csi500_forward_performance.json',
    suffix: '_csi500',
  },
  csi1000: {
    key: 'csi1000',
    label: '中证1000',
    code: '000852',
    expectedPitCount: 1000,
    horizonDays: 20,
    topk: 10,
    nd: 1,
    indexUrl: DATE_INDEX_URL,
    forwardUrl: CSV_BASE + '/csi1000_forward_performance.json',
    suffix: '',
  },
};

const POOL_ORDER = ['csi300', 'csi500', 'csi1000'];
const POOL_STORAGE_KEY = 'qlib_signal_pool';

function getSavedPool() {
  try {
    const saved = typeof window !== 'undefined' && window.localStorage
      ? window.localStorage.getItem(POOL_STORAGE_KEY)
      : null;
    if (saved && POOLS[saved]) return POOLS[saved];
  } catch (_) {}
  return POOLS.csi300;
}

function savePool(poolKey) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(POOL_STORAGE_KEY, poolKey);
    }
  } catch (_) {}
}

let currentPool = getSavedPool();

const PRODUCTION = {
  model: 'LightGBM · Alpha158 量价模型',
};

function portfolioLabel(pool) {
  return `等权 ${pool.topk} 只 · 单日最多换 ${pool.nd} 只`;
}

const qlibCode = inst => inst.replace(/^(SH|SZ|BJ)/, '');
const thsUrl = inst => `https://stockpage.10jqka.com.cn/${qlibCode(inst)}/`;

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
  if (lines.length > 201) return [];
  const header = lines[0].split(',').map(h => h.trim());
  if (!['rank', 'instrument', 'score'].every(h => header.includes(h))) return [];
  const rows = [];
  const seen = new Set();
  for (const line of lines.slice(1)) {
    const cols = line.split(',');
    const obj = {};
    header.forEach((h, i) => { obj[h] = (cols[i] || '').trim(); });
    const rank = Number(obj.rank);
    const score = Number(obj.score);
    // Fail closed on any malformed row: symbol format, contiguous 1..N integer
    // ranks, unique instruments, finite scores, bounded rows.
    if (!SIGNAL_INSTRUMENT.test(obj.instrument) ||
        !Number.isInteger(rank) || rank !== rows.length + 1 ||
        !Number.isFinite(score) || seen.has(obj.instrument)) {
      return [];
    }
    seen.add(obj.instrument);
    rows.push(obj);
  }
  return rows;
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
  // Raw regression output on the N-day return label.  It is an ordering
  // score, not a calibrated expected return, so never render it as a percent.
  const n = Number(value);
  if (!Number.isFinite(n)) return { text: '—', klass: '' };
  return {
    text: `${n.toFixed(3)}`,
    klass: n >= 0 ? 'positive' : 'negative',
  };
}

// R04 trust boundary: third-party names and CSV cells are rendered as text or
// rejected outright; nothing fetched from the network may become live HTML.
const SIGNAL_INSTRUMENT = /^(SH|SZ|BJ)\d{6}$/;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function sanitizeNameMap(raw) {
  const names = {};
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return names;
  for (const [code, name] of Object.entries(raw)) {
    if (!/^\d{6}$/.test(code) || typeof name !== 'string') continue;
    if (!name || name.length > 40 || /[<>&"'\u0000-\u001f]/.test(name)) continue;
    names[code] = name;
  }
  return names;
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

// Visible on touchscreens too; title only is not an accessible empty state.
function sparklineEmpty(state) {
  const states = {
    loading: ['走势加载中', '请稍候', '正在读取近60日行情'],
    unavailable: ['走势不可用', '暂无走势', '近60日走势图文件不可用'],
    insufficient: ['有效数据不足', '暂无走势', '近60日有效收盘价不足两笔'],
    discontinuous: ['价格不连续', '暂无走势', '近60日价格记录没有连续的有效线段'],
  };
  const [label, detail, title] = states[state];
  return `<span class="spark-empty" title="${title}"><span>${label}</span><span>${detail}</span></span>`;
}

function makeSparkline(values, w = 128, h = 34) {
  const raw = Array.isArray(values) ? values : [];
  if (raw.length < 2) return sparklineEmpty('insufficient');

  const finite = raw
    .map((v, i) => ({ i, v: Number(v) }))
    .filter(p => raw[p.i] !== null && raw[p.i] !== undefined && Number.isFinite(p.v));
  if (finite.length < 2) return sparklineEmpty('insufficient');

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

  if (!segments.length) return sparklineEmpty('discontinuous');
  const stroke = finite[finite.length - 1].v >= finite[0].v ? '#ff6b6b' : '#3ccf91';
  const paths = segments.map(seg => {
    const d = seg.map((p, i) => `${i ? 'L' : 'M'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round"/>`;
  }).join('');

  return `<svg class="sparkline-svg" viewBox="0 0 ${w} ${h}" aria-hidden="true">${paths}</svg>`;
}

async function loadAvailableDates(pool = currentPool) {
  // Pages publishes actual dates; never probe nonexistent calendar weekdays.
  const manifest = parseJSONLoose(await fetchText(pool.indexUrl, true));
  if (!manifest || manifest.version !== 1 || !Array.isArray(manifest.dates) ||
      !manifest.dates.length || manifest.dates.length > 25 ||
      !manifest.names || typeof manifest.names !== 'object' || Array.isArray(manifest.names) ||
      manifest.dates.some((day, i) =>
        !Number.isFinite(dateKeyToUTC(day)) ||
        (i > 0 && manifest.dates[i - 1] <= day))) {
    return null;
  }
  return { dates: manifest.dates.map(date => ({ date })), nameMap: sanitizeNameMap(manifest.names) };
}

async function loadDateContext(date, fresh = false, pool = currentPool) {
  const sfx = pool.suffix;
  const [chartText, paperText, metadataText] = await Promise.all([
    fetchText(CSV_BASE + '/' + date + (sfx ? '_chart' + sfx + '.json' : '_chart.json'), fresh),
    fetchText(CSV_BASE + '/' + date + (sfx ? '_paper_portfolio' + sfx + '.json' : '_paper_portfolio.json'), fresh),
    fetchText(CSV_BASE + '/' + date + (sfx ? '_mvp_metadata' + sfx + '.json' : '_mvp_metadata.json'), fresh),
  ]);
  return {
    chart: parseJSONLoose(chartText),
    paper: parseJSONLoose(paperText),
    metadata: parseJSONLoose(metadataText),
  };
}

// Frontend trust boundary: only a validated forward-only artifact may
// produce a numerical return. Never silently turn missing/null data into 0%.
const FORWARD_START = '2026-10-08';

function dateKeyToUTC(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
  const time = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : NaN;
}

function normalizeWebsiteSimulation(raw, market = null) {
  const invalid = { status: 'invalid' };
  const identity = raw.account_identity;
  const pool = POOLS[raw.market];
  if (!pool || (market && market !== raw.market) || !identity || identity.market !== raw.market ||
      identity.initial_cash !== 1000000 || identity.topk !== pool.topk || identity.nd !== pool.nd ||
      identity.horizon !== pool.horizonDays || raw.initial_cash !== 1000000 || raw.initial_nav !== 1 ||
      raw.status !== 'active' || raw.data_mode !== 'CAUSAL_SIMULATION' ||
      raw.execution_st_policy !== 'ST_HISTORY_UNVERIFIED_REFERENCE_ONLY' ||
      raw.start_date !== FORWARD_START || raw.inception_date !== FORWARD_START ||
      typeof raw.source_manifest_sha256 !== 'string' || typeof raw.provider_archive_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(raw.source_manifest_sha256 || '') ||
      !/^[0-9a-f]{64}$/.test(raw.provider_archive_sha256 || '') ||
      !Array.isArray(raw.points) || !raw.points.length || !Array.isArray(raw.trading_dates) ||
      raw.trading_dates[0] !== FORWARD_START ||
      JSON.stringify(raw.trading_dates) !== JSON.stringify(raw.points.map(p => p?.date))) return invalid;
  const profiles = { csi300: '59b93929_top10_drop1_10d',
    csi500: 'raw10d_c98856b4_top20_drop2_20d', csi1000: 'c98856b4_top10_drop1' };
  if (identity.profile !== profiles[raw.market] ||
      identity.paper_lineage !== profiles[raw.market] + '_website_oct08_v2') return invalid;
  let previousNav = 1, previousDate = '', fees = 0;
  for (const p of raw.points) {
    if (!p || !Number.isFinite(dateKeyToUTC(p.date)) || p.date <= previousDate ||
        p.data_mode !== 'CAUSAL_SIMULATION' ||
        ['nav', 'daily_return', 'cumulative_return', 'portfolio_value_close', 'cost', 'cash']
          .some(k => typeof p[k] !== 'number' || !Number.isFinite(p[k])) ||
        p.nav <= 0 || p.cost < 0 || p.cash < -0.01 ||
        Math.abs(p.portfolio_value_close / 1000000 - p.nav) > 1e-9 ||
        Math.abs(p.nav - 1 - p.cumulative_return) > 1e-9 ||
        Math.abs(p.nav / previousNav - 1 - p.daily_return) > 1e-9) return invalid;
    previousNav = p.nav; previousDate = p.date; fees += p.cost;
  }
  if (raw.latest_date !== previousDate || typeof raw.cumulative_return !== 'number' ||
      !Number.isFinite(raw.cumulative_return) || Math.abs(raw.cumulative_return - previousNav + 1) > 1e-9 ||
      typeof raw.total_fees !== 'number' || !Number.isFinite(raw.total_fees) ||
      Math.abs(raw.total_fees - fees) > 0.011) return invalid;
  const receiptVersion = raw.fit_receipt_version === undefined ? 1 : raw.fit_receipt_version;
  const fits = raw.fit_receipts;
  if (![1, 2].includes(receiptVersion) || !Array.isArray(fits) || !fits.length ||
      fits[0]?.fit_asof !== '2026-09-30') return invalid;
  let previousFitEnd = '';
  for (const fit of fits) {
    const contract = fit?.training_contract;
    if (!fit || !contract || fit.profile !== identity.profile || contract.horizon !== identity.horizon ||
        contract.asof !== fit.fit_asof ||
        typeof fit.model_sha256 !== 'string' || typeof fit.data_prefix_sha256 !== 'string' ||
        !/^[0-9a-f]{64}$/.test(fit.model_sha256 || '') ||
        !/^[0-9a-f]{64}$/.test(fit.data_prefix_sha256 || '') ||
        [fit.fit_asof, fit.signal_end, contract.train_end, contract.valid_end]
          .some(day => !Number.isFinite(dateKeyToUTC(day))) ||
        !(contract.train_end < contract.valid_end && contract.valid_end < fit.fit_asof &&
          fit.fit_asof <= fit.signal_end && fit.signal_end <= raw.latest_date) ||
        (previousFitEnd && fit.fit_asof <= previousFitEnd)) return invalid;
    previousFitEnd = fit.signal_end;
  }
  for (let i = 0; i < raw.points.length; i++) {
    const signalDate = i === 0 ? '2026-09-30' : raw.points[i - 1].date;
    const covering = fits.filter(fit => fit.fit_asof <= signalDate && signalDate <= fit.signal_end);
    if (covering.length !== 1) return invalid;
    const point = raw.points[i], fit = covering[0];
    if (receiptVersion === 2 && (point.signal_date !== signalDate || point.fit_asof !== fit.fit_asof ||
        point.model_sha256 !== fit.model_sha256)) return invalid;
  }
  return raw;
}

function normalizeForwardPerformance(raw, market = null) {
  const invalid = { status: 'invalid' };
  if (raw?.schema_version === 3) return normalizeWebsiteSimulation(raw, market);
  // Public market panels require the approved October account. The legacy
  // parser below is an explicit historical reader, never an automatic fallback.
  if (market) return invalid;
  const allowedStart = raw && raw.start_date === '2026-09-30';
  if (!allowedStart || !Array.isArray(raw.points)) return invalid;
  const stModes = ['ST_IGNORED_REFERENCE_ONLY', 'ST_VERIFIED_PREOPEN'];
  const segmented = raw.schema_version === 2;
  if (raw.schema_version != null && !segmented) return invalid;
  if (segmented && (raw.data_mode !== 'BACKFILL_PLUS_LIVE' || !raw.account_identity)) return invalid;
  if (raw.execution_st_policy != null &&
      ![...stModes, 'MIXED_ST_MODES'].includes(raw.execution_st_policy)) return invalid;
  if (raw.status === 'awaiting_first_valuation') {
    return raw.points.length === 0 && raw.latest_date == null && raw.cumulative_return == null
      ? raw : invalid;
  }
  if (raw.status !== 'active' || raw.points.length === 0 ||
      raw.points[0]?.date < raw.start_date ||
      raw.inception_date !== raw.points[0]?.date) return invalid;
  let previousDate = '';
  let previousNav = 1;
  const observedModes = new Set();
  const fallbackDates = [];
  for (const point of raw.points) {
    if (!point || !Number.isFinite(dateKeyToUTC(point.date)) ||
        point.date < raw.start_date || point.date <= previousDate ||
        typeof point.nav !== 'number' || !Number.isFinite(point.nav) || point.nav <= 0 ||
        typeof point.daily_return !== 'number' || !Number.isFinite(point.daily_return) ||
        typeof point.cumulative_return !== 'number' || !Number.isFinite(point.cumulative_return) ||
        Math.abs(point.nav - 1 - point.cumulative_return) > 1e-6 ||
        Math.abs(point.daily_return - (point.nav / previousNav - 1)) > 1e-6) {
      return invalid;
    }
    if (segmented) {
      if (point.data_mode === 'BACKFILL_RESEARCH') {
        if (point.execution_st_policy != null || point.date > raw.backfill_end_date) return invalid;
      } else if (point.data_mode === 'LIVE_PAPER') {
        if (!stModes.includes(point.execution_st_policy) || point.date <= raw.backfill_end_date) return invalid;
        if (point.execution_st_policy === 'ST_IGNORED_REFERENCE_ONLY') fallbackDates.push(point.date);
      } else return invalid;
    } else if (raw.execution_st_policy != null) {
      if (!stModes.includes(point.execution_st_policy)) return invalid;
      observedModes.add(point.execution_st_policy);
      if (point.execution_st_policy === 'ST_IGNORED_REFERENCE_ONLY') {
        fallbackDates.push(point.date);
      }
    } else if (point.execution_st_policy != null) {
      return invalid;
    }
    previousDate = point.date;
    previousNav = point.nav;
  }
  if (segmented && (!Array.isArray(raw.st_fallback_dates) ||
      JSON.stringify(raw.st_fallback_dates) !== JSON.stringify(fallbackDates))) return invalid;
  if (raw.execution_st_policy != null) {
    const actualMode = observedModes.size === 2
      ? 'MIXED_ST_MODES' : Array.from(observedModes)[0];
    if (actualMode !== raw.execution_st_policy ||
        !Array.isArray(raw.st_fallback_dates) ||
        JSON.stringify(raw.st_fallback_dates) !== JSON.stringify(fallbackDates)) return invalid;
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

async function loadForwardPerformance(pool = currentPool) {
  const text = await fetchText(pool.forwardUrl, true);
  if (text === null) return { status: 'unavailable' };
  return normalizeForwardPerformance(parseJSONLoose(text), pool.key);
}

function formatReturn(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const pct = n * 100;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(digits)}%`;
}

function makePerformanceChart(points, w = 760, h = 180, lastLabel = '最近估值') {
  const rows = Array.isArray(points) ? points : [];
  if (!rows.length) {
    return '<div class="performance-empty">从 2026.10.08 开盘首次建仓，收盘估值在数据更新后显示。</div>';
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
    <div class="performance-axis"><span>${formatDate(rows[0].date)} · 首次已核验估值</span><span>${formatDate(rows[rows.length - 1].date)} · ${lastLabel}</span></div>
  `;
}

function renderForwardPerformance(data, date = null) {
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
      invalid ? '估值数据校验未通过' : '等待首个估值';
    status.textContent = '模拟账户 · 2026.10.08 起';
    const message = unavailable ? '暂时无法读取累计收益数据。' :
      invalid ? '估值数据校验未通过，已停止展示。' :
      '等待首个估值。';
    chart.innerHTML = '<div class="performance-empty" role="status">' + message + '</div>';
    return;
  }

  // The ranking date is a T+1 usage date; its close valuation appears in the
  // forward series after that session settles. Show the matching point and
  // truncate the curve there, never the latest point under an old date label.
  let point = data.points[data.points.length - 1];
  let chartPoints = data.points;
  let lastLabel = '最近估值';
  let selected = false;
  let pendingKind = '';
  if (date) {
    const idx = data.points.findIndex(p => p.date === date);
    if (idx >= 0) {
      point = data.points[idx];
      chartPoints = data.points.slice(0, idx + 1);
      lastLabel = '所选日估值';
      selected = true;
    } else if (date < data.inception_date) {
      value.textContent = '未启用';
      latest.textContent = `${formatDate(data.inception_date)} 启用`;
      status.textContent = `模拟账户 · ${formatDate(data.inception_date)} 起`;
      chart.innerHTML = '<div class="performance-empty" role="status">所选日期早于启用日。</div>';
      return;
    } else {
      pendingKind = date > data.latest_date ? 'awaiting' : 'missing';
    }
  }
  const stale = point.date === data.latest_date && isPossiblyStale(data.latest_date);
  value.textContent = formatReturn(point.cumulative_return);
  value.className = 'performance-value ' + (point.cumulative_return >= 0 ? 'gain' : 'loss');
  const labelDate = formatDate(point.date);
  latest.textContent = pendingKind === 'awaiting'
    ? `所选日 ${formatDate(date)} 待结算 · 最近 ${labelDate}`
    : pendingKind === 'missing'
      ? `所选日无估值 · 最近 ${labelDate}`
      : selected ? `估值日 ${labelDate}` : `最新估值 ${labelDate}`;
  if (stale) latest.textContent += ' · 可能滞后';
  if (stale) latest.classList.add('stale');
  status.textContent = '模拟账户 · ' + formatDate(data.inception_date) + ' 起';
  if (data.data_mode === 'CAUSAL_SIMULATION') status.textContent += ' · 历史重建参考 · 含交易费用';
  if (chartPoints.some(p => p.data_mode === 'BACKFILL_RESEARCH') || data.data_mode === 'BACKFILL_RESEARCH') {
    status.textContent += ' · 含历史回填';
  }
  const fallbackCount = chartPoints.filter(p => p.execution_st_policy === 'ST_IGNORED_REFERENCE_ONLY').length;
  if (fallbackCount) status.textContent += ` · ${fallbackCount} 日未核实 ST，按参考规则模拟`;
  if (selected) status.textContent += ' · 截至 ' + formatDate(point.date);
  if (pendingKind === 'awaiting') status.textContent += ' · 待结算';
  if (pendingKind === 'missing') status.textContent += ' · 无估值';
  if (stale) status.textContent += ' · 可能滞后';
  if (stale) status.classList.add('stale');
  chart.innerHTML = makePerformanceChart(chartPoints, 760, 180, lastLabel);
}

const TOP10_PROFILES = new Set([
  'c98856b4_top10_drop1',
  'raw10d_c98856b4_top10_drop1',
  '59b93929_top10_drop1',
  '59b93929_top10_drop1_10d',
]);

const CANONICAL_PAIR_BY_PROFILE = {
  c98856b4_top10_drop1: 'c98856b4_top10_drop1_canonical_1m',
  '59b93929_top10_drop1_10d': '59b93929_top10_drop1_10d_canonical_1m',
  'raw10d_c98856b4_top20_drop2_20d': 'raw10d_c98856b4_top20_drop2_20d_canonical_1m',
  stage_b_winner: 'stage_b_winner_canonical',
};

// Legacy read-only artifacts remain displayable during migration.
const LEGACY_PAIR_BY_PROFILE = {
  c98856b4_top10_drop1: 'c98856b4_top10_drop1_canonical',
  raw10d_c98856b4_top10_drop1: 'raw10d_c98856b4_top10_drop1_canonical',
  '59b93929_top10_drop1': '59b93929_top10_drop1_canonical',
};

function isWinnerCanonical(paper) {
  const prof = paper?.production_lineage?.profile;
  const expected = prof ? CANONICAL_PAIR_BY_PROFILE[prof] : null;
  const legacy = prof ? LEGACY_PAIR_BY_PROFILE[prof] : null;
  return Boolean(
    paper &&
    ((expected && (paper.paper_lineage === expected || paper.paper_lineage === legacy)) ||
     (paper.paper_lineage === 'stage_b_winner_canonical' &&
      paper.production_lineage &&
      paper.production_lineage.profile === 'stage_b_winner'))
  );
}

const MVP_CONTRACT_VERSION = 'csi1000_mvp_daily_v1';

function evaluateMvpContract(metadata, paper, date, rows, pool = currentPool) {
  if (!metadata) {
    return {
      ok: false,
      legacy: true,
      errors: ['MVP metadata sidecar missing'],
      pitText: '未记录',
      riskText: '未记录',
    };
  }
  const errors = [];
  const top20 = rows.map(row => row.instrument);
  const metaTop20 = metadata.signal_lineage?.display_top20_instruments;
  const production = metadata.production_lineage;
  const pit = metadata.pit_universe;
  const label = metadata.label_maturity;
  const risk = metadata.risk;
  const expectedMarket = pool?.key || 'csi1000';
  const expectedPitCount = pool?.expectedPitCount || 1000;
  const publication = metadata.publication;
  const lagged = publication?.status === 'PASS_OPERATOR_OVERRIDE' ||
    publication?.publication_mode === 'OPERATOR_OVERRIDE' ||
    String(publication?.canonical_freshness_gate || '').includes('OPERATOR_OVERRIDE');

  if (metadata.contract_version !== MVP_CONTRACT_VERSION) errors.push('contract version mismatch');
  if (metadata.market !== expectedMarket) errors.push('market mismatch');
  if (!['PASS', 'PASS_BACKFILL', 'PASS_OPERATOR_OVERRIDE'].includes(metadata.publication?.status) || metadata.publication?.usage_date !== date) {
    errors.push('publication gate not bound to selected date');
  }
  if (!paper || metadata.publication?.signal_data_date !== paper.signal_data_date ||
      paper.usage_date !== date) {
    errors.push('paper date lineage mismatch');
  }
  if (lagged) {
    const pending = paper?.pending_orders;
    const dataDate = publication?.signal_data_date;
    const calendar = paper?.trading_calendar?.dates;
    const lag = Array.isArray(calendar) ? calendar.filter(day => dataDate < day && day < date).length : null;
    if (lag !== 1 || pending?.status !== 'awaiting_execution_data' ||
        !(pending.signal_date <= dataDate && dataDate < pending.execution_date && pending.execution_date < date)) {
      errors.push('lagged publication is not a frozen reference order');
    }
  }
  const expectedCount = TOP10_PROFILES.has(production?.profile) ? 10 : 20;
  if (!isWinnerCanonical(paper) ||
      !CANONICAL_PAIR_BY_PROFILE[production?.profile] ||
      production?.candidate_id !== paper?.production_lineage?.candidate_id ||
      production?.model_config_sha256 !== paper?.production_lineage?.model_config_sha256) {
    errors.push('frozen winner lineage mismatch');
  }
  if (metadata.provider?.calendar_end !== metadata.publication?.signal_data_date) {
    errors.push('provider freshness evidence mismatch');
  }
  if (pit?.status !== 'PASS' || pit?.member_count !== expectedPitCount ||
      pit?.asof !== metadata.publication?.signal_data_date) {
    errors.push('PIT universe gate failed');
  }
  if (label?.status !== 'PASS' || (label?.horizon_sessions !== 20 && label?.horizon_sessions !== 10) ||
      label?.signal_data_date !== metadata.publication?.signal_data_date) {
    errors.push('label maturity gate failed');
  }
  if (!Array.isArray(metaTop20) || metaTop20.length !== expectedCount ||
      rows.length !== expectedCount ||
      metaTop20.some((symbol, i) => symbol !== top20[i])) {
    errors.push('display Top20 lineage mismatch');
  }
  if (!risk || risk.tradeability_certified !== false ||
      !risk.st || !risk.usage_day_suspension) {
    errors.push('risk boundary missing');
  }

  const st = risk?.st;
  const stText = st?.status === 'NOT_CHECKED_REFERENCE_ONLY'
    ? 'ST未纳入模拟'
    : st?.status === 'SOURCE_SNAPSHOT_AVAILABLE'
      ? (st.top20_st_count ? `ST命中 ${st.top20_st_count}` : 'ST快照无命中')
      : 'ST状态未知';
  if (st?.status === 'NOT_CHECKED_REFERENCE_ONLY' &&
      (risk?.execution_st_policy !== 'ST_IGNORED_REFERENCE_ONLY' ||
       paper?.execution_st_policy !== 'ST_IGNORED_REFERENCE_ONLY' ||
       st.top20_st_count != null)) {
    errors.push('reference ST policy disclosure mismatch');
  }
  if (paper?.execution_st_policy === 'ST_VERIFIED_PREOPEN' &&
      (risk?.execution_st_policy !== 'ST_VERIFIED_PREOPEN' ||
       st?.status !== 'SOURCE_SNAPSHOT_AVAILABLE')) {
    errors.push('verified ST evidence/policy mismatch');
  }
  const suspensionText = risk?.usage_day_suspension?.status === 'NOT_INDEPENDENTLY_VERIFIED_PREOPEN'
    ? '停牌待执行后确认' : '停牌状态异常';
  return {
    ok: errors.length === 0,
    legacy: false,
    backfill: metadata.publication?.status === 'PASS_BACKFILL',
    lagged,
    publicationWarning: lagged ? `排名数据截至 ${formatDate(publication.signal_data_date)}，滞后 1 个交易日；仅供参考，旧订单保持冻结。` : null,
    errors,
    pitText: pit?.status === 'PASS' && label?.status === 'PASS'
      ? `${pit.member_count}只成分股 · 防泄漏校验通过` : '未通过',
    riskText: `${stText} · ${suspensionText}`,
  };
}

function buildRows(rows, nameMap, prev, chartData) {
  const prevCodes = new Set(prev ? prev.map(r => r.instrument) : []);
  const prevRank = {};
  if (prev) prev.forEach(r => { prevRank[r.instrument] = Number(r.rank); });

  return rows.map(r => {
    const rank = Number(r.rank);
    let tag = prev ? '<span class="tag tag-neutral" title="与上一期可用榜单排名相同">持平</span>' : '<span class="tag tag-neutral">无对照</span>';
    if (prev && !prevCodes.has(r.instrument)) {
      const pool = (typeof currentPool !== 'undefined' && currentPool) ? currentPool : { topk: 10, nd: 1 };
      tag = `<span class="tag tag-new" title="进入本期 Top ${pool.topk}；受 Drop${pool.nd} 限制，不等于实际买入">新进</span>`;
    }
    else if (prev && rank < prevRank[r.instrument]) tag = '<span class="tag tag-up">上升</span>';
    else if (prev && rank > prevRank[r.instrument]) tag = '<span class="tag tag-dn">下降</span>';

    return {
      rank,
      instrument: r.instrument,
      name: nameMap[qlibCode(r.instrument)] || '名称待更新',
      score: formatScore(r.score),
      tag,
      spark: chartData === undefined ? sparklineEmpty('loading') :
        (!chartData || !chartData.stocks ? sparklineEmpty('unavailable') :
          makeSparkline(calendarAlignChartValues(chartData.stocks[r.instrument], chartData.dates))),
      url: thsUrl(r.instrument),
    };
  });
}

// Dropping out of the ranking is not the same as being sold. The production
// paper artifact carries the actual planned Top-k/Drop-n orders for the next
// open plus the pre-trade holdings, so the page can separate a real sell from
// a stock that left the ranking but is still held and will be re-evaluated on
// later sessions.
function buildRebalance(rows, nameMap, prev, paper, date, pool = null) {
  const configured = pool || ((typeof currentPool !== 'undefined' && currentPool) ? currentPool : { topk: 10, nd: 1 });
  const topk = configured.topk || 10;
  const nd = configured.nd || 1;
  const currentRank = new Map(rows.map(r => [r.instrument, Number(r.rank)]));
  const prevRank = new Map((prev || []).map(r => [r.instrument, Number(r.rank)]));
  const item = (instrument, rank, rankLabel, rankFallback) => ({
    instrument,
    name: nameMap[qlibCode(instrument)] || '名称待更新',
    rank: Number.isFinite(rank) ? rank : null,
    rankLabel,
    rankFallback,
    url: thsUrl(instrument),
  });
  const exits = (prev || [])
    .filter(r => !currentRank.has(r.instrument))
    .map(r => item(r.instrument, Number(r.rank), '上期', '上期未在榜'));

  const paperMatches = Boolean(paper) && paper.usage_date === date;
  const pending = paperMatches ? paper.pending_orders : null;
  const sellCodes = pending && Array.isArray(pending.sell) ? pending.sell : null;
  const buyCodes = pending && Array.isArray(pending.buy) ? pending.buy : null;
  const planAvailable = sellCodes !== null && buyCodes !== null;
  const clean = codes => [...new Set(codes.filter(code => SIGNAL_INSTRUMENT.test(code)))];
  const sell = planAvailable ? clean(sellCodes).map(code => item(code, prevRank.get(code), '上期', '持仓中')) : [];
  const buy = planAvailable ? clean(buyCodes).map(code => item(code, currentRank.get(code), '本期', '本期未在榜')) : [];

  const positionsAvailable = paperMatches && paper.positions && typeof paper.positions === 'object'
    && !Array.isArray(paper.positions);
  const positions = positionsAvailable
    ? new Set(Object.keys(paper.positions).filter(code => SIGNAL_INSTRUMENT.test(code)))
    : null;
  const sold = new Set(sell.map(r => r.instrument));
  // Every held name outside the current Top-k is still exposed to the Drop-n
  // rule on later sessions, not only the names that left the ranking today.
  const heldOutside = planAvailable && positions
    ? [...positions]
      .filter(code => !currentRank.has(code) && !sold.has(code))
      .sort((a, b) => (prevRank.get(a) || 1e9) - (prevRank.get(b) || 1e9) || a.localeCompare(b))
      .map(code => item(code, prevRank.get(code), '上期', '上期未在榜'))
    : [];
  return {
    planAvailable,
    awaiting: Boolean(pending && pending.status === 'awaiting_execution_data'),
    frozen: Boolean(pending && pending.status === 'awaiting_execution_data' && pending.execution_date < date),
    pendingExecutionDate: pending?.execution_date || null,
    positionsAvailable: Boolean(positionsAvailable),
    topk,
    nd,
    sell,
    buy,
    exits,
    heldOutside,
  };
}

function rebalanceItem(r) {
  const rank = r.rank === null
    ? `<span class="trade-rank">${escapeHtml(r.rankFallback)}</span>`
    : `<span class="trade-rank">${escapeHtml(r.rankLabel)} #${escapeHtml(r.rank)}</span>`;
  return `
    <div class="trade-item">
      <a class="stock-link" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.instrument)}</a>
      <span class="stock-name">${escapeHtml(r.name)}</span>
      ${rank}
    </div>`;
}

function heldOutsideHtml(items, title, note) {
  return `
    <div class="held-exits">
      <div class="held-exits-head">
        <span class="panel-kicker">${title}</span>
        <span class="held-exits-note">${note}</span>
      </div>
      <div class="held-exit-list">
        ${items.map(r => `
          <span class="held-exit-item">
            <span class="held-exit-rank">${r.rank === null ? escapeHtml(r.rankFallback) : `${escapeHtml(r.rankLabel)} #${escapeHtml(r.rank)}`}</span>
            <a class="stock-link" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.instrument)}</a>
            <span class="stock-name">${escapeHtml(r.name)}</span>
          </span>`).join('')}
      </div>
    </div>`;
}

function rebalanceBody(model) {
  const topk = model.topk || 10;
  const nd = model.nd || 1;
  const column = (title, cls, items, emptyText) => `
    <div class="trade-column ${cls}">
      <div class="trade-head">
        <span class="trade-title">${title}</span>
        <span class="trade-count">${items.length} 只</span>
      </div>
      ${items.length ? `<div class="trade-items">${items.map(rebalanceItem).join('')}</div>` :
        `<div class="trade-empty">${emptyText}</div>`}
    </div>`;
  let body = '';
  if (model.planAvailable) {
    body += `
      <div class="rebalance-grid">
        ${column(model.frozen ? '冻结的原调入单' : '计划调入', 'trade-in', model.buy, '本期无调入')}
        ${column(model.frozen ? '冻结的原调出单' : '计划调出', 'trade-out', model.sell, '本期无调出')}
      </div>`;
  }
  if (model.planAvailable && model.heldOutside.length) {
    body += heldOutsideHtml(
      model.heldOutside,
      `持仓中不在本期 Top ${topk}`,
      `未触发 Drop${nd} · 继续持有`
    );
  } else if (!model.planAvailable && model.exits.length) {
    body += heldOutsideHtml(
      model.exits,
      '榜单移出',
      `退出 Top ${topk}，未调出`
    );
  } else if (model.planAvailable && !model.positionsAvailable && model.exits.length) {
    body += heldOutsideHtml(
      model.exits,
      '榜单移出',
      '持仓数据不可用'
    );
  }
  return body;
}

function renderRebalance(model, prevDate) {
  const section = document.getElementById('rebalance');
  if (!section) return;
  const body = document.getElementById('rebalance-body');
  const note = document.getElementById('rebalance-note');
  const visible = model.planAvailable || model.exits.length > 0;
  if (!visible) {
    section.hidden = true;
    if (body) body.innerHTML = '';
    return;
  }
  section.hidden = false;
  const title = document.getElementById('rebalance-title');
  if (title) title.textContent = model.frozen ? '历史待执行订单 · 冻结' : '本期调仓';
  if (body) body.innerHTML = rebalanceBody(model);
  if (note) {
    const compare = prevDate ? `${formatDate(prevDate)} 对比 · ` : '';
    if (model.planAvailable) {
      note.textContent = model.frozen
        ? `原执行日 ${formatDate(model.pendingExecutionDate)} · 保留原订单，本日未生成新订单`
        : compare + `T+1 开盘 · 最多调出 ${model.nd || 1} 只${model.awaiting ? ' · 沿用待执行' : ''}`;
    } else {
      note.textContent = compare + '调仓计划暂不可用';
    }
  }
}

function tableRows(rows) {
  return rows.map(r => `
    <tr>
      <td><span class="rank-badge${r.rank <= 3 ? ' top' : ''}">${escapeHtml(r.rank)}</span></td>
      <td><a class="stock-link" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.instrument)}</a></td>
      <td class="stock-name">${escapeHtml(r.name)}</td>
      <td><span class="score ${r.score.klass}">${escapeHtml(r.score.text)}</span></td>
      <td>${r.tag}</td>
      <td class="spark">${r.spark}</td>
    </tr>
  `).join('');
}

function mobileCards(rows) {
  return rows.map(r => `
    <article class="stock-card">
      <span class="rank-badge${r.rank <= 3 ? ' top' : ''}">${escapeHtml(r.rank)}</span>
      <div class="stock-main">
        <a class="stock-link" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.instrument)}</a>
        <span class="stock-name">${escapeHtml(r.name)}</span>
      </div>
      <div class="stock-side">
        <div><span class="score ${r.score.klass}">${escapeHtml(r.score.text)}</span> ${r.tag}</div>
        <div class="spark">${r.spark}</div>
      </div>
    </article>
  `).join('');
}

function renderHistoryButtons(dates) {
  return dates.slice(0, 24).map((d, i) =>
    `<button class="date-btn${i === 0 ? ' current' : ''}" aria-pressed="${i === 0}" data-date="${d.date}">${formatDate(d.date)}</button>`
  ).join('');
}

function pageShell(dates) {
  const history = renderHistoryButtons(dates);
  const poolTabs = POOL_ORDER.map(key => {
    const p = POOLS[key];
    const active = p.key === currentPool.key;
    return `<button class="pool-btn${active ? ' current' : ''}" role="tab" aria-selected="${active}" data-pool="${p.key}">${p.label} <span>${p.code}</span></button>`;
  }).join('');

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
          <div class="eyebrow"><span class="status-dot"></span> A股主流宽基量化多因子选股</div>
          <div class="pool-switcher" role="tablist" aria-label="选择指数股票池">${poolTabs}</div>
          <h1><span id="hero-pool-name">${currentPool.label}</span><span>每日选股信号</span></h1>
          <p class="hero-lede" id="hero-lede">LightGBM + Alpha158 量价模型，T 日收盘后生成指数成分股 Top ${currentPool.topk} 信号，T+1 开盘使用。</p>
        </div>
        <aside class="protocol-card">
          <h2>当前策略</h2>
          <div class="protocol-list">
            <div class="protocol-row"><span>股票池</span><strong id="protocol-pool">${currentPool.label} (${currentPool.code})</strong></div>
            <div class="protocol-row"><span>模型</span><strong>${PRODUCTION.model}</strong></div>
            <div class="protocol-row"><span>目标</span><strong id="protocol-target">${currentPool.horizonDays} 日横截面相对强弱</strong></div>
            <div class="protocol-row"><span>组合</span><strong id="protocol-portfolio">${portfolioLabel(currentPool)}</strong></div>
            <div class="protocol-row"><span>信号时点</span><strong>T 收盘后生成 · T+1 开盘使用</strong></div>
          </div>
        </aside>
      </section>

      <section class="status-grid" aria-label="信号状态">
        <div class="stat-card">
          <div class="stat-label">适用交易日</div>
          <div class="stat-value" id="stat-date">—</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">数据截止</div>
          <div class="stat-value" id="stat-data-date">—</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">模型重训日</div>
          <div class="stat-value" id="stat-fit-date">—</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">当前策略</div>
          <div class="stat-value" id="stat-lineage">—</div>
        </div>
      </section>

      <section class="panel" id="ranking">
        <div class="panel-head">
          <div>
            <h2 id="ranking-title">Top ${currentPool.topk}</h2>
          </div>
          <div class="panel-note">
            <div id="artifact-status"></div>
          </div>
        </div>
        <div class="table-wrap">
          <table class="signal-table">
            <thead>
              <tr><th>排名</th><th>代码</th><th>名称</th><th id="th-pred-horizon" title="模型原始回归评分，仅用于横截面排序；不是预期涨幅">${currentPool.horizonDays}日模型评分</th><th>排名变化</th><th>近60日</th></tr>
            </thead>
            <tbody id="signal-rows"></tbody>
          </table>
        </div>
        <p class="muted ranking-score-note">模型评分为原始回归输出，仅用于横截面排序，不代表预期涨幅或校准收益率。</p>
        <div id="mobile-cards" class="mobile-cards"></div>
        <div id="rebalance" class="rebalance-block" hidden>
          <div class="rebalance-head">
            <div>
              <div class="panel-kicker" id="rebalance-kicker">Top ${currentPool.topk} · Drop${currentPool.nd}</div>
              <h3 id="rebalance-title">本期调仓</h3>
            </div>
            <div class="panel-note" id="rebalance-note"></div>
          </div>
          <div id="rebalance-body"></div>
        </div>
      </section>

      <section class="panel" id="history">
        <div class="panel-head">
          <div>
            <h2>历史榜单</h2>
          </div>
        </div>
        <div class="history-wrap">
          <div id="history-buttons" class="history-buttons">${history}</div>
        </div>
      </section>

      <section class="panel performance-panel" id="performance">
        <div class="performance-head">
          <div>
            <h2>策略累计收益 · 模拟盘参考</h2>
            <p id="forward-status" class="performance-status">模拟账户 · 2026.10.08 起</p>
          </div>
          <div class="performance-summary">
            <div id="forward-return" class="performance-value">待开始</div>
            <div id="forward-latest" class="performance-latest">等待首个交易日收盘结算</div>
          </div>
        </div>
        <div id="forward-chart" class="performance-chart">
          <div class="performance-empty">从 2026.10.08 开盘建仓，收盘后计算估值。</div>
        </div>
      </section>

      <section class="panel governance-panel" id="governance-notes" aria-label="数据核验与口径">
        <div class="panel-head governance-head">
          <div>
            <h2>数据核验与口径</h2>
          </div>
        </div>
        <div class="governance-body">
          <div class="governance-meta-grid">
            <div class="governance-meta-item">
              <span class="governance-meta-label">成分与防泄漏</span>
              <strong class="governance-meta-value" id="stat-pit">—</strong>
            </div>
            <div class="governance-meta-item governance-meta-wide">
              <span class="governance-meta-label">每日核验</span>
              <div id="mvp-health" class="governance-health" role="status">核验中…</div>
            </div>
          </div>
          <ul class="governance-list">
            <li>CSI300/CSI1000 为 Top10/Drop1，CSI500 为 Top20/Drop2；新进榜单不等于立即买入。</li>
            <li>模型评分为原始回归输出，仅用于横截面排序，不代表预期涨幅或校准收益率。</li>
            <li>三个独立 100 万元模拟账户自 2026.10.08 开盘建仓，按策略连续调仓并扣费；历史重建未核实逐日 ST 和开盘成交量，收益为模拟参考。</li>
          </ul>
        </div>
      </section>

      <footer class="footer">
        <span>仅供研究与工程验证，不构成投资建议。</span>
        <span><a href="methodology.html">查看策略方法论与历史回测表现 →</a></span>
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

  let dates = index.dates;
  let nameMap = index.nameMap;
  app.innerHTML = pageShell(dates);

  const indexCache = new Map([[currentPool.key, Promise.resolve(index)]]);
  const forwardCache = new Map([[currentPool.key, forwardPromise]]);

  // One CSV fetch per pool+date per session, including rank comparisons.
  const csvCache = new Map();
  function loadDateRows(date, pool = currentPool) {
    const key = pool.key + ':' + date;
    if (!csvCache.has(key)) {
      csvCache.set(key, fetchText(CSV_BASE + '/' + date + '_top20_lgb158' + pool.suffix + '.csv',
        date === dates[0]?.date).then(parseCSV));
    }
    return csvCache.get(key);
  }

  const contextCache = new Map();
  function loadContext(date, pool = currentPool) {
    const key = pool.key + ':' + date;
    if (!contextCache.has(key)) {
      contextCache.set(key, loadDateContext(date, date === dates[0]?.date, pool));
    }
    return contextCache.get(key);
  }

  let latestDateRequest = 0;

  function loadForwardFor(pool, date, request) {
    let promise = forwardCache.get(pool.key);
    if (!promise) {
      promise = loadForwardPerformance(pool);
      forwardCache.set(pool.key, promise);
    }
    promise.then(data => {
      if (request !== latestDateRequest || currentPool.key !== pool.key) return;
      renderForwardPerformance(data, date);
    });
  }

  async function renderDate(dateInfo) {
    const pool = currentPool;
    const request = ++latestDateRequest;
    loadForwardFor(pool, dateInfo.date, request);
    const container = document.querySelector('.signal-table');
    if (container) container.setAttribute('aria-busy', 'true');
    document.getElementById('artifact-status').textContent = '加载中…';
    const idx = dates.findIndex(d => d.date === dateInfo.date);
    const prevDateInfo = idx >= 0 && idx + 1 < dates.length ? dates[idx + 1] : null;
    const previousPromise = prevDateInfo
      ? loadDateRows(prevDateInfo.date, pool).then(previous => previous.length ? previous : null)
      : Promise.resolve(null);
    const ctxPromise = loadContext(dateInfo.date, pool);
    const rows = await loadDateRows(dateInfo.date, pool);
    if (request !== latestDateRequest) return;

    if (!rows.length) {
      document.getElementById('signal-rows').innerHTML = '';
      document.getElementById('mobile-cards').innerHTML = '';
      renderRebalance(buildRebalance([], nameMap, null, null, dateInfo.date), null);
      document.getElementById('artifact-status').textContent = '该日信号文件无法读取';
      document.getElementById('stat-date').textContent = formatDate(dateInfo.date);
      document.getElementById('stat-data-date').textContent = '—';
      document.getElementById('stat-fit-date').textContent = '—';
      document.getElementById('stat-lineage').textContent = '未核验';
      document.getElementById('stat-pit').textContent = '—';
      document.getElementById('mvp-health').textContent = '信号文件缺失，无法核验。';
      if (container) container.setAttribute('aria-busy', 'false');
      return;
    }

    function paint(previous, ctx) {
      if (request !== latestDateRequest) return;
      const built = buildRows(rows, nameMap, previous, ctx ? ctx.chart : undefined);
      const rebalance = buildRebalance(rows, nameMap, previous, ctx ? ctx.paper : null, dateInfo.date);
      renderRebalance(rebalance, prevDateInfo ? prevDateInfo.date : null);
      const titleEl = document.getElementById('ranking-title');
      if (titleEl && rows.length) titleEl.textContent = 'Top ' + rows.length;
      document.getElementById('signal-rows').innerHTML = tableRows(built);
      document.getElementById('mobile-cards').innerHTML = mobileCards(built);
      document.getElementById('stat-date').textContent = formatDate(dateInfo.date);
      const paper = ctx?.paper;
      document.getElementById('stat-data-date').textContent = formatDate(paper?.signal_data_date || null);
      document.getElementById('stat-fit-date').textContent = formatDate(paper?.model_fit_asof || null);
      const winner = isWinnerCanonical(paper);
      const lineageTopk = Number(paper?.production_lineage?.topk);
      const lineageLabel = Number.isInteger(lineageTopk)
        ? `Top${lineageTopk} 低换手优选`
        : '定型优选策略';
      const contract = ctx ? evaluateMvpContract(ctx.metadata, paper, dateInfo.date, rows, pool) : null;
      document.getElementById('stat-lineage').textContent =
        ctx ? (winner ? lineageLabel : '历史 / 未核验') : '核验中';
      document.getElementById('stat-pit').textContent =
        !ctx ? '核验中' : contract.pitText;
      document.getElementById('artifact-status').innerHTML = !ctx
        ? '校验中…'
        : (contract.ok
          ? (contract.lagged ? '<span class="status-pill history">滞后排名 · 订单冻结</span>'
            : contract.backfill ? '<span class="status-pill"><span class="status-dot"></span> 回补校验 · 通过</span>' : '<span class="status-pill"><span class="status-dot"></span> 校验 · 通过</span>')
          : '<span class="status-pill history">校验信息不完整</span>');
      const health = document.getElementById('mvp-health');
      if (health) {
        const horizonDays = ctx?.metadata?.label_maturity?.horizon_sessions || 20;
        health.textContent = !ctx
          ? '核验中…'
          : contract.ok
            ? (contract.lagged ? contract.publicationWarning
              : `收盘行情、${pool.expectedPitCount} 只成分股、${horizonDays} 日防泄漏与账本核验通过。`)
            : contract.legacy
              ? '缺少校验文件，仅只读展示。'
              : '校验未通过：' + escapeHtml(contract.errors.join('；'));
      }
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

  function bindDateButtons() {
    document.querySelectorAll('.date-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const d = dates.find(item => item.date === btn.dataset.date);
        if (d) await renderDate(d);
      });
    });
  }

  async function switchPool(poolKey) {
    const nextPool = POOLS[poolKey];
    if (!nextPool || nextPool.key === currentPool.key) return;
    const poolRequest = ++latestDateRequest;
    currentPool = nextPool;
    dates = [];
    nameMap = {};
    const oldHistory = document.getElementById('history-buttons');
    if (oldHistory) oldHistory.innerHTML = '';
    document.getElementById('signal-rows').innerHTML = '';
    document.getElementById('mobile-cards').innerHTML = '';
    document.getElementById('artifact-status').textContent = '加载中…';
    for (const id of ['stat-date', 'stat-data-date', 'stat-fit-date', 'stat-lineage', 'stat-pit']) {
      document.getElementById(id).textContent = '—';
    }
    document.getElementById('mvp-health').textContent = '核验中…';
    renderForwardPerformance({ status: 'awaiting_first_valuation', points: [] }, null);
    renderRebalance(buildRebalance([], {}, null, null, null), null);
    savePool(nextPool.key);
    document.querySelectorAll('.pool-btn').forEach(btn => {
      const active = btn.dataset.pool === nextPool.key;
      btn.classList.toggle('current', active);
      btn.setAttribute('aria-selected', String(active));
    });
    const heroPool = document.getElementById('hero-pool-name');
    if (heroPool) heroPool.textContent = nextPool.label;
    const heroLede = document.getElementById('hero-lede');
    if (heroLede) {
      heroLede.textContent =
        `LightGBM + Alpha158 量价模型，T 日收盘后生成指数成分股 Top ${nextPool.topk} 信号，T+1 开盘使用。`;
    }
    const protoPool = document.getElementById('protocol-pool');
    if (protoPool) protoPool.textContent = `${nextPool.label} (${nextPool.code})`;
    const protoTarget = document.getElementById('protocol-target');
    if (protoTarget) protoTarget.textContent = `${nextPool.horizonDays} 日横截面相对强弱`;
    const protoPortfolio = document.getElementById('protocol-portfolio');
    if (protoPortfolio) protoPortfolio.textContent = portfolioLabel(nextPool);
    const rebalanceKicker = document.getElementById('rebalance-kicker');
    if (rebalanceKicker) {
      rebalanceKicker.textContent = `Top ${nextPool.topk} · Drop${nextPool.nd}`;
    }
    const thPred = document.getElementById('th-pred-horizon');
    if (thPred) thPred.textContent = `${nextPool.horizonDays}日模型评分`;

    if (!indexCache.has(nextPool.key)) {
      indexCache.set(nextPool.key, loadAvailableDates(nextPool));
    }
    if (!forwardCache.has(nextPool.key)) {
      forwardCache.set(nextPool.key, loadForwardPerformance(nextPool));
    }
    const poolIndex = await indexCache.get(nextPool.key);
    if (poolRequest !== latestDateRequest || currentPool.key !== nextPool.key) return;
    if (!poolIndex || !poolIndex.dates.length) {
      document.getElementById('signal-rows').innerHTML = '';
      document.getElementById('mobile-cards').innerHTML = '';
      document.getElementById('artifact-status').textContent = '暂无信号';
      return;
    }
    dates = poolIndex.dates;
    nameMap = poolIndex.nameMap;
    const historyWrap = document.getElementById('history-buttons');
    if (historyWrap) {
      historyWrap.innerHTML = renderHistoryButtons(dates);
      bindDateButtons();
    }
    renderDate(dates[0]);
  }

  document.querySelectorAll('.pool-btn').forEach(btn => {
    btn.addEventListener('click', () => switchPool(btn.dataset.pool));
  });

  bindDateButtons();
  renderDate(dates[0]);
}

main();
