const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// lib/review/visionStats.ts (its only import is a type), run on raw engine results.
const source = fs.readFileSync(path.join(__dirname, '../lib/review/visionStats.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const lib = {};
vm.runInNewContext(code, { exports: lib });
// Arrays made inside the vm have another realm's prototype: compare plain copies.
const matchStats = (r) => JSON.parse(JSON.stringify(lib.matchStats(r)));

function result(metrics, extra = {}) {
  const frames = Array.from({ length: 10 }, (_, i) => ({
    t: i * 0.5, scene: 0, ball: null,
    players: [{ id: 1, team: 0, box: [10, 10, 20, 40], confidence: 0.9 }, { id: 2, team: 1, box: [50, 10, 60, 40], confidence: 0.9 }],
  }));
  return {
    analysedDuration: 5, analysedStart: 0, frames, ...extra,
    metrics: { sampledFrames: 10, playerFrames: 10, ballFrames: 0, unknownSeconds: 5, trackCount: 2, events: [], ...metrics },
  };
}

test('a player-load result (possession not measured) gives null figures, not zeros, and does not throw', () => {
  const stats = matchStats(result(
    { teamSeconds: null, possessionCoverage: null, possessionShare: [null, null], possessions: null },
    { ballSearch: 'none' },
  ));
  assert.deepEqual(stats.controlSeconds, [null, null]);
  assert.equal(stats.controlCoverage, null);
  assert.deepEqual(stats.controlShare, [null, null]);
  assert.deepEqual(stats.possessions, [null, null]);
  assert.equal(stats.shareRange, null);
  assert.equal(stats.evidenceScore, 10);
});

test('a ball result keeps its measured possession figures', () => {
  const stats = matchStats(result({ teamSeconds: [3, 1], possessionCoverage: 80, possessionShare: [75, 25] }));
  assert.deepEqual(stats.controlSeconds, [3, 1]);
  assert.equal(stats.controlCoverage, 80);
  assert.deepEqual(stats.controlShare, [75, 25]);
});

test('complementary shares add up to 100, so 59.5 / 40.5 reads 60 / 40', () => {
  const shares = (a, b) => JSON.parse(JSON.stringify(lib.shares(a, b)));
  assert.deepEqual(shares(59.5, 40.5), [60, 40]);
  assert.deepEqual(shares(40.5, 59.5), [41, 59]);
  assert.deepEqual(shares(50, 50), [50, 50]);
  // One side missing, or shares that are not complementary: each on its own.
  assert.deepEqual(shares(null, 40), [null, 40]);
  assert.deepEqual(shares(33.3, 33.3), [33, 33]);
  assert.deepEqual(shares(null, null), [null, null]);
});
