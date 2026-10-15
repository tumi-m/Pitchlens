/** Response headers for every page and API route. Script and connect sources are
 * not restricted yet (Firebase sign-in needs its own allowlist, tested against a
 * real project first); these lines only remove what the site never uses. */
const securityHeaders = [
  // Nobody may frame the site (clickjacking), plug-ins never load, a base tag or a
  // form can never send the page elsewhere.
  { key: 'Content-Security-Policy', value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'" },
  { key: 'X-Frame-Options', value: 'DENY' },
  // Worker responses are relayed with the worker's type: never sniff them as HTML.
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  // Other sites see only the origin, never /vision/<match id> or ?reference=.
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  // Upload keeps the screen awake and video goes full screen: both stay allowed.
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()' },
  // Ignored over plain http (local runs); HTTPS only once a browser has seen it.
  // No includeSubDomains: it would force an operator's or club's other subdomains
  // onto HTTPS for two years. Vercel sends this same value.
  { key: 'Strict-Transport-Security', value: 'max-age=63072000' },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  // next/image is not used: without remote patterns /_next/image cannot be used to
  // fetch and resize images from other people's storage on this site's quota. If it
  // is ever used, add a pattern limited by pathname to the project's own bucket
  // ('/v0/b/<project>.appspot.com/**').
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
  webpack: (config) => {
    config.externals = [...(config.externals || []), { canvas: 'canvas' }];
    return config;
  },
};

module.exports = nextConfig;
