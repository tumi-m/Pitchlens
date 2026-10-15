const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// lib/review/analysis.ts, with a stub for its './vision' import (fetch helpers, unused here).
const source = fs.readFileSync(path.join(__dirname, '../lib/review/analysis.ts'), 'utf8');
const mod = { exports: {} };
const stub = { VisionError: class VisionError extends Error {}, visionJson: () => Promise.reject(new Error('not in tests')) };
const load = (name) => (name === './vision' ? stub : require(name));
new Function('module', 'exports', 'require', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(mod, mod.exports, load);
const { pitchLines, PITCH_PRESETS } = mod.exports;

const close = (a, b, tolerance = 1e-9) => Math.abs(a - b) <= tolerance;
const template = (name) => PITCH_PRESETS[name].template;
// What backend pitch.line_segments draws for the same templates (tests/test_pitch.py).
const BACKEND_POLYLINES = { 'five-a-side': 7, futsal: 8, 'seven-a-side': 12, 'eleven-a-side': 20 };

test('the overlay draws as many lines as the worker aligns to, per template', () => {
  for (const [name, count] of Object.entries(BACKEND_POLYLINES)) assert.equal(pitchLines(template(name)).length, count, name);
  const custom = { length: 100, width: 64, goalWidth: 7.32, centreRadius: 9.15, areaRadius: null, areaDepth: 16.5, areaWidth: 40.32, penaltySpot: 11 };
  assert.equal(pitchLines(custom).length, 20);
  const court = { length: 30, width: 18, goalWidth: 3, centreRadius: 3, areaRadius: null, areaDepth: 8.5, areaWidth: 5, penaltySpot: 6 };
  assert.equal(pitchLines(court).length, 12);
});

test('an 11-a-side pitch shows both goal areas and the penalty arcs outside the box', () => {
  const t = template('eleven-a-side');
  const { length: L, width: W } = t;
  const lines = pitchLines(t);
  for (const x of [5.5, L - 5.5]) {
    const front = lines.find((line) => line.every(([px]) => close(px, x)));
    assert.ok(front, `goal area front at x=${x}`);
    const ys = front.map(([, y]) => y);
    assert.ok(close(Math.min(...ys), W / 2 - 9.16) && close(Math.max(...ys), W / 2 + 9.16));
  }
  const [left, right] = lines.slice(-2);
  for (const [line, cx, outside] of [
    [left, 11, (x) => x >= 16.5 - 1e-9],
    [right, L - 11, (x) => x <= L - 16.5 + 1e-9],
  ]) {
    assert.ok(line.length > 8);
    for (const [x, y] of line) {
      assert.ok(close(Math.hypot(x - cx, y - W / 2), 9.15, 0.01));
      assert.ok(outside(x));
    }
  }
});

test('seven-a-side and futsal get no goal area and no 9.15 m arc', () => {
  for (const name of ['seven-a-side', 'futsal']) {
    const t = template(name);
    const lines = pitchLines(t);
    const arcAroundSpot = lines.some((line) => t.penaltySpot && line.every(([x, y]) => close(Math.hypot(x - t.penaltySpot, y - t.width / 2), 9.15, 0.01)));
    assert.equal(arcAroundSpot, false, name);
    assert.equal(lines.some((line) => line.every(([x]) => close(x, 5.5))), false, name);
  }
});
