const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// lib/review/uploadProgress.ts has no imports: run it as it is.
const source = fs.readFileSync(path.join(__dirname, '../lib/review/uploadProgress.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const lib = {};
vm.runInNewContext(code, { exports: lib });
const { formatBytes, percentLabel, etaSeconds, minutesAt10Mbit } = lib;
const MiB = 1024 * 1024;

test('sizes are 1024-based, as phones show them', () => {
  assert.equal(formatBytes(2_000_000_000), '1.9 GB');
  assert.equal(formatBytes(2 * 1024 * MiB), '2.0 GB');
  assert.equal(formatBytes(800 * MiB), '800 MB');
  assert.equal(formatBytes(4 * MiB), '4.0 MB');
  assert.equal(formatBytes(105_194), '103 KB');
});

test('a file over 1 GB shows tenths of a percent, rounded down; smaller files whole percents', () => {
  // 20 MiB of 2 GB: a whole percent would still read 1% for another 20 MB.
  assert.equal(percentLabel(20 * MiB, 2e9), '1.0%');
  assert.equal(percentLabel(4 * MiB, 2e9), '0.2%');
  assert.equal(percentLabel(0, 2e9), '0.0%');
  assert.equal(percentLabel(2e9, 2e9), '100.0%');
  assert.equal(percentLabel(4 * MiB, 10 * MiB), '40%');
  assert.equal(percentLabel(10 * MiB - 1, 10 * MiB), '99%');
  assert.equal(percentLabel(5, 0), '0%');
});

test('the time left waits for three acknowledgements, then uses the last minute of speed', () => {
  assert.equal(etaSeconds([], 100), null);
  assert.equal(etaSeconds([{ at: 0, sent: 0 }, { at: 1000, sent: 4 * MiB }], 100 * MiB), null);
  // Steady 4 MiB a second: 88 MiB left after 12 MiB.
  const steady = [0, 1, 2, 3].map((s) => ({ at: s * 1000, sent: s * 4 * MiB }));
  assert.equal(etaSeconds(steady, 100 * MiB), 22);
  // A slow start more than a minute ago no longer counts.
  const later = [{ at: 0, sent: 0 }, { at: 120_000, sent: 4 * MiB }, { at: 121_000, sent: 8 * MiB }, { at: 122_000, sent: 12 * MiB }];
  assert.equal(etaSeconds(later, 100 * MiB), 22);
  // Nothing moving: no estimate rather than "forever".
  assert.equal(etaSeconds([{ at: 0, sent: 5 }, { at: 1000, sent: 5 }, { at: 2000, sent: 5 }], 100), null);
});

test('upload minutes at 10 Mbit/s round up', () => {
  assert.equal(minutesAt10Mbit(2e9), 27);
  assert.equal(minutesAt10Mbit(1), 1);
});
