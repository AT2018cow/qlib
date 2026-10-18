// R04 frontend security contract: external names/CSV never become live HTML.
// Loads the sanitizer slice and the table renderers from app.js without a DOM.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'website', 'app.js'), 'utf8');

const helperStart = source.indexOf('function parseCSV(');
const helperEnd = source.indexOf('function calendarAlignChartValues(', helperStart);
assert.ok(helperStart >= 0 && helperEnd > helperStart, 'sanitizer helpers found');

const rowsStart = source.indexOf('function tableRows(');
const rowsEnd = source.indexOf('function renderHistoryButtons(', rowsStart);
assert.ok(rowsStart >= 0 && rowsEnd > rowsStart, 'table renderers found');

const { escapeHtml, sanitizeNameMap, parseCSV, tableRows, mobileCards } = vm.runInNewContext(
  source.slice(helperStart, helperEnd) +
  source.slice(rowsStart, rowsEnd) +
  ';({ escapeHtml, sanitizeNameMap, parseCSV, tableRows, mobileCards })'
);

test('escapeHtml neutralizes markup and attribute breakers', () => {
  const payload = '<img src=x onerror=alert(1)> & "quoted" \'single\'';
  const escaped = escapeHtml(payload);
  assert.ok(!escaped.includes('<img'));
  assert.ok(escaped.includes('&lt;img'));
  assert.ok(escaped.includes('&amp;'));
  assert.ok(!escaped.includes('"'));
  assert.ok(!escaped.includes("'"));
});

test('sanitizeNameMap drops markup/control/oversized names', () => {
  const names = sanitizeNameMap({
    '600000': '浦发银行',
    '000001': '<img src=x onerror=alert(1)>',
    '000002': 'bad"quote',
    '000003': 'x'.repeat(41),
    '000004': 42,
    'SH600000': 'wrong key',
  });
  assert.deepEqual(Object.keys(names), ['600000']);
  assert.equal(names['600000'], '浦发银行');
});

test('parseCSV rejects hostile or malformed rows and keeps valid files', () => {
  const valid = 'rank,instrument,score\n1,SH600000,0.5\n2,SZ000001,0.25\n';
  assert.equal(parseCSV(valid).length, 2);
  const hostile = 'rank,instrument,score\n1,SH600000," onmouseover="alert(1)\n';
  assert.equal(parseCSV(hostile).length, 0);
  const bad = [
    'rank,instrument,score\n1,SH60000,0.5\n',
    'rank,instrument,score\n0,SH600000,0.5\n',
    'rank,instrument,score\n2,SH600000,0.5\n',
    'rank,instrument,score\n1,SH600000,0.5\n1,SZ000001,0.2\n',
    'rank,instrument,score\n1,SH600000,NaN\n',
    'not_a_header\n1,SH600000,0.5\n',
  ];
  for (const text of bad) assert.equal(parseCSV(text).length, 0, text);
  const tooMany = 'rank,instrument,score\n' +
    Array.from({ length: 250 }, (_, i) => `${i + 1},SH600000,0.5`).join('\n');
  assert.equal(parseCSV(tooMany).length, 0);
});

test('renderers escape name/instrument/url instead of emitting live markup', () => {
  const row = {
    rank: 1,
    instrument: 'SH600000',
    name: '<img src=x onerror=alert(1)>',
    score: { text: '+1.0%', klass: 'positive' },
    tag: '',
    spark: '',
    url: 'https://example.test/?x=<script>',
  };
  for (const output of [tableRows([row]), mobileCards([row])]) {
    assert.ok(!output.includes('<img'));
    assert.ok(!output.includes('<script'));
    assert.ok(output.includes('&lt;img'));
    assert.ok(output.includes('&lt;script&gt;'));
  }
});
