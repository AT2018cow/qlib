const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../website/app.js'), 'utf8');
const pools = source.slice(source.indexOf('const POOLS ='), source.indexOf('const POOL_ORDER'));
const functions = source.slice(source.indexOf('const FORWARD_START ='), source.indexOf('function isPossiblyStale('));
const { normalizeForwardPerformance } = vm.runInNewContext(
  "const CSV_BASE = 'signals'; const DATE_INDEX_URL = 'signals/available_dates.json';" +
  pools + functions + ';({ normalizeForwardPerformance })');

function series() {
  return { schema_version: 3, status: 'active', data_mode: 'CAUSAL_SIMULATION',
    market: 'csi300', initial_cash: 1000000, initial_nav: 1,
    account_identity: { market: 'csi300', profile: '59b93929_top10_drop1_10d',
      paper_lineage: '59b93929_top10_drop1_10d_website_oct08_v2', initial_cash: 1000000,
      topk: 10, nd: 1, horizon: 10 },
    source_manifest_sha256: 'a'.repeat(64), provider_archive_sha256: 'b'.repeat(64),
    start_date: '2026-10-08', inception_date: '2026-10-08', latest_date: '2026-10-08',
    trading_dates: ['2026-10-08'], cumulative_return: -0.01, total_fees: 500,
    execution_st_policy: 'ST_HISTORY_UNVERIFIED_REFERENCE_ONLY',
    points: [{ date: '2026-10-08', nav: 0.99, daily_return: -0.01, cumulative_return: -0.01,
      portfolio_value_close: 990000, cash: 10000, cost: 500, data_mode: 'CAUSAL_SIMULATION' }] };
}

test('October 8 first-close return is displayed with correct market identity', () => {
  assert.equal(normalizeForwardPerformance(series(), 'csi300').status, 'active');
  assert.equal(normalizeForwardPerformance(series(), 'csi500').status, 'invalid');
});
test('incorrect old capital, missing dates and inconsistent fees cannot display a return', () => {
  for (const change of [s => s.initial_cash = 100000000, s => s.trading_dates = [],
    s => s.points[0].nav = null, s => s.total_fees = 0, s => s.points[0].cumulative_return = 0]) {
    const s = series(); change(s);
    assert.equal(normalizeForwardPerformance(s).status, 'invalid');
  }
});

test('current market panel refuses an obsolete cumulative-return file', () => {
  const legacy = { start_date: '2026-09-30', inception_date: '2026-09-30', status: 'active',
    latest_date: '2026-09-30', cumulative_return: 0.03,
    points: [{ date: '2026-09-30', nav: 1.03, daily_return: 0.03, cumulative_return: 0.03 }] };
  assert.equal(normalizeForwardPerformance(legacy, 'csi300').status, 'invalid');
});
