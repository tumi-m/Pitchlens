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

function setup() {
  const calls = [];
  const exports = {};
  const context = {
    exports, require, Buffer, URL, URLSearchParams, Headers, Response,
    process: { env: { VISION_SERVICE_URL: 'https://worker.example', VISION_SERVICE_TOKEN: 'fixture-token', VISION_ACCESS_CODE: 'fixture-access' } },
    fetch: async (url, init) => {
      calls.push({ url, init });
      assert.equal(init.headers.Authorization, 'Bearer fixture-token');
      assert.equal(init.headers['x-pitchlens-require-owner'], '1');
      if (init.headers['x-pitchlens-owner'] !== OWNER) return Response.json({ detail: 'Job not found' }, { status: 404 });
      return Response.json({ ok: true });
    },
  };
  vm.runInNewContext(code, context);
  function request({ method = 'GET', owner, cookie, query = '', access, origin = 'https://pitchlens.example' } = {}) {
    const headers = new Headers({ host: 'pitchlens.example', origin });
    if (owner) headers.set('x-pitchlens-owner', owner);
    if (access) headers.set('x-pitchlens-access', access);
    return { method, headers, nextUrl: new URL(`https://pitchlens.example/api/vision/jobs/${ID}${query}`),
      cookies: { get: () => cookie ? { value: cookie } : undefined }, body: null };
  }
  const route = (suffix = '') => ({ params: Promise.resolve({ path: ['jobs', ID, ...suffix.split('/').filter(Boolean)] }) });
  return { api: exports, calls, request, route };
}

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

test('delete and retry require same-origin access authorization', async () => {
  const s = setup();
  assert.equal((await s.api.DELETE(s.request({ method: 'DELETE', owner: OWNER }), s.route())).status, 401);
  assert.equal((await s.api.DELETE(s.request({ method: 'DELETE', owner: OWNER, access: 'fixture-access', origin: 'https://attacker.example' }), s.route())).status, 403);
  assert.equal(s.calls.length, 0);
  assert.equal((await s.api.DELETE(s.request({ method: 'DELETE', owner: OWNER, access: 'fixture-access' }), s.route())).status, 200);
  assert.equal((await s.api.POST(s.request({ method: 'POST', owner: OWNER, access: 'fixture-access' }), s.route('retry'))).status, 200);
});
