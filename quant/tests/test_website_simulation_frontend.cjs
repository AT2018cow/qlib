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
    fit_receipts: [{ fit_asof: '2026-09-30', signal_end: '2026-10-08',
      profile: '59b93929_top10_drop1_10d', model_sha256: 'c'.repeat(64), data_prefix_sha256: 'd'.repeat(64),
      training_contract: { asof: '2026-09-30', horizon: 10, train_end: '2025-08-15', valid_end: '2026-09-14' } }],
    points: [{ date: '2026-10-08', nav: 0.99, daily_return: -0.01, cumulative_return: -0.01,
      portfolio_value_close: 990000, cash: 10000, cost: 500, data_mode: 'CAUSAL_SIMULATION' }] };
}

test('October 8 first-close return is displayed with correct market identity', () => {
  assert.equal(normalizeForwardPerformance(series(), 'csi300').status, 'active');
  assert.equal(normalizeForwardPerformance(series(), 'csi500').status, 'invalid');
});
test('verified dated ST policy requires a history hash and source', () => {
  const verified = series();
  verified.execution_st_policy = 'ST_VERIFIED_HISTORY';
  assert.equal(normalizeForwardPerformance(verified).status, 'invalid');
  verified.st_history_sha256 = 'f'.repeat(64);
  verified.st_history_source = 'test:dated-st';
  assert.equal(normalizeForwardPerformance(verified).status, 'active');
  verified.st_history_sha256 = 'not-a-hash';
  assert.equal(normalizeForwardPerformance(verified).status, 'invalid');
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

test('fit receipts require valid hashes, coverage and explicit version 2 point binding', () => {
  const valid = series();
  valid.fit_receipt_version = 2;
  Object.assign(valid.points[0], { signal_date: '2026-09-30', fit_asof: '2026-09-30', model_sha256: 'c'.repeat(64) });
  assert.equal(normalizeForwardPerformance(valid, 'csi300').status, 'active');
  for (const change of [s => s.fit_receipts[0].model_sha256 = '',
    s => s.fit_receipts[0].data_prefix_sha256 = ['d'.repeat(64)],
    s => s.fit_receipts[0].signal_end = '2026-09-29',
    s => s.fit_receipts.push(structuredClone(s.fit_receipts[0])),
    s => s.points[0].model_sha256 = 'e'.repeat(64),
    s => s.points[0].signal_date = '2026-10-08', s => s.fit_receipt_version = 99]) {
    const raw = structuredClone(valid); change(raw);
    assert.equal(normalizeForwardPerformance(raw, 'csi300').status, 'invalid');
  }
});

test('new website identity fields are display-compatible and do not change NAV', () => {
  const withIdentities = series();
  Object.assign(withIdentities, {
    identity_schema_version: 2,
    release_source_sha256: 'e'.repeat(64),
    website_semantic_sha256: 'f'.repeat(64),
    account_contract_sha256: '0'.repeat(64),
  });
  const normalized = normalizeForwardPerformance(withIdentities, 'csi300');
  assert.equal(normalized.status, 'active');
  assert.equal(normalized.cumulative_return, -0.01);
});

test('methodology and ranking UI disclose v2 raw TopK and postclose schedule', () => {
  const methodology = fs.readFileSync(path.join(__dirname, '../website/methodology.html'), 'utf8');
  for (const text of ['18:00', '23:00', '23:15', '07:00', '公开榜单合同 v2', '不因 T 日收盘涨跌停删除股票']) {
    assert.ok(methodology.includes(text), `methodology missing ${text}`);
  }
  assert.ok(!methodology.includes('19:00 幂等重试'), 'historical 19:00 retry must not remain current');
  assert.ok(source.includes('榜单与模拟账户使用同一套模型原始排名（合同 v2）'));
});
