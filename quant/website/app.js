// CSI1000 production signal dashboard.
// Historical/research pool artifacts remain in the repository but are not part
// of the public production surface.
const CSV_BASE = 'signals';
const DATE_INDEX_URL = CSV_BASE + '/available_dates.json';

const PRODUCTION = {
  pool: '中证1000',
  profile: 'Top10 低换手优选',
  model: 'LightGBM · Alpha158 量价模型',
  target: '20 交易日前瞻收益',
  portfolio: '等权 10 只 · 单日限换 1 只',
  execution: 'T 日收盘打分 → T+1 开盘调仓',
  retrain: '每 20 个交易日滚动重训',
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
  const [chartText, paperText, metadataText] = await Promise.all([
    fetchText(CSV_BASE + '/' + date + '_chart.json', fresh),
    fetchText(CSV_BASE + '/' + date + '_paper_portfolio.json', fresh),
    fetchText(CSV_BASE + '/' + date + '_mvp_metadata.json', fresh),
  ]);
  return {
    chart: parseJSONLoose(chartText),
    paper: parseJSONLoose(paperText),
    metadata: parseJSONLoose(metadataText),
  };
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
  const stModes = ['ST_IGNORED_REFERENCE_ONLY', 'ST_VERIFIED_PREOPEN'];
  if (raw.execution_st_policy != null &&
      ![...stModes, 'MIXED_ST_MODES'].includes(raw.execution_st_policy)) return invalid;
  if (raw.status === 'awaiting_first_valuation') {
    return raw.points.length === 0 && raw.latest_date == null && raw.cumulative_return == null
      ? raw : invalid;
  }
  if (raw.status !== 'active' || raw.points.length === 0 ||
      raw.points[0]?.date < FORWARD_START ||
      raw.inception_date !== raw.points[0]?.date) return invalid;
  let previousDate = '';
  let previousNav = 1;
  const observedModes = new Set();
  const fallbackDates = [];
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
    if (raw.execution_st_policy != null) {
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
    return '<div class="performance-empty">从新策略启用（2026.10.15）开始记录，首个收盘估值将在下一交易日数据更新后显示。</div>';
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
    <div class="performance-axis"><span>${formatDate(rows[0].date)} · 首次已核验估值</span><span>${formatDate(rows[rows.length - 1].date)} · 最近估值</span></div>
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
      invalid ? '估值数据校验未通过' : '等待首个交易日收盘结算';
    status.textContent = '模拟净值跟踪 · 2026.10.15 新策略上线起';
    const message = unavailable ? '暂时无法读取累计收益数据，请稍后重试。' :
      invalid ? '累计收益数据不完整或不符合前向跟踪约定，已停止展示。' :
      '从新策略启用（2026.10.15）开始记录，首个收盘估值将在下一交易日数据更新后显示。';
    chart.innerHTML = '<div class="performance-empty" role="status">' + message + '</div>';
    return;
  }

  const stale = isPossiblyStale(data.latest_date);
  value.textContent = formatReturn(data.cumulative_return);
  value.className = 'performance-value ' + (data.cumulative_return >= 0 ? 'gain' : 'loss');
  latest.textContent = '最新估值 ' + formatDate(data.latest_date) + (stale ? ' · 可能滞后' : '');
  if (stale) latest.classList.add('stale');
  status.textContent = data.execution_st_policy === 'MIXED_ST_MODES'
    ? '参考模拟收益 · 部分日期 ST 接口故障，未考虑 ST · ' + formatDate(data.inception_date) + ' 起'
    : data.execution_st_policy === 'ST_IGNORED_REFERENCE_ONLY'
      ? '参考模拟收益 · 不考虑 ST（接口故障降级） · ' + formatDate(data.inception_date) + ' 起'
      : data.execution_st_policy === 'ST_VERIFIED_PREOPEN'
        ? '参考模拟收益 · 已使用 ST 快照 · ' + formatDate(data.inception_date) + ' 起'
        : '模拟净值跟踪 · ' + formatDate(data.inception_date) + ' 首次核验起';
  if (stale) status.textContent += ' · 请核对估值时效';
  if (stale) status.classList.add('stale');
  chart.innerHTML = makePerformanceChart(data.points);
}

function isWinnerCanonical(paper) {
  return Boolean(
    paper &&
    ((paper.paper_lineage === 'c98856b4_top10_drop1_canonical' &&
      paper.production_lineage &&
      paper.production_lineage.profile === 'c98856b4_top10_drop1') ||
     (paper.paper_lineage === 'stage_b_winner_canonical' &&
      paper.production_lineage &&
      paper.production_lineage.profile === 'stage_b_winner'))
  );
}

const MVP_CONTRACT_VERSION = 'csi1000_mvp_daily_v1';

