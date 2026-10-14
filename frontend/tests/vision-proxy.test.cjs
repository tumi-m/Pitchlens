const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../app/api/vision/[...path]/route.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const OWNER = 'a'.repeat(32);
const OTHER = 'b'.repeat(32);
const ID = 'c'.repeat(32);

/** Loads the real route with a fake worker. By default the worker is hosted and
 *  only knows OWNER's job; pass `worker` to answer requests yourself. */
function setup(env = {}, worker) {
  const calls = [];
  const exports = {};
  const context = {
    exports, require, Buffer, URL, URLSearchParams, Headers, Response,
    process: { env: { VISION_SERVICE_URL: 'https://worker.example', VISION_SERVICE_TOKEN: 'fixture-token', VISION_ACCESS_CODE: 'fixture-access', ...env } },
    fetch: async (url, init) => {
      calls.push({ url, init });
      assert.equal(init.headers.Authorization, 'Bearer fixture-token');
      if (worker) return worker(url, init);
      assert.equal(init.headers['x-pitchlens-require-owner'], '1');
      if (init.headers['x-pitchlens-owner'] !== OWNER) return Response.json({ detail: 'Job not found' }, { status: 404 });
      // A worker that allows site training (VISION_SITE_TRAINING=1).
      if (new URL(url).pathname === '/ball-model') return Response.json({ active: null, runs: [], siteTraining: true });
      return Response.json({ ok: true });
    },
  };
  vm.runInNewContext(code, context);
  function request({ method = 'GET', owner, cookie, query = '', access, origin = 'https://pitchlens.example', host = 'pitchlens.example', headers: extra = {}, body } = {}) {
    const headers = new Headers({ host, ...extra });
    if (origin) headers.set('origin', origin);
    if (owner) headers.set('x-pitchlens-owner', owner);
    if (access) headers.set('x-pitchlens-access', access);
    return { method, headers, nextUrl: new URL(`https://${host}/api/vision/jobs/${ID}${query}`),
      cookies: { get: () => cookie ? { value: cookie } : undefined }, body: null,
      arrayBuffer: async () => body ?? new ArrayBuffer(0) };
  }
  const route = (suffix = '') => ({ params: Promise.resolve({ path: ['jobs', ID, ...suffix.split('/').filter(Boolean)] }) });
  const at = (path) => ({ params: Promise.resolve({ path: path.split('/').filter(Boolean) }) });
  return { api: exports, calls, request, route, at };
}
const ok = () => Response.json({ ok: true });
const sentOwner = (call) => call.init.headers['x-pitchlens-owner'];
const ownerQuery = (call) => new URL(call.url).searchParams.get('owner');

test('private proxy rejects anonymous reads without contacting the worker', async () => {
  const s = setup();
  const response = await s.api.GET(s.request(), s.route('video'));
  assert.equal(response.status, 401);
  assert.equal(s.calls.length, 0);
});

test('successful owner request establishes a private playback cookie', async () => {
  const s = setup();
  const response = await s.api.GET(s.request({ owner: OWNER }), s.route());
  assert.equal(response.status, 200);
  const cookie = response.headers.get('set-cookie');
  for (const flag of ['HttpOnly', 'SameSite=Strict', 'Secure', 'Path=/api/vision']) assert.ok(cookie.includes(flag));
  const playback = await s.api.GET(s.request({ cookie: OWNER }), s.route('video'));
  assert.equal(playback.status, 200);
  assert.equal(s.calls[1].init.headers['x-pitchlens-owner'], OWNER);
});

test('owner query cannot override the caller capability and GPU grants are not forwarded', async () => {
  const s = setup();
  const request = s.request({ owner: OTHER, query: `?owner=${OWNER}` });
  request.headers.set('x-pitchlens-video-grant', 'forged');
  const response = await s.api.GET(request, s.route('result'));
  assert.equal(response.status, 404);
  assert.equal(new URL(s.calls[0].url).searchParams.get('owner'), null);
  assert.equal(s.calls[0].init.headers['x-pitchlens-video-grant'], undefined);
  assert.equal(response.headers.get('set-cookie'), null);
});

