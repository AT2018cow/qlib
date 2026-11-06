const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../website/app.js'), 'utf8');

test('segmented NAV preserves ST fallback and backfill identity', () => {
  const start = source.indexOf('const FORWARD_START');
  const end = source.indexOf('function isWinnerCanonical', start);
  const { normalizeForwardPerformance } = vm.runInNewContext(
    source.slice(start, end) + ';({ normalizeForwardPerformance })');
  const raw = {
    schema_version: 2, data_mode: 'BACKFILL_PLUS_LIVE', account_identity: { market: 'csi300' },
    start_date: '2026-09-30', inception_date: '2026-09-30', latest_date: '2026-11-03',
    status: 'active', cumulative_return: 0.01, backfill_end_date: '2026-11-02',
    st_fallback_dates: ['2026-11-03'], points: [
      { date: '2026-09-30', nav: 1, daily_return: 0, cumulative_return: 0, data_mode: 'BACKFILL_RESEARCH' },
      { date: '2026-11-02', nav: 1, daily_return: 0, cumulative_return: 0, data_mode: 'BACKFILL_RESEARCH' },
      { date: '2026-11-03', nav: 1.01, daily_return: 0.01, cumulative_return: 0.01,
        data_mode: 'LIVE_PAPER', execution_st_policy: 'ST_IGNORED_REFERENCE_ONLY' },
    ],
  };
  assert.equal(normalizeForwardPerformance(raw).status, 'active');
  delete raw.points[2].execution_st_policy;
  assert.equal(normalizeForwardPerformance(raw).status, 'invalid');
});

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

test('old CSV cannot repaint while the next market index is pending or empty', async () => {
  const csv = deferred();
  const nextIndex = deferred();
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', dataset: {},
      classList: { toggle() {}, add() {} }, setAttribute() {},
      addEventListener(event, handler) { this.handler = handler; },
    });
    return elements.get(id);
  };
  const a = { key: 'a', suffix: '', topk: 10, nd: 1 };
  const b = { key: 'b', suffix: '_b', topk: 20, nd: 2 };
  const button = element('poolB'); button.dataset.pool = 'b';
  const context = {
    document: {
      getElementById: element,
      querySelector: () => element('table'),
      querySelectorAll: selector => selector === '.pool-btn' ? [button] : [],
    },
    POOLS: { a, b }, currentPool: a, CSV_BASE: '',
    loadForwardPerformance: () => Promise.resolve({ status: 'awaiting_first_valuation', points: [] }),
    loadAvailableDates: pool => pool?.key === 'b' ? nextIndex.promise :
      Promise.resolve({ dates: [{ date: '2026-11-03' }], nameMap: {} }),
    pageShell: () => '', savePool() {}, portfolioLabel: () => '', renderHistoryButtons: () => '',
    fetchText: () => csv.promise, parseCSV: () => [{ instrument: 'OLD', rank: 1, score: 1 }],
    loadDateContext: () => Promise.resolve({}), renderForwardPerformance() {},
    buildRebalance: () => [], renderRebalance() {}, buildRows: rows => rows,
    tableRows: () => 'OLD MARKET', mobileCards: () => 'OLD MARKET', formatDate: x => x,
    isWinnerCanonical: () => false,
    evaluateMvpContract: () => ({ ok: true, pitText: 'OK' }),
  };
  const main = source.slice(source.indexOf('async function main()'), source.lastIndexOf('main();'));
  await vm.runInNewContext(main + '\nmain();', context);
  const switching = button.handler();
  csv.resolve('csv');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('signal-rows').innerHTML, '');
  assert.equal(element('artifact-status').textContent, '加载中…');
  nextIndex.resolve({ dates: [], nameMap: {} });
  await switching;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element('signal-rows').innerHTML, '');
  assert.equal(element('artifact-status').textContent, '暂无信号');
});
