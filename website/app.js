// ============================================================
// 每日选股结果展示 — 纯静态单页应用（响应式：桌面表格 / 移动卡片）
// 当前生产页面仅展示 csi1000（中证1000）；其他研究池暂停展示
// ============================================================
const CSV_BASE = 'signals';
const NAME_MAP_URL = `${CSV_BASE}/code_name_map.csv`;

// Production pool allowlist. Historical/research pool artifacts may remain in
// the repository, but are intentionally not reachable from this UI.
const POOLS = {
    csi1000: { label: '中证1000', suffix: '', desc: 'top20 · 预测未来 20 日收益 · nd2 执行口径' },
};
const DEFAULT_POOL = 'csi1000';

const qlibCode = inst => inst.replace(/^(SH|SZ|BJ)/, '');
const sinaUrl = inst => `https://finance.sina.com.cn/realstock/company/${inst.toLowerCase()}/nc.shtml`;

async function fetchText(url) { const r = await fetch(url); if (!r.ok) return null; return r.text(); }

function parseCSV(text) {
    if (!text) return [];
    const lines = text.trim().split('\n');
    const header = lines[0].split(',');
    return lines.slice(1).map(line => {
        const cols = line.split(',');
        const obj = {};
        header.forEach((h, i) => obj[h.trim()] = (cols[i] || '').trim());
        return obj;
    });
}

// 宽松 JSON 解析：历史文件可能含 Python json.dumps 输出的裸 NaN（非法 JSON，JSON.parse 整体失败）——NaN→null
function parseJSONLoose(text) { try { return JSON.parse(String(text).replace(/\bNaN\b/g, 'null')); } catch (e) { return null; } }