test('delete, retry and saved-video reruns require same-origin access authorization', async () => {
  const s = setup();
  assert.equal((await s.api.DELETE(s.request({ method: 'DELETE', owner: OWNER }), s.route())).status, 401);
  assert.equal((await s.api.DELETE(s.request({ method: 'DELETE', owner: OWNER, access: 'fixture-access', origin: 'https://attacker.example' }), s.route())).status, 403);
  assert.equal(s.calls.length, 0);
  assert.equal((await s.api.DELETE(s.request({ method: 'DELETE', owner: OWNER, access: 'fixture-access' }), s.route())).status, 200);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access' }), s.route('retry'))).status, 200);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER }), s.route('rerun'))).status, 401);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access', origin: 'https://attacker.example' }), s.route('rerun'))).status, 403);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access', query: '?mode=full&start=0&requestId=abc' }), s.route('rerun'))).status, 200);
  assert.equal(new URL(s.calls.at(-1).url).searchParams.get('mode'), 'full');
});

test('billing requires owner and write authorization; browser proxy never accepts webhooks', async () => {
  const s = setup();
  const billing = (name) => ({ params: Promise.resolve({ path: ['billing', name] }) });
  assert.equal((await s.api.GET(s.request(), billing('summary'))).status, 401);
  assert.equal((await s.api.GET(s.request({ owner: OWNER }), billing('summary'))).status, 200);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER }), billing('checkout'))).status, 401);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access' }), billing('verify'))).status, 200);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access' }), billing('webhook'))).status, 404);
});

test('an access code pasted into the dashboard with a trailing newline still matches', async () => {
  const s = setup({ VISION_ACCESS_CODE: 'fixture-access\n ' });
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access' }), s.route('retry'))).status, 200);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-acces' }), s.route('retry'))).status, 401);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'Fixture-access' }), s.route('retry'))).status, 401);
});

test('without an access code set, the hosted site is open but still private per browser', async () => {
  for (const code of ['', '   ']) {
    const s = setup({ VISION_ACCESS_CODE: code });
    assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER }), s.route('retry'))).status, 200);
    // Another browser's match stays invisible, and anonymous requests are refused.
    assert.equal((await s.api.GET(s.request({ owner: OTHER }), s.route('result'))).status, 404);
    assert.equal((await s.api.GET(s.request(), s.route('result'))).status, 401);
    // Cross-site writes are still refused.
    assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, origin: 'https://attacker.example' }), s.route('retry'))).status, 403);
  }
});

test('health tells the upload page whether a code is needed', async () => {
  const health = async (code) => {
    const s = setup({ VISION_ACCESS_CODE: code });
    const response = await s.api.GET(s.request({ owner: OWNER }), { params: Promise.resolve({ path: ['health'] }) });
    return response.json();
  };
  assert.equal((await health('')).accessRequired, false);
  assert.equal((await health('fixture-access')).accessRequired, true);
});

test('site training is refused while the site has no access code', async () => {
  const s = setup({ VISION_ACCESS_CODE: '' });
  const request = s.request({ method: 'POST', owner: OWNER });
  const response = await s.api.POST(request, { params: Promise.resolve({ path: ['ball-model', 'train'] }) });
  assert.equal(response.status, 403);
  assert.equal(s.calls.length, 0);
  const closed = setup();
  const allowed = await closed.api.POST(closed.request({ method: 'POST', owner: OWNER, access: 'fixture-access' }), { params: Promise.resolve({ path: ['ball-model', 'train'] }) });
  assert.equal(allowed.status, 200);
});

test('health offers site training only with an access code and the worker allowing it', async () => {
  const health = async (code, workerSays) => {
    const s = setup({ VISION_ACCESS_CODE: code }, () => Response.json({ available: true, siteTraining: workerSays }));
    const response = await s.api.GET(s.request({ owner: OWNER }), { params: Promise.resolve({ path: ['health'] }) });
    return (await response.json()).siteTraining;
  };
  // An open site refuses training (above), whatever the worker says.
  assert.equal(await health('', true), false);
  assert.equal(await health('fixture-access', true), true);
  assert.equal(await health('fixture-access', false), false);
  assert.equal(await health('fixture-access', 'true'), false);
});

