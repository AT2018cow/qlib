// End-to-end publication contract against the committed October artifacts.
//
// This test exists because component-level fixtures previously passed while the
// real dashboard data was missing paper/metadata sidecars: every date then
// rendered as "历史记录 / 校验信息不完整" with no data/fit dates, no rebalance
// plan and a "待开始" performance panel. It loads the actual files shipped to
// Pages and runs them through the same frontend contract functions.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const signals = path.join(root, 'data', 'signals');
const source = fs.readFileSync(path.join(root, 'website', 'app.js'), 'utf8');

function sliceBetween(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `missing ${startMarker} .. ${endMarker}`);
  return source.slice(start, end);
}

const { POOLS } = vm.runInNewContext(
  sliceBetween('const CSV_BASE', 'const POOL_ORDER') + ';({ POOLS })'
);
const { evaluateMvpContract } = vm.runInNewContext(
  sliceBetween('const TOP10_PROFILES', 'function buildRows') + ';({ evaluateMvpContract })'
);
const { normalizeForwardPerformance } = vm.runInNewContext(
  sliceBetween('const FORWARD_START', 'function isWinnerCanonical') +
  ';({ normalizeForwardPerformance })'
);

const MARKETS = [
  { key: 'csi300', suffix: '_csi300', pit: 300, topk: 10, horizon: 10, index: 'available_dates_csi300.json' },
  { key: 'csi500', suffix: '_csi500', pit: 500, topk: 20, horizon: 20, index: 'available_dates_csi500.json' },
  { key: 'csi1000', suffix: '', pit: 1000, topk: 10, horizon: 20, index: 'available_dates.json' },
];

const EXPECTED_DATES = [
  '2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13', '2026-10-14',
  '2026-10-15', '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21',
  '2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28',
  '2026-10-29', '2026-10-30', '2026-11-02',
].reverse();

function readJSON(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function readRows(file, expectedCount) {
  const lines = fs.readFileSync(file, 'utf8').trim().split('\n');
  const header = lines[0].split(',').map(h => h.trim());
  assert.deepEqual(header.slice(0, 3), ['rank', 'instrument', 'score'], file);
  const index = Object.fromEntries(header.map((h, i) => [h, i]));
  const rows = lines.slice(1).map(line => {
    const cols = line.split(',');
    return {
      rank: Number(cols[index.rank]),
      instrument: cols[index.instrument],
      score: Number(cols[index.score]),
    };
  });
  assert.equal(rows.length, expectedCount, file);
  return rows;
}

test('pool metadata matches the frozen per-market production profiles', () => {
  for (const market of MARKETS) {
    assert.equal(POOLS[market.key].horizonDays, market.horizon, market.key);
    assert.equal(POOLS[market.key].topk, market.topk, market.key);
  }
  assert.equal(POOLS.csi300.topk, 10);
  assert.equal(POOLS.csi500.topk, 20);
  assert.equal(POOLS.csi1000.topk, 10);
});

for (const market of MARKETS) {
  test(`${market.key}: every published date has a passing dashboard contract`, () => {
    const manifest = readJSON(path.join(signals, market.index));
    const manifestDates = new Set(manifest.dates);
    for (const date of EXPECTED_DATES) {
      assert.ok(manifestDates.has(date), `${market.key} index missing ${date}`);
    }
    assert.ok(
      fs.existsSync(path.join(signals, 'archive', 'legacy_100m_2026-10', '2026-11-02_paper_portfolio.json')),
      'legacy 100M 11/02 artifacts must remain archived'
    );

    const forward = normalizeForwardPerformance(
      readJSON(path.join(signals, `${market.key}_forward_performance.json`))
    );
    assert.equal(forward.status, 'active', `${market.key} forward series`);
    assert.equal(forward.start_date, '2026-09-30');
    assert.equal(forward.inception_date, '2026-09-30');
    assert.ok(forward.latest_date >= '2026-11-02', `${market.key} latest valuation`);
    assert.ok(forward.points.length >= EXPECTED_DATES.length + 1);
    const forwardDates = new Set(forward.points.map(p => p.date));
    for (const date of EXPECTED_DATES) {
      assert.ok(forwardDates.has(date), `${market.key} forward missing ${date}`);
    }

    for (const date of EXPECTED_DATES) {
      const rows = readRows(
        path.join(signals, `${date}_top20_lgb158${market.suffix}.csv`),
        market.topk
      );
      const paper = readJSON(path.join(signals, `${date}_paper_portfolio${market.suffix}.json`));
      const metadata = readJSON(path.join(signals, `${date}_mvp_metadata${market.suffix}.json`));

      assert.equal(paper.market, market.key, `${date} paper market`);
      assert.equal(paper.usage_date, date, `${date} paper usage date`);
      assert.equal(paper.data_mode, 'BACKFILL_RESEARCH', `${date} paper mode`);
      assert.ok(paper.signal_data_date < date, `${date} signal must precede usage`);
      assert.ok(paper.model_fit_asof, `${date} missing model fit date`);
      assert.ok(paper.positions && Object.keys(paper.positions).length > 0, `${date} empty positions`);
      assert.ok(Number.isFinite(paper.cash) && paper.cash >= 0, `${date} invalid cash`);
      assert.ok(paper.pending_orders, `${date} missing pending orders`);
      assert.equal(paper.pending_orders.execution_date, date, `${date} rebalance execution date`);
      assert.equal(paper.pending_orders.signal_date, paper.signal_data_date, `${date} rebalance signal date`);
      assert.ok(Array.isArray(paper.pending_orders.sell), `${date} sell plan`);
      assert.ok(Array.isArray(paper.pending_orders.buy), `${date} buy plan`);

      const contract = evaluateMvpContract(
        metadata, paper, date, rows,
        { key: market.key, expectedPitCount: market.pit, label: '' }
      );
      assert.equal(contract.ok, true, `${date} ${market.key}: ${contract.errors.join('; ')}`);
      assert.equal(contract.backfill, true, `${date} backfill marker`);
      assert.equal(contract.pitText, `${market.pit}只成分股 · 防泄漏校验通过`, `${date} PIT`);
    }
  });
}
