// Frontend contract checks without external npm dependencies.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'website', 'app.js'), 'utf8');
const start = source.indexOf('function calendarAlignChartValues(');
const end = source.indexOf('async function loadAvailableDates(', start);
assert.ok(start >= 0 && end > start, 'sparkline functions found');
const { calendarAlignChartValues, makeSparkline } = vm.runInNewContext(
  source.slice(start, end) + ';({ calendarAlignChartValues, makeSparkline })'
);

test('legacy short data retains missing calendar prefix', () => {
  const values = calendarAlignChartValues([10, 12], ['D1', 'D2', 'D3', 'D4']);
  assert.deepEqual(Array.from(values), [null, null, 10, 12]);
});

test('null is not interpreted as zero or squeezed out', () => {
  const svg = makeSparkline([10, 11, null, 9, 10]);
  assert.match(svg, /class="sparkline-svg"/);
  assert.equal((svg.match(/<path /g) || []).length, 2);
  // x-coordinate after the gap keeps its original 5-point position.
  assert.ok(svg.includes('M 95.0 32.0'));
});

test('chart trend color follows first and last valid values', () => {
  assert.match(makeSparkline([10, 11, 13]), /stroke="#ff6b6b"/);
  assert.match(makeSparkline([13, 11, 10]), /stroke="#3ccf91"/);
  assert.match(makeSparkline([null, null, null]), /—/);
});

