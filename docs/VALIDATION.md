# Verification record — 14 September 2026

## Passed

- Frontend TypeScript checking, ESLint (zero errors; two existing/native-image optimization warnings), production build.
- Firebase Functions TypeScript build after migrating Firebase Admin to modular imports.
- `npm audit` reports zero vulnerabilities for both frontend and Functions dependency trees at verification time. This is dependency advisory checking, not a guarantee of application security.
- Backend: 14 tests passed on Python 3.12, covering analytics helpers, credential-free health, bucket-scoped URL validation, match IDs and pass-edge serialization.
- Poetry metadata and generated lockfile validate. Python support is explicitly 3.11–3.12.
- API-only Playwright check: absent inference configuration produces HTTP 503 with an error; removed remote-video proxy produces HTTP 410.
- Connected in-app browser: synthetic four-second 640×360 MP4 opens and is stored; a manual goal tag increments observed goals/shots; timeline and coaching notes survive page reload; video remains playable; 390px mobile layout fits; invalid MP4 displays a decoder error rather than creating statistics.
- Connected browser export: JSON preview/copy works. Copied payload was parsed and checked: duration 4 seconds, one manually sourced goal, one goal/shot in observed counts, and no fabricated stats field.

## Environment limitations / not verified

- The standalone Playwright suite contains six end-to-end scenarios, including mocked AI success/failure, but this macOS sandbox aborts direct Chrome launches. One API test passed; the five browser tests could not launch. They are not reported as passing. The production server was used for connected-browser checks after the development server hit the host file-watcher limit.
- The in-app browser's download-event wait timed out. Added an explicit export preview, native download link and verified clipboard-copy fallback. Native file download and full delete flow still need a standard-browser test run.
- Live Roboflow inference, Firebase sign-in, deployed security rules, cloud upload/processing and deployment were not exercised without service configuration. AI browser tests mock provider responses and do not measure detector accuracy.
- No real-match video or labelled benchmark was supplied. Automatic xG, possession, team identity and event accuracy remain unvalidated; the research engine is disabled by default.
- Docker image build and full clean Poetry environment installation were not run. Dependency resolution and lock validation passed; backend tests used the local Python 3.12 test environment.

## Reproduce

```sh
cd frontend
npm ci
npm run type-check
npm run lint
npm run build
npx playwright install chromium
npm test
```

The included test fixture is a synthetic colour-pattern video. Use representative match footage and configured services for production acceptance testing. A synthetic QA review may remain in the connected browser's local workspace.
