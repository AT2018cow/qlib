// Frontend contract checks without external npm dependencies.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'website', 'app.js'), 'utf8');
const start = source.indexOf('function calendarAlignChartValues(');
const end = source.indexOf('async function loadNameMap(', start);
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
