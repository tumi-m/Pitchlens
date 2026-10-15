const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const config = require(path.join(__dirname, '../next.config.js'));

async function headersFor(route) {
  const rules = typeof config.headers === 'function' ? await config.headers() : [];
  const out = {};
  for (const rule of rules) {
    // Every rule used here applies site-wide ("/:path*").
    if (rule.source === '/:path*' || rule.source === route) for (const h of rule.headers) out[h.key.toLowerCase()] = h.value;
  }
  return out;
}

test('every page and API route refuses framing, sniffing and leaking the match path', async () => {
  for (const route of ['/', '/vision/abc', '/api/vision/jobs']) {
    const h = await headersFor(route);
    assert.match(h['content-security-policy'] || '', /frame-ancestors 'none'/);
    assert.match(h['content-security-policy'] || '', /object-src 'none'/);
    assert.equal(h['x-frame-options'], 'DENY');
    assert.equal(h['x-content-type-options'], 'nosniff');
    assert.equal(h['referrer-policy'], 'strict-origin-when-cross-origin');
    assert.match(h['permissions-policy'] || '', /camera=\(\)/);
    assert.match(h['strict-transport-security'] || '', /max-age=\d{8,}/);
    // includeSubDomains would pin an operator's or club's other subdomains to HTTPS.
    assert.doesNotMatch(h['strict-transport-security'] || '', /includeSubDomains/i);
  }
});

test('the site does not advertise its framework or proxy other hosts through /_next/image', () => {
  assert.equal(config.poweredByHeader, false);
  assert.equal((config.images?.remotePatterns || []).length, 0);
  assert.equal((config.images?.domains || []).length, 0);
});