// ------------------------------------------------ owner key: the cookie survives script storage

test('a surviving owner cookie wins over a freshly minted browser key and is never replaced', async () => {
  // Safari's 7-day cap or "clear website data" wiped localStorage (new key OTHER);
  // the HttpOnly cookie still holds OWNER, the key the matches were uploaded under.
  const s = setup();
  const response = await s.api.GET(s.request({ owner: OTHER, cookie: OWNER }), s.route('result'));
  assert.equal(response.status, 200);
  assert.equal(sentOwner(s.calls[0]), OWNER);
  assert.equal(response.headers.get('x-pitchlens-owner'), OWNER);
  const cookie = response.headers.get('set-cookie');
  assert.ok(!cookie || cookie.includes(`pitchlens-vision-owner=${OWNER};`), cookie);
  const listing = setup({}, ok);
  await listing.api.GET(listing.request({ owner: OTHER, cookie: OWNER }), listing.at('jobs'));
  assert.equal(ownerQuery(listing.calls[0]), OWNER);
});

test('the first request sets the cookie to the browser key, and an invalid cookie is ignored', async () => {
  for (const cookie of [undefined, 'not-an-owner-key']) {
    const s = setup({}, ok);
    const response = await s.api.GET(s.request({ owner: OTHER, cookie }), s.route());
    assert.equal(response.status, 200);
    assert.equal(sentOwner(s.calls[0]), OTHER);
    assert.equal(response.headers.get('x-pitchlens-owner'), OTHER);
    assert.ok(response.headers.get('set-cookie').includes(`pitchlens-vision-owner=${OTHER};`));
  }
});

// ------------------------------------------------ storage blocked: no owner at all

test('without any owner key, listings are empty, new analyses ask for site data, and matches stay private', async () => {
  const s = setup({ VISION_ACCESS_CODE: '' });
  for (const name of ['jobs', 'venues']) {
    const list = await s.api.GET(s.request(), s.at(name));
    assert.equal(list.status, 200);
    assert.deepEqual(await list.json(), []);
  }
  for (const name of ['jobs', 'jobs/from-url', 'venues']) {
    const write = await s.api.POST(s.request({ method: 'POST' }), s.at(name));
    assert.equal(write.status, 400);
    assert.equal((await write.json()).code, 'storage');
  }
  const match = await s.api.GET(s.request(), s.route());
  assert.equal(match.status, 401);
  assert.equal((await match.json()).code, 'ownership');
  assert.equal(s.calls.length, 0);
});

// ------------------------------------------------ local worker: other devices on the network

test('local mode never forwards an ownerless listing, even with a spoofed Host header', async () => {
  const s = setup({ VISION_SERVICE_URL: 'http://127.0.0.1:8100', VISION_ACCESS_CODE: '' }, ok);
  const spoofed = { host: 'localhost:3000', origin: null };
  const list = await s.api.GET(s.request(spoofed), s.at('jobs'));
  assert.deepEqual(await list.json(), []);
  assert.deepEqual(await (await s.api.GET(s.request(spoofed), s.at('venues'))).json(), []);
  assert.equal((await s.api.POST(s.request({ ...spoofed, method: 'POST', headers: { 'sec-fetch-site': 'same-origin' } }), s.at('jobs'))).status, 400);
  assert.equal(s.calls.length, 0);
  // With this browser's key the worker filters the listing to its matches.
  await s.api.GET(s.request({ ...spoofed, owner: OWNER }), s.at('jobs'));
  assert.equal(ownerQuery(s.calls[0]), OWNER);
  // Legacy ownerless jobs opened by link keep working: ownership is not forced locally.
  assert.equal(s.calls[0].init.headers['x-pitchlens-require-owner'], undefined);
});

