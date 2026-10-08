// CSI1000 production signal dashboard.
// Historical/research pool artifacts remain in the repository but are not part
// of the public production surface.
const CSV_BASE = 'signals';
const NAME_MAP_URL = `${CSV_BASE}/code_name_map.csv`;

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

async function fetchText(url) {
  try {
    const r = await fetch(url, { cache: 'no-store' });
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

function makeSparkline(values, w = 92, h = 28) {
  const vs = (values || [])
    .filter(v => v !== null && v !== undefined && Number.isFinite(Number(v)))
    .map(Number);
  if (vs.length < 2) return '<span class="muted">—</span>';

  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const range = max - min || 1;
  const pts = vs.map((v, i) =>
    `${(i / (vs.length - 1) * w).toFixed(1)},${(h - (v - min) / range * h).toFixed(1)}`
  ).join(' ');
  const stroke = vs[vs.length - 1] >= vs[0] ? '#ff6b6b' : '#3ccf91';
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="1.7"
      stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

async function loadNameMap() {
  const rows = parseCSV(await fetchText(NAME_MAP_URL));
  const map = {};
  rows.forEach(r => { if (r.code) map[r.code] = r.name; });
  return map;
}

async function loadAvailableDates() {
  const now = new Date();
  const candidates = [];
  for (let i = 0; i < 45; i++) {
    const d = new Date(now - i * 86400000);
    if (d.getDay() === 0 || d.getDay() === 6) continue;
    candidates.push(
      d.getFullYear() + '-' +
      String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0')
    );
  }

  const results = await Promise.all(candidates.map(async date => {
    const text = await fetchText(`${CSV_BASE}/${date}_top20_lgb158.csv`);
    return text ? { date, text } : null;
  }));
  return results.filter(Boolean).sort((a, b) => b.date.localeCompare(a.date));
}

async function loadDateContext(date) {
  const [chartText, paperText] = await Promise.all([
    fetchText(`${CSV_BASE}/${date}_chart.json`),
    fetchText(`${CSV_BASE}/${date}_paper_portfolio.json`),
  ]);
  return {
    chart: parseJSONLoose(chartText),
    paper: parseJSONLoose(paperText),
  };
}

function isWinnerCanonical(paper) {
  return Boolean(
    paper &&
    paper.paper_lineage === 'stage_b_winner_canonical' &&
    paper.production_lineage &&
    paper.production_lineage.profile === 'stage_b_winner'
  );
}

function buildRows(dateInfo, nameMap, prev, chartData) {
  const rows = parseCSV(dateInfo.text);
  const prevCodes = new Set(prev ? prev.map(r => r.instrument) : []);
  const prevRank = {};
  if (prev) prev.forEach(r => { prevRank[r.instrument] = Number(r.rank); });

  return rows.map(r => {
    const rank = Number(r.rank);
    let tag = '<span class="tag tag-hold">持有</span>';
    if (!prevCodes.has(r.instrument)) tag = '<span class="tag tag-new">新进</span>';
    else if (rank < prevRank[r.instrument]) tag = '<span class="tag tag-up">上升</span>';
    else if (rank > prevRank[r.instrument]) tag = '<span class="tag tag-dn">下降</span>';

    return {
      rank,
      instrument: r.instrument,
      name: nameMap[qlibCode(r.instrument)] || '名称待更新',
      score: formatScore(r.score),
      tag,
      spark: makeSparkline(chartData?.stocks?.[r.instrument]),
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
    `<button class="date-btn${i === 0 ? ' current' : ''}" data-date="${d.date}">${formatDate(d.date)}</button>`
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
            当前生产模型为冻结的 Stage-B winner。页面展示每日 Top20 排名、模型数据日期与
            paper lineage 状态；研究协议与生产实现保持同一套 T close → T+1 open 语义。
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
          <div class="stat-sub" id="stat-lineage-sub">artifact lineage</div>
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
            <div style="margin-top:6px">分数为模型输出，不代表确定收益</div>
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

      <section class="panel" id="history">
        <div class="panel-head">
          <div>
            <div class="panel-kicker">Archive</div>
            <h2>历史榜单</h2>
          </div>
          <div class="panel-note">历史文件只读保留；切换日期不会改变生产状态</div>
        </div>
        <div class="history-wrap">
          <div id="history-buttons" class="history-buttons">${history}</div>
        </div>
      </section>

      <div class="callout">
        <strong>当前范围：</strong>生产页面仅展示 CSI1000。创业板旧模型的每日更新与公开展示已暂停，
        历史研究结果仍保留；待新模型完成独立验证并通过 production gate 后再恢复。
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
  const [nameMap, dates] = await Promise.all([loadNameMap(), loadAvailableDates()]);

  if (!dates.length) {
    app.innerHTML = `<main class="shell"><div class="callout"><strong>暂无可用信号。</strong> 当前没有找到最近的 CSI1000 production artifact。</div></main>`;
    return;
  }

  app.innerHTML = pageShell(dates);

  async function renderDate(dateInfo) {
    const idx = dates.findIndex(d => d.date === dateInfo.date);
    const prev = idx >= 0 && idx + 1 < dates.length ? parseCSV(dates[idx + 1].text) : null;
    const ctx = await loadDateContext(dateInfo.date);
    const rows = buildRows(dateInfo, nameMap, prev, ctx.chart);
    const winner = isWinnerCanonical(ctx.paper);

    document.getElementById('signal-rows').innerHTML = tableRows(rows);
    document.getElementById('mobile-cards').innerHTML = mobileCards(rows);
    document.getElementById('stat-date').textContent = formatDate(dateInfo.date);
    document.getElementById('stat-data-date').textContent =
      formatDate(ctx.paper?.signal_data_date || dateInfo.date);
    document.getElementById('stat-fit-date').textContent =
      formatDate(ctx.paper?.model_fit_asof || null);
    document.getElementById('stat-lineage').textContent = winner ? 'Winner canonical' : '历史记录';
    document.getElementById('stat-lineage-sub').textContent = winner
      ? 'stage_b_winner_canonical'
      : '非当前 winner lineage / 元数据缺失';
    document.getElementById('artifact-status').innerHTML = winner
      ? '<span class="status-pill"><span class="status-dot"></span> CURRENT WINNER LINEAGE</span>'
      : '<span class="status-pill history">HISTORICAL ARTIFACT</span>';

    document.querySelectorAll('.date-btn').forEach(btn => {
      btn.classList.toggle('current', btn.dataset.date === dateInfo.date);
    });
  }

  document.querySelectorAll('.date-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const d = dates.find(x => x.date === btn.dataset.date);
      if (d) await renderDate(d);
    });
  });

  await renderDate(dates[0]);
}

main();