test('desktop table-cell layout remains intact; mobile uses flex only', () => {
  const css = fs.readFileSync(path.join(root, 'website', 'styles.css'), 'utf8');
  assert.match(css, /\.signal-table td\.spark\s*\{\s*display:\s*table-cell;/);
  assert.match(css, /\.mobile-cards \.spark\s*\{\s*display:\s*flex;/);
  assert.doesNotMatch(css, /(?:^|\n)\.spark\s*\{\s*display:\s*flex;/);
});


// Forward public performance must be validated before the UI can show a number.
const forwardStart = source.indexOf('const FORWARD_START =');
const forwardEnd = source.indexOf('function isWinnerCanonical(', forwardStart);
const dateFormatStart = source.indexOf('function formatDate(');
const dateFormatEnd = source.indexOf('function formatScore(', dateFormatStart);
assert.ok(forwardStart > 0 && forwardEnd > forwardStart && dateFormatEnd > dateFormatStart);
const nodes = new Map();
const doc = {
  getElementById(id) {
    if (!nodes.has(id)) {
      const node = {
        textContent: '', innerHTML: '', className: '',
        classList: { add(name) { node.className += ' ' + name; } },
      };
      nodes.set(id, node);
    }
    return nodes.get(id);
  },
};
const {
  normalizeForwardPerformance, isPossiblyStale,
  renderForwardPerformance, makePerformanceChart,
} = vm.runInNewContext(
  source.slice(dateFormatStart, dateFormatEnd) +
  source.slice(forwardStart, forwardEnd) +
  ';({ normalizeForwardPerformance, isPossiblyStale, renderForwardPerformance, makePerformanceChart })',
  { document: doc }
);
const firstPoint = {
  date: '2026-10-12', nav: 1.03,
  daily_return: 0.03, cumulative_return: 0.03,
};
const validForward = {
  start_date: '2026-10-12', status: 'active',
  latest_date: '2026-10-12', cumulative_return: 0.03,
  points: [firstPoint],
};

test('forward panel rejects pre-inception, malformed and contradictory data', () => {
  const validate = normalizeForwardPerformance;
  assert.equal(validate(validForward).status, 'active');
  assert.equal(validate({ ...validForward, start_date: '2026-09-30' }).status, 'invalid');
  assert.equal(validate({ ...validForward, points: [{ ...firstPoint, date: '2026-10-08' }] }).status, 'invalid');
  assert.equal(validate({ ...validForward, points: [{ ...firstPoint, nav: null }] }).status, 'invalid');
  assert.equal(validate({ ...validForward, points: [{ ...firstPoint, cumulative_return: NaN }] }).status, 'invalid');
  assert.equal(validate({ ...validForward, points: [{ ...firstPoint, nav: 2 }] }).status, 'invalid');
  assert.equal(validate({ ...validForward, cumulative_return: 0 }).status, 'invalid');
  assert.equal(validate({
    ...validForward,
    latest_date: '2026-10-13',
    points: [{ ...firstPoint, date: '2026-10-13' }],
  }).status, 'invalid', 'no return without the 2026-10-12 inception report');
  assert.equal(validate({
    ...validForward, points: [{ ...firstPoint, daily_return: 0.99 }],
  }).status, 'invalid', 'daily return must reconcile to NAV');
  assert.equal(validate({ ...validForward, latest_date: '2026-10-13' }).status, 'invalid');
  assert.equal(validate({
    ...validForward, points: [firstPoint, firstPoint],
  }).status, 'invalid', 'duplicate dates cannot be plotted');
});

test('forward awaiting state is not confused with a zero return', () => {
  const waiting = {
    start_date: '2026-10-12', status: 'awaiting_first_valuation',
    latest_date: null, cumulative_return: null, points: [],
  };
  assert.equal(normalizeForwardPerformance(waiting).status, 'awaiting_first_valuation');
  assert.equal(normalizeForwardPerformance({ ...waiting, cumulative_return: 0 }).status, 'invalid');
  renderForwardPerformance(waiting);
  assert.equal(nodes.get('forward-return').textContent, '待开始');
  renderForwardPerformance({ status: 'unavailable' });
  assert.equal(nodes.get('forward-return').textContent, '暂不可用');
  assert.match(nodes.get('forward-chart').innerHTML, /无法读取/);
  renderForwardPerformance({ status: 'invalid' });
  assert.equal(nodes.get('forward-return').textContent, '数据异常');
  assert.doesNotMatch(nodes.get('forward-chart').innerHTML, /\+0\.00%/);
});

test('validated forward curve labels dates and flags possibly stale valuations', () => {
  const chart = makePerformanceChart(validForward.points);
  assert.match(chart, /2026\.10\.12 · 起点/);
  assert.match(chart, /2026\.10\.12 · 最近估值/);
  assert.ok(isPossiblyStale('2026-10-12', new Date('2026-10-24T00:00:00Z')));
  assert.equal(isPossiblyStale('2026-10-12', new Date('2026-10-13T00:00:00Z')), false);
  assert.equal(makePerformanceChart([{ cumulative_return: NaN }]).includes('<svg'), false);
});

test('date selection is latest-request-wins, accessible and placed above the ranking', () => {
  assert.match(source, /if \(request !== latestDateRequest\) return;/);
  assert.match(source, /btn\.setAttribute\('aria-pressed', String\(active\)\)/);
  assert.ok(source.indexOf('id="history"') < source.indexOf('id="performance"'));
  assert.ok(source.indexOf('id="performance"') < source.indexOf('id="signal-rows"'));
  const css = fs.readFileSync(path.join(root, 'website', 'styles.css'), 'utf8');
  assert.match(css, /@media \(min-width: 701px\) and \(max-width: 860px\)/);
  assert.match(css, /\.performance-axis\s*\{/);
  assert.match(css, /overscroll-behavior-x: contain/);
  assert.match(css, /\.performance-line\s*\{\s*fill:\s*none;/);
  assert.doesNotMatch(css, /\.performance-line\.(gain|loss),\s*\.performance-dot\./);
});

test('Pages index replaces weekday probing and first-render barrier', () => {
  assert.match(source, /available_dates\.json/);
  assert.match(source, /fetch\(url, \{ cache: fresh \? 'no-cache' : 'default' \}\)/);
  assert.doesNotMatch(source, /cache: 'no-store'/);
  assert.doesNotMatch(source, /for \(let i = 0; i < 45; i\+\+\)/);
  assert.match(source, /const forwardPromise = loadForwardPerformance\(\);/);
  assert.match(source, /const index = await loadAvailableDates\(\);/);
  assert.match(source, /app\.innerHTML = pageShell\(dates\);/);
  assert.match(source, /forwardPromise\.then\(renderForwardPerformance\)/);
  assert.match(source, /const csvCache = new Map\(\);/);
  assert.match(source, /paint\(null, null\);/);
  assert.match(source, /paint\(previous, ctx\);/);
});
