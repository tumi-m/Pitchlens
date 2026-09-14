const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const ts = require('typescript');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pitchlens-tests-'));
for (const name of ['types', 'portable']) {
  const source = fs.readFileSync(path.join(__dirname, '../lib/review', `${name}.ts`), 'utf8');
  fs.writeFileSync(path.join(dir, `${name}.js`), ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText);
}
after(() => fs.rmSync(dir, { recursive: true, force: true }));
const { parseReviewExport, validateEvent, fingerprintVideo, validateLinkedVideo } = require(path.join(dir, 'portable.js'));
const { summariseEvents } = require(path.join(dir, 'types.js'));
const fixture = () => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/review.json'), 'utf8'));
const parse = value => parseReviewExport(JSON.stringify(value));

test('portable round trip preserves evidence and recomputes observed counts', () => {
  const data = fixture();
  data.summary = [{ goals: 999 }]; data.userId = 'someone-else'; data.videoUrls = ['https://evil.test']; data.id = 'overwrite-me';
  const result = parse(data);
  assert.equal(result.review.events[0].note, 'Synthetic portability test');
  assert.equal(summariseEvents(result.review.events)[0].goals, 1);
  assert.equal(result.review.duration, 4);
  assert.ok(result.review.importedAt);
  for (const key of ['id','userId','videoUrls','summary','stats']) assert.equal(key in result, false);
});

test('edited events update counts and preserve source and identity', () => {
  const original = fixture().review.events[0];
  const event = validateEvent({ ...original, timestamp:2.5, team:'away', type:'shot', note:'Corrected' }, 4);
  assert.equal(event.id, original.id); assert.equal(event.source, 'manual');
  const [home, away] = summariseEvents([event]);
  assert.equal(home.goals, 0); assert.equal(away.shots, 1); assert.equal(away.goals, 0);
});

for (const timestamp of [-1, 4.1, Infinity, NaN, '2', null]) test(`reject invalid timestamp ${timestamp}`, () => {
  assert.throws(() => validateEvent({ ...fixture().review.events[0], timestamp }, 4), /timestamp/);
});

test('reject duplicate IDs and unsupported schemas', () => {
  const data = fixture(); data.review.events.push({ ...data.review.events[0] }); assert.throws(() => parse(data), /Duplicate/);
  data.schemaVersion = 2; assert.throws(() => parse(data), /Unsupported/);
});

test('reject remote frame URLs rather than fetching imported content', () => {
  const data = fixture(); data.review.frames = [{timestamp:1,image:'https://evil.test/tracker',width:640,height:360,predictions:[]}];
  assert.throws(() => parse(data), /embedded JPEG/);
});

test('reject malformed, oversized and prototype-shaped data', () => {
  assert.throws(() => parseReviewExport('{no}'), /valid JSON/);
  assert.throws(() => parseReviewExport(' '.repeat(3 * 1024 * 1024 + 1)), /3 MB/);
  assert.throws(() => parse({ ...fixture(), review: null }), /object/);
  const data = fixture(); data.review.events[0].team = '__proto__'; assert.throws(() => parse(data), /team/);
});

test('video matching rejects mismatches, allows renamed same-metadata legacy files', () => {
  const original = parse(fixture()).review;
  assert.doesNotThrow(() => validateLinkedVideo(original, {...original,fileName:'renamed.mp4'}));
  for (const change of [{fileSize:1},{duration:5},{width:1920},{height:1080}]) assert.throws(() => validateLinkedVideo(original,{...original,...change}), /does not match/);
});

test('fingerprint is stable and prevents matching a changed same-size sample', async () => {
  const first = await fingerprintVideo(new Blob(['original']));
  assert.equal(first, await fingerprintVideo(new Blob(['original'])));
  const second = await fingerprintVideo(new Blob(['modified']));
  assert.notEqual(first, second);
  const original = {...parse(fixture()).review,videoFingerprint:first};
  assert.throws(() => validateLinkedVideo(original,{...original,videoFingerprint:second}), /does not match/);
});
