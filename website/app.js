// ============================================================
// 每日选股结果展示 —— 纯静态单页应用
// 数据源: GitHub Pages 部署的 signals/*.csv (每日 cron 自动推送)
// 托管: GitHub Pages
// 免责声明: 仅供学术研究参考,不构成投资建议
// ============================================================
const CSV_BASE = 'signals';
const NAME_MAP_URL = `${CSV_BASE}/code_name_map.csv`;

// ---------- 工具函数 ----------
const qlibCode = inst => inst.replace(/^(SH|SZ|BJ)/, '');
const sinaUrl = inst => `https://finance.sina.com.cn/realstock/company/${inst.toLowerCase()}/nc.shtml`;

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

// ---------- Sparkline（红涨绿跌：A股惯例）----------
function makeSparkline(values, w, h) {
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const pts = values.map((v, i) =>
        `${(i / (values.length - 1) * w).toFixed(1)},${(h - (v - min) / range * h).toFixed(1)}`
    ).join(' ');
    const up = values[values.length - 1] >= values[0];
    const color = up ? '#f85149' : '#3fb950';  // A股红涨绿跌
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

// ---------- 名称映射 ----------
async function loadNameMap() {
    const text = await fetchText(NAME_MAP_URL);
    const rows = parseCSV(text);
    const map = {};
    rows.forEach(r => { if (r.code) map[r.code] = r.name; });
    return map;
}

// ---------- 历史日期列表（并行探测，解决加载慢）----------
async function loadAvailableDates() {
    const now = new Date();
    const candidates = [];
    for (let i = 0; i < 30; i++) {
        const d = new Date(now - i * 86400000);
        if (d.getDay() === 0 || d.getDay() === 6) continue;
        const ymd = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
        candidates.push(ymd);
    }
    const results = await Promise.all(candidates.map(async ymd => {
        const text = await fetchText(`${CSV_BASE}/${ymd}_top20_lgb158.csv`);
        return text ? { date: ymd, text } : null;
    }));
    return results.filter(Boolean).sort((a, b) => b.date.localeCompare(a.date));
}

// ---------- 渲染主表 ----------
function renderTable(dateInfo, nameMap, prev, chartData) {
    const { date, text } = dateInfo;
    const rows = parseCSV(text);

    // 变化对比
    const prevCodes = new Set(prev ? prev.map(r => r.instrument) : []);
    const prevRank = {};
    if (prev) prev.forEach(r => prevRank[r.instrument] = parseInt(r.rank));

    const tbody = rows.map(r => {
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
            spark = `<td class="spark">${makeSparkline(chartData.stocks[r.instrument], 80, 24)}</td>`;
        } else {
            spark = '<td class="spark muted">—</td>';
        }
        return `<tr><td class="rank">${rank}</td><td><code><a href="${sinaUrl(r.instrument)}" target="_blank" rel="noopener">${r.instrument}</a></code></td><td class="name">${name}</td><td class="score red">+${score}%</td><td>${tag}</td>${spark}</tr>`;
    }).join('');

    return { date, rows, tbody };
}

// ---------- 入口 ----------
async function main() {
    const [nameMap, dates] = await Promise.all([loadNameMap(), loadAvailableDates()]);
    const app = document.getElementById('app');

    if (!dates.length) {
        app.innerHTML = '<p style="text-align:center;color:#8b949e">暂无信号数据</p>';
        return;
    }

    const latest = dates[0];
    const prev = dates.length > 1 ? parseCSV(dates[1].text) : null;

    // 并行拉走势
    const chartText = await fetchText(`${CSV_BASE}/${latest.date}_chart.json`);
    let chartData = null;
    if (chartText) { try { chartData = JSON.parse(chartText); } catch (e) {} }

    console.log('[debug] latest date:', latest.date, '| chartData stocks:', chartData ? Object.keys(chartData.stocks).length : 'null');
    const { date, tbody } = renderTable(latest, nameMap, prev, chartData);

    app.innerHTML = `
        <header>
            <h1>A 股每日选股信号</h1>
            <p class="subtitle">csi1000 · top20 · 预测未来 20 日收益</p>
            <p class="date">📅 ${date}</p>
        </header>
        <table class="signal-table">
            <thead><tr><th>#</th><th>代码</th><th>名称</th><th>预测20日收益</th><th>变化</th><th>近60日</th></tr></thead>
            <tbody>${tbody}</tbody>
        </table>
        <div class="history">
            <h3>历史榜单</h3>
            <div id="history-buttons" class="date-buttons">${
                dates.slice(1).map(d => `<button class="date-btn" data-date="${d.date}">${d.date}</button>`).join('') ||
                '<p class="muted">暂无更早数据（系统每日自动积累）</p>'
            }</div>
        </div>
        <footer>
            <p>⚠️ 仅供研究参考，不构成投资建议 · 据此操作风险自负</p>
            <p><a href="https://github.com/AT2018cow/qlib" target="_blank">GitHub 仓库</a> · 每个交易日 20:30 自动更新</p>
        </footer>
    `;

    // "回到最新"按钮
    const backBtn = document.createElement('button');
    backBtn.className = 'date-btn back-btn';
    backBtn.textContent = '← 回到最新';
    backBtn.style.display = 'none';
    backBtn.addEventListener('click', async () => {
        const chartText2 = await fetchText(`${CSV_BASE}/${latest.date}_chart.json`);
        let cd2 = null;
        if (chartText2) { try { cd2 = JSON.parse(chartText2); } catch (e) {} }
        const { tbody } = renderTable(latest, nameMap, prev, cd2);
        document.querySelector('.signal-table tbody').innerHTML = tbody;
        document.querySelector('.date').textContent = `📅 ${latest.date}`;
        backBtn.style.display = 'none';
    });
    document.querySelector('.history').prepend(backBtn);

    document.getElementById('history-buttons').querySelectorAll('.date-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const d = dates.find(x => x.date === btn.dataset.date);
            if (!d) return;
            const pd = dates.find(x => x.date < btn.dataset.date);
            const subChart = await fetchText(`${CSV_BASE}/${d.date}_chart.json`);
            let subChartData = null;
            if (subChart) { try { subChartData = JSON.parse(subChart); } catch (e) {} }
            const { tbody } = renderTable(d, nameMap, pd ? parseCSV(pd.text) : null, subChartData);
            document.querySelector('.signal-table tbody').innerHTML = tbody;
            document.querySelector('.date').textContent = `📅 ${d.date}`;
            backBtn.style.display = 'inline-block';
        });
    });
}
main();
