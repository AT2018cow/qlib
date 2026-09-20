// ============================================================
// 每日选股结果展示 —— 纯静态单页应用
// 数据源: GitHub 仓库 results/signals/*.csv (每日 cron 自动推送)
// 名称映射: results/signals/code_name_map.csv
// 托管: GitHub Pages
// 免责声明: 仅供学术研究参考,不构成投资建议(not investment advice)
// ============================================================
const CSV_BASE = 'signals';  // GitHub Pages 下相对于 gh-pages 的路径
const NAME_MAP_URL = `${CSV_BASE}/code_name_map.csv`;

// ---------- 工具函数 ----------
const qlibCode = inst => inst.replace(/^(SH|SZ|BJ)/, '');  // SH600064 -> 600064

async function fetchText(url) {
    const r = await fetch(url);
    if (!r.ok) return null;
    return r.text();
}

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

// ---------- 数据加载 ----------
async function loadNameMap() {
    const text = await fetchText(NAME_MAP_URL);
    const rows = parseCSV(text);
    const map = {};
    rows.forEach(r => { if (r.code) map[r.code] = r.name; });
    return map;
}

async function loadAvailableDates() {
    // GitHub Pages 无目录列表 API——用已知的日期规则尝试（从今天回溯 30 天找文件）
    const dates = [];
    const now = new Date();
    for (let i = 0; i < 45; i++) {
        const d = new Date(now - i * 86400000);
        const dow = d.getDay();
        if (dow === 0 || dow === 6) continue;  // 跳过周末
        const ymd = d.toISOString().slice(0, 10);
        const text = await fetchText(`${CSV_BASE}/${ymd}_top20_lgb158.csv`);
        if (text) dates.push({ date: ymd, text });
    }
    return dates.sort((a, b) => b.date.localeCompare(a.date));
}

// ---------- 渲染 ----------
function render(dateInfo, nameMap) {
    const { date, text } = dateInfo;
    const rows = parseCSV(text);
    const app = document.getElementById('app');

    const stockRow = (r, prevSet, change) => {
        const code = qlibCode(r.instrument);
        const name = nameMap[code] || '——';
        const score = (parseFloat(r.score) * 100).toFixed(1);
        const changeTag = change === 'new' ? '<span class="tag tag-new">新进</span>'
                        : change === 'out' ? '<span class="tag tag-out">退出</span>'
                        : change === 'in'  ? '<span class="tag tag-in">留存↑</span>'
                        : change === 'dn'  ? '<span class="tag tag-dn">留存↓</span>'
                        : '';
        return `<tr>
            <td class="rank">${r.rank}</td>
            <td><code>${r.instrument}</code></td>
            <td class="name">${name}</td>
            <td class="score">+${score}%</td>
            <td>${changeTag}</td>
        </tr>`;
    };

    // 计算与前一期的变化
    let changeHtml = '<div class="card"><h3>📋 榜单说明</h3><p>分数 = 模型预测的<b>未来 20 个交易日收益率</b>（越高越看好）。榜单仅为排名（ranking-only），非可执行交易订单。执行时请遵守操作手册的 T+1 保护规则。</p></div>';

    app.innerHTML = `
        <header>
            <h1>A 股每日选股信号</h1>
            <p class="subtitle">csi1000 · top20 · LightGBM + Alpha158 + 20 日标签</p>
            <p class="date">📅 ${date}</p>
        </header>
        ${changeHtml}
        <table class="signal-table">
            <thead><tr><th>#</th><th>代码</th><th>名称</th><th>预测 20 日收益</th><th>变化</th></tr></thead>
            <tbody id="signal-rows"></tbody>
        </table>
        <div class="history">
            <h3>历史榜单</h3>
            <div id="history-buttons" class="date-buttons"></div>
        </div>
        <footer>
            <p>⚠️ <b>免责声明</b>：本页仅为学术研究与实验性工程验证（research-only），不构成任何投资建议（not investment advice）。据此操作风险自负。</p>
            <p>数据管道：Modal Cron 每个交易日 20:30 自动训练/推理/推送 · <a href="https://github.com/AT2018cow/qlib" target="_blank">GitHub 仓库</a></p>
        </footer>
    `;
    document.getElementById('signal-rows').innerHTML = rows.map(r => stockRow(r)).join('');
}

