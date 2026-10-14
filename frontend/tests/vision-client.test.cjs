const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// The browser-side client (lib/review/vision.ts), run against a fake localStorage
// and a fake proxy. Node's Headers rejects non-Latin-1 values like browsers do.
const source = fs.readFileSync(path.join(__dirname, '../lib/review/vision.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const OWNER_KEY = 'pitchlens-vision-owner';
const ACCESS_KEY = 'pitchlens-vision-access';
const OLD = 'a'.repeat(32);
const FRESH = 'e'.repeat(32);

function setup({ stored = {}, respond } = {}) {
  const store = new Map(Object.entries(stored));
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  const events = [];
  const calls = [];
  const exports = {};
  vm.runInNewContext(code, {
    exports, localStorage, Headers, Response, URLSearchParams, DOMException, setTimeout, clearTimeout,
    crypto: globalThis.crypto,
    CustomEvent: class { constructor(type) { this.type = type; } },
    window: { dispatchEvent: (e) => events.push(e.type), addEventListener() {}, removeEventListener() {} },
    fetch: async (url, init) => {
      calls.push({ url, headers: init.headers });
      return respond ? respond(url, init) : Response.json({ ok: true });
    },
  });
  return { lib: exports, store, events, calls };
}

test('the owner key the proxy used is written back, so both copies converge', async () => {
  // localStorage was cleared and a fresh key minted; the proxy used the cookie's key.
  const s = setup({ stored: { [OWNER_KEY]: FRESH }, respond: () => Response.json([], { headers: { 'x-pitchlens-owner': OLD } }) });
  await s.lib.visionJson('jobs');
  assert.equal(s.calls[0].headers.get('x-pitchlens-owner'), FRESH);
  assert.equal(s.store.get(OWNER_KEY), OLD);
  await s.lib.visionJson('jobs');
  assert.equal(s.calls[1].headers.get('x-pitchlens-owner'), OLD);
});

test('a missing or malformed owner reply leaves the stored key alone, errors included', async () => {
  for (const header of [undefined, 'not-a-key']) {
    const s = setup({
      stored: { [OWNER_KEY]: FRESH },
      respond: () => Response.json({ detail: 'Job not found' }, { status: 404, headers: header ? { 'x-pitchlens-owner': header } : {} }),
    });
    await assert.rejects(s.lib.visionJson('jobs/x'), /Job not found/);
    assert.equal(s.store.get(OWNER_KEY), FRESH);
  }
});

test('invisible characters pasted with an access code are never sent', async () => {
  const s = setup({ stored: { [ACCESS_KEY]: '​pitch-2026​﻿ ' } });
  await s.lib.visionJson('jobs');
  assert.equal(s.calls[0].headers.get('x-pitchlens-access'), 'pitch-2026');
  assert.equal(s.lib.setVisionAccessCode('  Fußball2026‍\n'), true);
  assert.equal(s.store.get(ACCESS_KEY), 'Fußball2026');
  await s.lib.visionJson('jobs');
  assert.equal(s.calls[1].headers.get('x-pitchlens-access'), 'Fußball2026');
});

test('a stored code that cannot be sent is dropped and the code prompt opens, instead of a TypeError', async () => {
  const s = setup({ stored: { [ACCESS_KEY]: 'pitch’2026' } });
  const data = await s.lib.visionJson('jobs');
  assert.deepEqual(data, { ok: true });
  assert.equal(s.calls[0].headers.get('x-pitchlens-access'), null);
  assert.equal(s.store.has(ACCESS_KEY), false);
  assert.deepEqual(s.events, [s.lib.ACCESS_EVENT]);
});

test('a code with characters a header cannot carry is refused when saved', () => {
  const s = setup({ stored: { [ACCESS_KEY]: 'old-code' } });
  assert.equal(s.lib.setVisionAccessCode('pitch—2026'), false);
  assert.equal(s.store.has(ACCESS_KEY), false);
  assert.equal(s.lib.accessCodeSendable('pitch—2026'), false);
  assert.equal(s.lib.accessCodeSendable('Fußball2026'), true);
});

test('a daily-cap refusal (429) is shown at once, not retried', async () => {
  const s = setup({ respond: () => Response.json({ detail: 'This server has reached its limit of 30 analyses in 24 hours.' }, { status: 429 }) });
  const started = Date.now();
  await assert.rejects(s.lib.uploadToVision(new Blob(['x']), { title: 't' }), /reached its limit/);
  assert.equal(s.calls.length, 1);
  assert.ok(Date.now() - started < 500);
});

test('upload defaults: player load unless the ball model is asked for, football models when available', () => {
  const s = setup();
  const options = s.lib.defaultVisionOptions;
  const plain = (x) => JSON.parse(JSON.stringify(x));
  assert.deepEqual(plain(options({ profiles: ['general', 'broadcast'] }, { playerLoadOnly: true })), { profile: 'broadcast', search: 'none' });
  assert.deepEqual(plain(options({ profiles: ['general'] }, { playerLoadOnly: true })), { profile: 'general', search: 'none' });
  assert.deepEqual(plain(options(null, { playerLoadOnly: false })), { profile: 'general', search: 'exhaustive' });
  assert.deepEqual(plain(options({ profiles: ['broadcast'] }, { playerLoadOnly: false, adaptive: true })), { profile: 'broadcast', search: 'adaptive' });
  assert.deepEqual(plain(options({ profiles: ['broadcast'] }, { playerLoadOnly: false, adaptive: true, profile: 'general' })), { profile: 'general', search: 'exhaustive' });
});