function makeSparkline(values, w, h) {
    // 过滤 null/NaN（停牌日）：至少保留 2 个有效点才画
    const vs = values.filter(v => v !== null && v !== undefined && !Number.isNaN(v)).map(Number);
    if (vs.length < 2) return '<span class="muted">—</span>';
    const min = Math.min(...vs), max = Math.max(...vs);
    const range = max - min || 1;
    const pts = vs.map((v, i) => `${(i/(vs.length-1)*w).toFixed(1)},${(h-(v-min)/range*h).toFixed(1)}`).join(' ');
    const color = vs[vs.length-1] >= vs[0] ? '#f85149' : '#3fb950';
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

async function loadNameMap() {
    const rows = parseCSV(await fetchText(NAME_MAP_URL));
    const map = {};
    rows.forEach(r => { if (r.code) map[r.code] = r.name; });
    return map;
}

async function loadAvailableDates(pool) {
    const suffix = POOLS[pool].suffix;
    const now = new Date();
    const candidates = [];
    for (let i = 0; i < 30; i++) {  // 回溯 30 个自然日（≈20 交易日）
        const d = new Date(now - i * 86400000);
        if (d.getDay() === 0 || d.getDay() === 6) continue;
        candidates.push(d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0'));
    }
    const results = await Promise.all(candidates.map(async ymd => {
        const text = await fetchText(`${CSV_BASE}/${ymd}_top20_lgb158${suffix}.csv`);
        return text ? { date: ymd, text } : null;
    }));
    return results.filter(Boolean).sort((a, b) => b.date.localeCompare(a.date));
}

// 渲染行数据（统一数据流：桌面表格行 + 移动卡片共用）
function buildRows(dateInfo, nameMap, prev, chartData) {
    const { date, text } = dateInfo;
    const rows = parseCSV(text);
    const prevCodes = new Set(prev ? prev.map(r => r.instrument) : []);
    const prevRank = {};
    if (prev) prev.forEach(r => prevRank[r.instrument] = parseInt(r.rank));

    return rows.map(r => {
        const code = qlibCode(r.instrument);
        const name = nameMap[code] || '——';
        const score = (parseFloat(r.score) * 100).toFixed(1);
        const rank = parseInt(r.rank);
        let tag = '';
        if (!prevCodes.has(r.instrument)) tag = '<span class="tag tag-new">新进</span>';
        else if (rank < prevRank[r.instrument]) tag = '<span class="tag tag-up">↑</span>';
        else if (rank > prevRank[r.instrument]) tag = '<span class="tag tag-dn">↓</span>';
        else tag = '<span class="tag tag-hold">—</span>';
        let spark = '';
        if (chartData && chartData.stocks[r.instrument] && chartData.stocks[r.instrument].length >= 2) {
            spark = makeSparkline(chartData.stocks[r.instrument], 80, 24);
        } else { spark = '<span class="muted">—</span>'; }
        return { rank, instrument: r.instrument, name, score, tag, spark, sinaUrl: sinaUrl(r.instrument), date };
    });
}

async function main(pool) {
    pool = POOLS[pool] ? pool : DEFAULT_POOL;
    const suffix = POOLS[pool].suffix;
    const [nameMap, dates] = await Promise.all([loadNameMap(), loadAvailableDates(pool)]);
    const app = document.getElementById('app');
    if (!dates.length) { app.innerHTML = '<p style="text-align:center;color:#8b949e">暂无信号数据</p>'; return; }

    const latest = dates[0];
    const prev = dates.length > 1 ? parseCSV(dates[1].text) : null;
    let chartData = null;
    const ct = await fetchText(`${CSV_BASE}/${latest.date}_chart${suffix}.json`);
    if (ct) { try { chartData = parseJSONLoose(ct); } catch(e) {} }

    // --- 骨架 ---
    const poolTabs = Object.keys(POOLS).map(p =>
        `<button class="date-btn pool-btn${p === pool ? ' current' : ''}" data-pool="${p}">${POOLS[p].label}</button>`
    ).join('<span class="muted">·</span>');
    app.innerHTML = `
        <header>
            <h1>A 股每日选股信号</h1>
            <p class="pool-tabs">${poolTabs}</p>
            <p class="subtitle">${POOLS[pool].label} · ${POOLS[pool].desc} · <a href="methodology.html" style="color:inherit;text-decoration:underline;">策略方法论</a></p>
            <p class="date">📅 ${latest.date}</p>
        </header>
        <div id="table-wrap">
            <table class="signal-table">
                <thead><tr><th>#</th><th>代码</th><th>名称</th><th>预测20日收益</th><th>变化</th><th>近60日</th></tr></thead>
                <tbody id="signal-rows"></tbody>
            </table>
        </div>
        <div id="cards"></div>
        <div class="history">
            <h3>历史榜单</h3>
            <div id="history-buttons" class="date-buttons">${
                dates.slice(0, 20).map(d => `<button class="date-btn${d.date === latest.date ? ' current' : ''}" data-date="${d.date}">${d.date}</button>`).join('')
            }</div>
        </div>
        <footer>
            <p>⚠️ 仅供研究参考，不构成投资建议 · 据此操作风险自负</p>
            <p>每个交易日 07:00 自动更新 · 当前生产信号仅中证1000</p>
        </footer>`;

    const render = (rows) => {
        // 桌面表格行
        document.getElementById('signal-rows').innerHTML = rows.map(r => `
            <tr><td class="rank">${r.rank}</td>
                <td><code><a href="${r.sinaUrl}" target="_blank" rel="noopener">${r.instrument}</a></code></td>
                <td class="name">${r.name}</td>
                <td class="score red">+${r.score}%</td>
                <td>${r.tag}</td>
                <td class="spark">${r.spark}</td></tr>`).join('');
        // 移动卡片
        document.getElementById('cards').innerHTML = rows.map(r => `
            <div class="stock-card">
                <div class="card-top"><span class="rank">${r.rank}</span>
                    <code><a href="${r.sinaUrl}" target="_blank" rel="noopener">${r.instrument}</a></code>
                    <span class="name">${r.name}</span>${r.tag}</div>
                <div class="card-bottom"><span class="score red">+${r.score}%</span>${r.spark}</div>
            </div>`).join('');
    };

    render(buildRows(latest, nameMap, prev, chartData));
    const switchDate = async (d) => {
        const pd = dates.find(x => x.date < d.date);
        let sc = null;
        const sct = await fetchText(`${CSV_BASE}/${d.date}_chart${suffix}.json`);
        if (sct) { try { sc = parseJSONLoose(sct); } catch(e) {} }
        render(buildRows(d, nameMap, pd ? parseCSV(pd.text) : null, sc));
        document.querySelector('.date').textContent = `📅 ${d.date}`;
        // 高亮当前选中的日期按钮
        document.querySelectorAll('.date-btn:not(.pool-btn)').forEach(b =>
            b.classList.toggle('current', b.dataset.date === d.date));
    };

    document.getElementById('history-buttons').querySelectorAll('.date-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const d = dates.find(x => x.date === btn.dataset.date);
            if (d) switchDate(d);
        });
    });
    // 池切换：URL 带 pool 参数（可分享/收藏），重新渲染整页
    document.querySelectorAll('.pool-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.pool === pool) return;
            const u = new URL(location.href);
            u.searchParams.set('pool', btn.dataset.pool);
            location.href = u.toString();
        });
    });
}

// 入口：未知/历史 pool 参数会被 main() 的 allowlist 自动回退到 csi1000。
main(new URLSearchParams(location.search).get('pool') || 'csi1000');
