const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../app/api/payments/paystack/webhook/route.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const SIGNATURE = 'a'.repeat(128);

function setup({ base = 'https://worker.example', status = 200, fail = false } = {}) {
  const calls = [];
  const exports = {};
  vm.runInNewContext(code, {
    exports, Buffer, URL, Response, AbortSignal,
    process: { env: { VISION_SERVICE_URL: base, VISION_SERVICE_TOKEN: 'fixture-service-token' } },
    fetch: async (url, init) => {
      calls.push({ url: url.toString(), init });
      if (fail) throw new Error('unavailable');
      return Response.json({ received: true }, { status });
    },
  });
  const request = (body = '{ "event" : "charge.success" }\n', headers = { 'x-paystack-signature': SIGNATURE }) =>
    new Request('https://pitchlens.example/api/payments/paystack/webhook', { method: 'POST', body, headers });
  return { post: exports.POST, request, calls };
}

test('webhook relay preserves exact signed bytes and signature without browser credentials', async () => {
  const s = setup();
  const raw = '{ "event" : "charge.success", "name": "é" }\n';
  const response = await s.post(s.request(raw));
  assert.equal(response.status, 200);
  assert.equal(s.calls[0].url, 'https://worker.example/billing/webhook');
  const { init } = s.calls[0];
  assert.equal(init.body.toString('utf8'), raw);
  assert.equal(init.headers.Authorization, 'Bearer fixture-service-token');
  assert.equal(init.headers['x-paystack-signature'], SIGNATURE);
  assert.equal(init.redirect, 'error');
  assert.equal(init.headers['x-pitchlens-owner'], undefined);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('webhook rejects missing or malformed signatures before forwarding', async () => {
  const s = setup();
  for (const signature of ['', 'forged', 'a'.repeat(129)]) {
    assert.equal((await s.post(s.request('{}', { 'x-paystack-signature': signature }))).status, 401);
  }
  assert.equal(s.calls.length, 0);
});

test('webhook enforces byte limit with and without Content-Length', async () => {
  const s = setup();
  assert.equal((await s.post(s.request('x'.repeat(65537)))).status, 413);
  assert.equal((await s.post(s.request('{}', { 'content-length': '1000000', 'x-paystack-signature': SIGNATURE }))).status, 413);
  assert.equal(s.calls.length, 0);
});

test('failed worker signature or unavailable storage is never acknowledged as a payment', async () => {
  for (const status of [401, 409, 500, 503]) {
    const s = setup({ status });
    assert.equal((await s.post(s.request())).status, status);
  }
  const s = setup({ fail: true });
  assert.equal((await s.post(s.request())).status, 503);
});

test('webhook relay refuses missing, insecure or loopback destinations', async () => {
  for (const base of ['', 'not-a-url', 'http://worker.example', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://user:password@worker.example']) {
    const s = setup({ base });
    assert.equal((await s.post(s.request())).status, 503);
    assert.equal(s.calls.length, 0);
  }
});