test('local writes need a same-origin browser request', async () => {
  const s = setup({ VISION_SERVICE_URL: 'http://127.0.0.1:8100', VISION_ACCESS_CODE: '' }, ok);
  const write = (extra) => s.api.POST(s.request({ method: 'POST', owner: OWNER, host: 'localhost:3000', origin: null, ...extra }), s.route('retry'));
  assert.equal((await write()).status, 403);
  assert.equal((await write({ headers: { 'sec-fetch-site': 'same-site' } })).status, 403);
  assert.equal(s.calls.length, 0);
  assert.equal((await write({ headers: { 'sec-fetch-site': 'same-origin' } })).status, 200);
  assert.equal((await write({ origin: 'http://localhost:3000' })).status, 200);
  assert.equal(s.calls.length, 2);
});

// ------------------------------------------------ guards that only these tests cover

test('a hosted worker must use https: the bearer token never goes over plain HTTP', async () => {
  const s = setup({ VISION_SERVICE_URL: 'http://worker.example' });
  const response = await s.api.GET(s.request({ owner: OWNER }), s.route());
  assert.equal(response.status, 503);
  assert.equal(s.calls.length, 0);
});

test('a local worker is only served to this computer', async () => {
  const s = setup({ VISION_SERVICE_URL: 'http://127.0.0.1:8100' }, ok);
  const response = await s.api.GET(s.request({ owner: OWNER, host: '192.0.2.2:3000' }), s.route());
  assert.equal(response.status, 403);
  assert.equal(s.calls.length, 0);
});

test('upload chunks over 4.5 MB are refused, with or without a Content-Length', async () => {
  const s = setup({}, ok);
  const big = 4.5 * 1024 * 1024 + 1;
  const put = (extra) => s.api.PUT(s.request({ method: 'PUT', owner: OWNER, access: 'fixture-access', query: '?offset=0', ...extra }), s.route('video'));
  const declared = s.request({ method: 'PUT', owner: OWNER, access: 'fixture-access', headers: { 'content-length': String(big) } });
  declared.arrayBuffer = async () => assert.fail('a declared oversize body must not be read');
  assert.equal((await s.api.PUT(declared, s.route('video'))).status, 413);
  assert.equal((await put({ body: new ArrayBuffer(big) })).status, 413);
  assert.equal(s.calls.length, 0);
  assert.equal((await put({ body: new ArrayBuffer(1024) })).status, 200);
  assert.equal(s.calls[0].init.body.byteLength, 1024);
});

test('hosted writes need an Origin header, and cross-site requests are refused', async () => {
  const s = setup();
  const retry = (extra) => s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access', ...extra }), s.route('retry'));
  assert.equal((await retry({ origin: null })).status, 403);
  assert.equal((await retry({ origin: null, headers: { 'sec-fetch-site': 'same-origin' } })).status, 403);
  assert.equal((await retry({ headers: { 'sec-fetch-site': 'cross-site' } })).status, 403);
  assert.equal(s.calls.length, 0);
  assert.equal((await retry()).status, 200);
});

test('only known per-match paths are forwarded', async () => {
  const s = setup();
  for (const suffix of ['models', 'video/../../admin', 'frames/1234567']) {
    assert.equal((await s.api.GET(s.request({ owner: OWNER }), s.route(suffix))).status, 404);
  }
  assert.equal(s.calls.length, 0);
});

test('an open site tells the page up front that training is unavailable, in words for a team owner', async () => {
  const model = { params: Promise.resolve({ path: ['ball-model'] }) };
  const open = setup({ VISION_ACCESS_CODE: '' });
  const status = await open.api.GET(open.request({ owner: OWNER }), model);
  assert.equal(status.status, 200);
  const body = await status.json();
  assert.equal(body.siteTraining, false);
  assert.deepEqual(body.runs, []);
  const refused = await open.api.POST(open.request({ method: 'POST', owner: OWNER }), { params: Promise.resolve({ path: ['ball-model', 'train'] }) });
  const detail = (await refused.json()).detail;
  assert.equal(refused.status, 403);
  assert.ok(!detail.includes('VISION_ACCESS_CODE') && !detail.includes('terminal'), detail);
  // With a code, the worker's own answer is passed through.
  const closed = setup();
  assert.equal((await (await closed.api.GET(closed.request({ owner: OWNER }), model)).json()).siteTraining, true);
});
