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

function setup({ stored = {}, respond, context = {} } = {}) {
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
      calls.push({ url, headers: init.headers, init });
      return respond ? respond(url, init) : Response.json({ ok: true });
    },
    AbortController,
    ...context,
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

test('an out-of-storage refusal (507) is shown at once with the worker\'s words, not retried under a new request ID', async () => {
  const FULL = 'The analysis server is out of storage. Try again later.';
  for (const reply of [{ status: 507, body: { detail: FULL, code: 'out-of-storage' } }, { status: 507, body: { detail: FULL } }, { status: 503, body: { detail: FULL, code: 'out-of-storage' } }]) {
    // A resend of the same request ID would meet the released reservation's 409.
    let posts = 0;
    const s = setup({
      respond: () => ++posts === 1
        ? Response.json(reply.body, { status: reply.status })
        : Response.json({ detail: 'The previous upload stopped. Start the upload again.' }, { status: 409 }),
    });
    const started = Date.now();
    await assert.rejects(s.lib.uploadToVision(new Blob(['x']), { title: 't' }), (e) => e.message === FULL && e.status === reply.status);
    assert.equal(s.calls.length, 1);
    assert.ok(Date.now() - started < 500);
  }
});

test('a worker that cannot run full matches (503 full-match-unavailable) is answered after one request', async () => {
  const NOT_SET_UP = 'Full-match analysis is not set up on this server yet. A 20-second test still works.';
  const refuse = () => Response.json({ detail: NOT_SET_UP, code: 'full-match-unavailable' }, { status: 503 });
  for (const run of [
    (lib) => lib.uploadToVision(new Blob(['x']), { title: 't' }),
    (lib) => lib.analyseYouTube('https://youtu.be/dQw4w9WgXcQ', {}),
    (lib) => lib.rerunSavedVision('f'.repeat(32), 'full', 0, '1'.repeat(32)),
  ]) {
    const s = setup({ respond: refuse });
    const started = Date.now();
    await assert.rejects(run(s.lib), (e) => e.message === NOT_SET_UP && e.status === 503 && e.code === 'full-match-unavailable');
    assert.equal(s.calls.length, 1);
    assert.ok(Date.now() - started < 500);
  }
});

test('a 503 without a code is still retried with the same request ID', async () => {
  let posts = 0;
  const s = setup({
    respond: (url) => {
      if (/\/jobs\?/.test(url) && ++posts === 1) return Response.json({ detail: 'Busy' }, { status: 503 });
      if (/\/jobs\?/.test(url)) return Response.json({ id: 'f'.repeat(32), status: 'uploading', receivedBytes: 0 });
      if (url.endsWith('/start')) return Response.json({ id: 'f'.repeat(32), status: 'processing' });
      return Response.json({ received: 1 });
    },
  });
  const job = await s.lib.uploadToVision(new Blob(['x']), { title: 't' });
  assert.equal(job.status, 'processing');
  const ids = s.calls.filter((c) => /\/jobs\?/.test(c.url)).map((c) => new URL(c.url, 'https://site.example').searchParams.get('requestId'));
  assert.equal(ids.length, 2);
  assert.equal(ids[0], ids[1]);
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

test('detail defaults: Balanced whenever the ball model runs or a GPU is present', async () => {
  const s = setup({ respond: () => Response.json({ detail: 'Fixture: stop after the reservation.' }, { status: 400 }) });
  const fps = s.lib.defaultVisionFps;
  assert.equal(fps(null, true), '3');
  assert.equal(fps({ gpu: false }, true), '3');
  assert.equal(fps({ gpu: false }, false), '6');
  assert.equal(fps({ gpu: true }, true), '6');
  assert.equal(fps(null, false), '6');
  // The reservation carries it (a missing fps would fall back to Quick).
  await assert.rejects(s.lib.uploadToVision(new Blob(['x']), { title: 't', fps: fps(null, false) }), /stop after the reservation/);
  const reserve = s.calls.find((c) => /\/jobs\?/.test(c.url));
  assert.equal(new URL(reserve.url, 'https://site.example').searchParams.get('fps'), '6');
});

// ── Uploads that stall, pause or are cancelled ──────────────────────────
const MiB = 1024 * 1024;
const JOB = 'f'.repeat(32);

/** A worker that reserves JOB and acknowledges each piece; `put` may answer a piece itself. */
function uploadWorker(put) {
  const puts = [];
  const respond = (url, init) => {
    if (/\/jobs\?/.test(url)) return Response.json({ id: JOB, status: 'uploading', receivedBytes: 0 });
    if (url.endsWith('/start')) return Response.json({ id: JOB, status: 'processing' });
    if (url.endsWith('/cancel')) return Response.json({ requested: true });
    const offset = Number(new URL(url, 'https://site.example').searchParams.get('offset'));
    puts.push({ offset, size: init.body.size });
    return (put && put(offset, init, puts)) || Response.json({ received: offset + init.body.size });
  };
  return { puts, respond };
}

/** A reply that never comes; it only ends when its request is aborted. */
const never = (init) =>
  new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('The operation was aborted.', 'AbortError'))));

