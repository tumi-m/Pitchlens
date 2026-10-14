const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const ts = require('typescript');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pitchlens-frames-'));
const source = fs.readFileSync(path.join(__dirname, '../lib/review/frames.ts'), 'utf8');
fs.writeFileSync(path.join(dir, 'frames.js'), ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText);
after(() => fs.rmSync(dir, { recursive: true, force: true }));
const { seekTarget, frameIndexAt, nearestFrame, analysedFrameTime } = require(path.join(dir, 'frames.js'));

// Real numbers from the Tryzub clip: 29.97 fps, about 3 analysed frames a second.
// 90.357 was rounded down from 90.357024; 90.691 was rounded up from 90.690691.
const FPS = 29.97;
const SAMPLE = 2.997;
const frames = [{ t: 90.023 }, { t: 90.357 }, { t: 90.691 }, { t: 91.024 }];

test('seeking to a rounded-down analysed frame lands on that frame, not the one before', () => {
  assert.ok(seekTarget(90.357) > 90.357024);
  // Still well inside the same source frame (the next starts 33 ms later).
  assert.ok(seekTarget(90.357) < 90.357024 + 1 / FPS);
});

test('the overlay follows the frame on screen', () => {
  // The previous video frame (what a seek to exactly 90.357 showed) keeps the previous sample.
  assert.equal(frameIndexAt(frames, 90.323657, FPS, SAMPLE), 0);
  assert.equal(frameIndexAt(frames, 90.357024, FPS, SAMPLE), 1);
  // A presented frame just below a rounded-up t picks that sample, not the one before.
  assert.equal(frameIndexAt(frames, 90.690691, FPS, SAMPLE), 2);
  assert.equal(frameIndexAt(frames, 90.8, FPS, SAMPLE), 2);
  // Gaps in the analysis draw nothing.
  assert.equal(frameIndexAt(frames, 95, FPS, SAMPLE), -1);
  assert.equal(frameIndexAt([], 90, FPS, SAMPLE), -1);
});

test('pitch setup uses the stored time of the analysed frame on screen', () => {
  assert.equal(analysedFrameTime(frames, 90.690691, FPS), 90.691);
  assert.equal(analysedFrameTime(frames, 90.357024, FPS), 90.357);
  assert.equal(analysedFrameTime(frames, seekTarget(90.357), FPS), 90.357);
  // Between analysed frames: keep the decoder's time.
  assert.equal(analysedFrameTime(frames, 90.5, FPS), null);
  assert.equal(analysedFrameTime([], 90.5, FPS), null);
});

test('nearest analysed frame', () => {
  assert.equal(nearestFrame(frames, 90.5), 1);
  assert.equal(nearestFrame(frames, 90.6), 2);
  assert.equal(nearestFrame(frames, 0), 0);
  assert.equal(nearestFrame(frames, 200), 3);
});
