const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../lib/review/overlay.ts'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText)(mod, mod.exports);
const { overlayAt, playerMaps } = mod.exports;

const player = (id, x, y = 100) => ({ id, team: 0, box: [x, y, x + 20, y + 40], confidence: 0.9 });
const frame = (t, players, { scene = 0, ball = { x: 10 * t, y: 50, box: null, confidence: 0.8 } } = {}) => ({ t, scene, players, ball });
const at = (frames, index, time, fps = 3) => overlayAt(frames, index, time, fps, playerMaps(frames));

test('halfway between two analysed frames a box is at the midpoint', () => {
  const frames = [frame(0, [player(1, 100)]), frame(1 / 3, [player(1, 130, 106)])];
  const { players } = at(frames, 0, 1 / 6);
  assert.deepEqual(players[0].box.map((v) => Math.round(v * 1000) / 1000), [115, 103, 135, 143]);
  assert.equal(players[0].id, 1);
  // Exactly on the held frame, and at the next one, nothing moves past the samples.
  assert.deepEqual(at(frames, 0, 0).players[0].box, [100, 100, 120, 140]);
  assert.deepEqual(at(frames, 0, 1).players[0].box, [130, 106, 150, 146]);
});

test('a scene change or a gap over 1.5 sample intervals keeps the held box', () => {
  const cut = [frame(0, [player(1, 100)]), frame(1 / 3, [player(1, 300)], { scene: 1 })];
  assert.deepEqual(at(cut, 0, 1 / 6).players[0].box, [100, 100, 120, 140]);
  const gap = [frame(0, [player(1, 100)]), frame(0.51, [player(1, 300)])];
  assert.deepEqual(at(gap, 0, 0.25).players[0].box, [100, 100, 120, 140]);
  // Just inside the limit it moves.
  const close = [frame(0, [player(1, 100)]), frame(0.49, [player(1, 149)])];
  assert.ok(at(close, 0, 0.245).players[0].box[0] > 120);
});

test('a track seen in only one of the two frames is drawn where it was', () => {
  const frames = [frame(0, [player(1, 100), player(2, 400)]), frame(1 / 3, [player(1, 130), player(3, 50)])];
  const { players } = at(frames, 0, 1 / 6);
  assert.deepEqual(players.map((p) => p.id), [1, 2]);
  assert.equal(players[0].box[0], 115);
  assert.deepEqual(players[1].box, [400, 100, 420, 140]);
});

test('the ball is never interpolated', () => {
  const frames = [frame(0, [player(1, 100)]), frame(1 / 3, [player(1, 130)])];
  assert.deepEqual(at(frames, 0, 1 / 6).ball, frames[0].ball);
  const lastFrame = at(frames, 1, 0.5);
  assert.deepEqual(lastFrame.ball, frames[1].ball);
  assert.deepEqual(lastFrame.players[0].box, [130, 100, 150, 140]);
});

test('an index with no frame draws nothing', () => {
  assert.deepEqual(at([], 0, 0), { players: [], ball: null });
});