// ---------- Sparkline ----------
function makeSparkline(values, w, h) {
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const pts = values.map((v, i) =>
        `${(i / (values.length - 1) * w).toFixed(1)},${(h - (v - min) / range * h).toFixed(1)}`
    ).join(' ');
    const up = values[values.length - 1] >= values[0];
    const color = up ? '#3fb950' : '#f85149';
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" /></svg>`;
}

// ---------- 变化对比 ----------
function diffRows(today, prev, nameMap) {
    const todayCodes = new Set(today.map(r => r.instrument));
    const prevCodes = new Set(prev ? prev.map(r => r.instrument) : []);
    return today.map(r => {
        if (!prevCodes.has(r.instrument)) return 'new';
        const prevRank = prev.findIndex(p => p.instrument === r.instrument) + 1;
        const curRank = parseInt(r.rank);
        if (curRank < prevRank) return 'in';
        if (curRank > prevRank) return 'dn';
        return '';
    });
}

// ---------- 入口 ----------
async function main() {
    const nameMap = await loadNameMap();
    const dates = await loadAvailableDates();
    if (!dates.length) {
        document.getElementById('app').innerHTML = '<p>暂无信号数据</p>';
        return;
    }
    // 渲染最新一期
    const latest = dates[0];
    const prev = dates.length > 1 ? parseCSV(dates[1].text) : null;
    render(latest, nameMap);

    // 变化对比重渲染 rows
    const today = parseCSV(latest.text);
    const changes = diffRows(today, prev);
    document.getElementById('signal-rows').innerHTML = today.map((r, i) => {
        const code = qlibCode(r.instrument);
        const name = nameMap[code] || '——';
        const score = (parseFloat(r.score) * 100).toFixed(1);
        const changeTag = changes[i] === 'new' ? '<span class="tag tag-new">新进</span>'
                        : changes[i] === 'in'  ? '<span class="tag tag-in">↑</span>'
                        : changes[i] === 'dn'  ? '<span class="tag tag-dn">↓</span>'
                        : '';
        return `<tr><td class="rank">${r.rank}</td><td><code>${r.instrument}</code></td><td class="name">${name}</td><td class="score">+${score}%</td><td>${changeTag}</td></tr>`;
    }).join('');

    // ---- 走势 sparkline ----
    const chartText = await fetchText(`${CSV_BASE}/${latest.date}_chart.json`);
    if (chartText) {
        try {
            const chart = JSON.parse(chartText);
            const rows = document.querySelectorAll('#signal-rows tr');
            rows.forEach((tr, i) => {
                const inst = today[i].instrument;
                const closes = chart.stocks[inst];
                if (!closes || closes.length < 2) return;
                const svg = makeSparkline(closes, 80, 24);
                tr.insertAdjacentHTML('beforeend', `<td class="spark">${svg}</td>`);
            });
            document.querySelector('.signal-table thead tr').insertAdjacentHTML('beforeend', '<th>近 60 日</th>');
        } catch (e) { console.warn('chart parse failed', e); }
    }

    // 历史日期按钮
    const historyDiv = document.getElementById('history-buttons');
    historyDiv.innerHTML = dates.slice(1).map(d =>
        `<button class="date-btn" data-date="${d.date}">${d.date}</button>`
    ).join('') || '<p>暂无更早数据（系统每日自动积累）</p>';
    historyDiv.querySelectorAll('.date-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const date = btn.dataset.date;
            const info = dates.find(d => d.date === date);
            if (info) render(info, nameMap);
        });
    });
}
main();