test('a piece whose reply never comes is resent smaller from the same offset, without cancelling', async () => {
  let stalled = false;
  const worker = uploadWorker((offset, init) => {
    if (offset === 4 * MiB && !stalled) {
      stalled = true;
      return never(init);
    }
  });
  const s = setup({ respond: worker.respond });
  const job = await s.lib.uploadToVision(new Blob([new Uint8Array(7 * MiB)]), { title: 't', pieceTimeoutMs: 50 });
  assert.equal(job.status, 'processing');
  assert.deepEqual(worker.puts.map((p) => [p.offset / MiB, p.size / MiB]), [[0, 4], [4, 3], [4, 2], [6, 1]]);
  assert.ok(!s.calls.some((c) => c.url.endsWith('/cancel')));
});

test('cancelling during a piece that hangs asks the worker to stop', async () => {
  const worker = uploadWorker((offset, init) => never(init));
  const s = setup({ respond: worker.respond });
  const controller = new AbortController();
  const upload = s.lib.uploadToVision(new Blob([new Uint8Array(MiB)]), { title: 't', signal: controller.signal, pieceTimeoutMs: 60_000 });
  setTimeout(() => controller.abort(), 30);
  await assert.rejects(upload, (e) => e.name === 'AbortError');
  assert.equal(s.calls.filter((c) => c.url.endsWith(`/jobs/${JOB}/cancel`)).length, 1);
});

/** Pages and windows that record their listeners, and a clock the test moves. */
function pageAt(visibility) {
  const listeners = { document: {}, window: {} };
  const on = (where) => ({
    addEventListener: (type, fn) => (listeners[where][type] = fn),
    removeEventListener: (type) => delete listeners[where][type],
  });
  const clock = { t: 1_000_000 };
  const document = { visibilityState: visibility, ...on('document') };
  const window = { dispatchEvent() {}, ...on('window') };
  const fire = (where, type) => listeners[where][type] && listeners[where][type]();
  return { document, window, fire, clock, Date: { now: () => clock.t } };
}

test('minutes spent hidden (a locked phone) do not count towards giving up', async () => {
  const page = pageAt('hidden');
  let failures = 0;
  const worker = uploadWorker((offset) => {
    if (failures === 3) return undefined;
    failures++;
    page.clock.t += 200_000; // 200 s pass while the phone is locked
    if (failures < 3) setTimeout(() => page.fire('window', 'online'), 5); // back online: no backoff wait
    else
      setTimeout(() => {
        page.document.visibilityState = 'visible';
        page.fire('document', 'visibilitychange'); // unlocked: retry at once
      }, 5);
    throw new TypeError('Failed to fetch');
  });
  const s = setup({ respond: worker.respond, context: { document: page.document, window: page.window, Date: page.Date } });
  const job = await s.lib.uploadToVision(new Blob([new Uint8Array(10)]), { title: 't', uploadAbandonSeconds: 7200 });
  assert.equal(job.status, 'processing');
  assert.equal(worker.puts.length, 4);
  // The same 400 s of failures with the page on screen give up, and say how long the worker keeps it.
  const shown = pageAt('visible');
  const down = uploadWorker(() => {
    shown.clock.t += 200_000;
    setTimeout(() => shown.fire('window', 'online'), 5);
    throw new TypeError('Failed to fetch');
  });
  const again = setup({ respond: down.respond, context: { document: shown.document, window: shown.window, Date: shown.Date } });
  await assert.rejects(
    again.lib.uploadToVision(new Blob([new Uint8Array(10)]), { title: 't', uploadAbandonSeconds: 7200 }),
    /^Error: Connection lost at 0%\. Press Analyse with the same file to continue\. What was sent is kept for about 2 hours unless someone else starts an analysis\.$/,
  );
  assert.equal(down.puts.length, 2);
  assert.ok(!again.calls.some((c) => c.url.endsWith('/cancel')));
});