function evaluateMvpContract(metadata, paper, date, rows) {
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

  if (metadata.contract_version !== MVP_CONTRACT_VERSION) errors.push('contract version mismatch');
  if (metadata.market !== 'csi1000') errors.push('market mismatch');
  if (metadata.publication?.status !== 'PASS' || metadata.publication?.usage_date !== date) {
    errors.push('publication gate not bound to selected date');
  }
  if (!paper || metadata.publication?.signal_data_date !== paper.signal_data_date ||
      paper.usage_date !== date) {
    errors.push('paper date lineage mismatch');
  }
  const expectedCount = production?.profile === 'c98856b4_top10_drop1' ? 10 : 20;
  if (!isWinnerCanonical(paper) ||
      (production?.profile !== 'c98856b4_top10_drop1' && production?.profile !== 'stage_b_winner') ||
      production?.candidate_id !== paper?.production_lineage?.candidate_id ||
      production?.model_config_sha256 !== paper?.production_lineage?.model_config_sha256) {
    errors.push('frozen winner lineage mismatch');
  }
  if (metadata.provider?.calendar_end !== metadata.publication?.signal_data_date) {
    errors.push('provider freshness evidence mismatch');
  }
  if (pit?.status !== 'PASS' || pit?.member_count !== 1000 ||
      pit?.asof !== metadata.publication?.signal_data_date) {
    errors.push('PIT universe gate failed');
  }
  if (label?.status !== 'PASS' || label?.horizon_sessions !== 20 ||
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
    if (prev && !prevCodes.has(r.instrument)) tag = '<span class="tag tag-new">新进</span>';
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
          <div class="eyebrow"><span class="status-dot"></span> 中证1000 量化多因子选股</div>
          <h1>中证1000<span>每日选股信号</span></h1>
          <p class="hero-lede">
            基于 LightGBM 与 158 维量价因子体系，经多轮全周期交叉验证与双账本独立审计定型，
            每日生成中证1000成分股横截面预测 Top 10。信号于 T 日收盘后生成，供 T+1 日开盘调仓参考。
          </p>
        </div>
        <aside class="protocol-card">
          <h2>当前策略配置</h2>
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
          <div class="stat-sub">T+1 信号生效日</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">数据截止</div>
          <div class="stat-value" id="stat-data-date">—</div>
          <div class="stat-sub">T 日收盘行情</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">模型重训日</div>
          <div class="stat-value" id="stat-fit-date">—</div>
          <div class="stat-sub">最近一次参数更新</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">当前策略</div>
          <div class="stat-value" id="stat-lineage">—</div>
          <div class="stat-sub" id="stat-lineage-sub">版本说明</div>
        </div>
      </section>

      <section class="panel" id="ranking">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">每日选股榜单</div>
            <h2 id="ranking-title">Top 10</h2>
          </div>
          <div class="panel-note">
            <div id="ranking-date" class="ranking-date">—</div>
            <div id="artifact-status"></div>
          </div>
        </div>
        <div class="table-wrap">
          <table class="signal-table">
            <thead>
              <tr><th>排名</th><th>代码</th><th>名称</th><th>20日预测</th><th>排名变化</th><th>近60日</th></tr>
            </thead>
            <tbody id="signal-rows"></tbody>
          </table>
        </div>
        <div id="mobile-cards" class="mobile-cards"></div>
      </section>

      <section class="panel" id="history">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">历史记录</div>
            <h2>历史榜单</h2>
          </div>
          <div class="panel-note">新策略上线以来存档 · 按日期查看</div>
        </div>
        <div class="history-wrap">
          <div id="history-buttons" class="history-buttons">${history}</div>
        </div>
      </section>

      <section class="panel performance-panel" id="performance">
        <div class="performance-head">
          <div>
            <div class="panel-kicker">模拟组合表现</div>
            <h2>策略累计收益 · 模拟盘参考</h2>
            <p id="forward-status" class="performance-status">模拟净值跟踪 · 2026.10.15 新策略上线起</p>
          </div>
          <div class="performance-summary">
            <div id="forward-return" class="performance-value">待开始</div>
            <div id="forward-latest" class="performance-latest">等待首个交易日收盘结算</div>
          </div>
        </div>
        <div id="forward-chart" class="performance-chart">
          <div class="performance-empty">从新策略启用（2026.10.15）开始记录，首个收盘估值将在下一交易日数据更新后显示。</div>
        </div>
        <div class="performance-foot">
          <span>起点：2026.10.15 新策略启用日开盘前账户净值 = 1.0000</span>
          <span>T+1 开盘调仓 · 收盘估值结算（含交易成本与涨跌停约束）</span>
        </div>
      </section>

      <section class="panel governance-panel" id="governance-notes" aria-label="运行核验与口径说明">
        <div class="panel-head governance-head">
          <div>
            <div class="panel-kicker">风控核验与口径说明</div>
            <h2>运行核验与口径说明</h2>
          </div>
          <div class="panel-note">自动化数据校验与策略边界汇总</div>
        </div>
        <div class="governance-body">
          <div class="governance-meta-grid">
            <div class="governance-meta-item">
              <span class="governance-meta-label">成分股与样本校验</span>
              <strong class="governance-meta-value" id="stat-pit">—</strong>
              <span class="governance-meta-sub">当日指数成分与 20 日样本防泄漏校验</span>
            </div>
            <div class="governance-meta-item">
              <span class="governance-meta-label">特殊状态与停牌核验</span>
              <strong class="governance-meta-value" id="stat-risk">—</strong>
              <span class="governance-meta-sub">ST 风险警示与停牌状态核查</span>
            </div>
            <div class="governance-meta-item governance-meta-wide">
              <span class="governance-meta-label">每日数据完整性核验</span>
              <div id="mvp-health" class="governance-health" role="status">正在核对每日数据完整性与发布校验…</div>
            </div>
          </div>
          <ul class="governance-list">
            <li><strong>排名与预测口径：</strong>排名变化对比上一期可用榜单，不代表实际持仓；预测分数用于排序，不构成收益承诺。</li>
            <li><strong>累计收益与 ST 规则：</strong>旧策略信号已归档移除，累计收益自 2026.10.15 启用当前定型策略（Top 10 等权 · 单日限换 1 只）起独立计算。ST 接口正常则纳入 ST 快照；仅连接故障时不考虑 ST 并逐日披露。仅作模拟盘参考，非真实成交。</li>
            <li><strong>当前范围：</strong>生产页面仅展示中证1000（CSI1000）。创业板旧模型的每日更新与公开展示已暂停，待新模型完成独立验证并获准恢复后再展示。</li>
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
    document.getElementById('ranking-date').textContent = formatDate(dateInfo.date);
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
      document.getElementById('stat-pit').textContent = '—';
      document.getElementById('stat-risk').textContent = '—';
      document.getElementById('mvp-health').textContent =
        '该日信号文件缺失或读取失败，无法建立 MVP 发布合同。';
      if (container) container.setAttribute('aria-busy', 'false');
      return;
    }

    function paint(previous, ctx) {
      if (request !== latestDateRequest) return;
      const built = buildRows(rows, nameMap, previous, ctx ? ctx.chart : undefined);
      const titleEl = document.getElementById('ranking-title');
      if (titleEl && rows.length) titleEl.textContent = 'Top ' + rows.length;
      document.getElementById('signal-rows').innerHTML = tableRows(built);
      document.getElementById('mobile-cards').innerHTML = mobileCards(built);
      document.getElementById('stat-date').textContent = formatDate(dateInfo.date);
      const paper = ctx?.paper;
      document.getElementById('stat-data-date').textContent = formatDate(paper?.signal_data_date || null);
      document.getElementById('stat-fit-date').textContent = formatDate(paper?.model_fit_asof || null);
      const winner = isWinnerCanonical(paper);
      const lineageLabel = paper?.production_lineage?.profile === 'c98856b4_top10_drop1'
        ? 'Top10 低换手优选'
        : '定型优选策略';
      const contract = ctx ? evaluateMvpContract(ctx.metadata, paper, dateInfo.date, rows) : null;
      document.getElementById('stat-lineage').textContent =
        ctx ? (winner ? lineageLabel : '历史 / 未核验') : '核验中';
      document.getElementById('stat-lineage-sub').textContent = ctx
        ? (winner ? '定型主策略 · 参数已锁定' : '历史记录未包含当前版本标识') : '正在加载版本资料';
      document.getElementById('stat-pit').textContent =
        !ctx ? '核验中' : contract.pitText;
      document.getElementById('stat-risk').textContent =
        !ctx ? '核验中' : contract.riskText;
      document.getElementById('artifact-status').innerHTML = !ctx
        ? '预测排名已载入 · 正在获取数据校验状态'
        : (contract.ok
          ? '<span class="status-pill"><span class="status-dot"></span> 每日数据校验 · 已通过</span>'
          : '<span class="status-pill history">历史记录 / 校验信息不完整</span>');
      const health = document.getElementById('mvp-health');
      if (health) {
        health.textContent = !ctx
          ? '正在核对行情时效、成分股完整性、样本防泄漏与模拟盘状态。'
          : contract.ok
            ? '已完成行情时效、当日成分股、样本防泄漏、策略参数锁定与模拟账本核验。' +
              (ctx.metadata?.risk?.st?.status === 'NOT_CHECKED_REFERENCE_ONLY'
                ? '今日 ST 接口故障，模拟收益降级为不考虑 ST 的参考计算；'
                : '今日已获取 ST 快照参与模拟计算；') +
              '停牌与实盘成交未作认证。'
            : contract.legacy
              ? '该历史日期缺少完整校验文件，仅作只读展示，因此不会把旧版本标识单独解释为“已核验”。'
              : '每日数据校验未通过：' + contract.errors.join('；');
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

  document.querySelectorAll('.date-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const d = dates.find(item => item.date === btn.dataset.date);
      if (d) {
        // Render first: the MVP health/risk copy can change height between a
        // current contract and a legacy date. Scrolling before that reflow
        // makes the final Top20 position drift on tablet/mobile viewports.
        await renderDate(d);
        document.getElementById('ranking').scrollIntoView({ behavior: 'auto', block: 'start' });
      }
    });
  });

  renderDate(dates[0]);
}

main();
