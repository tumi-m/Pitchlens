# Verification record — 14–15 September 2026

## Passed

- Frontend TypeScript checking, ESLint (zero errors; two existing/native-image optimization warnings), production build.
- Firebase Functions TypeScript build after migrating Firebase Admin to modular imports.
- `npm audit` reports zero vulnerabilities for both frontend and Functions dependency trees at verification time. This is dependency advisory checking, not a guarantee of application security.
- Backend: 14 tests passed on Python 3.12, covering analytics helpers, credential-free health, bucket-scoped URL validation, match IDs and pass-edge serialization.
- Poetry metadata and generated lockfile validate. Python support is explicitly 3.11–3.12.
- API-only Playwright check: absent inference configuration produces HTTP 503 with an error; removed remote-video proxy produces HTTP 410.
- Connected in-app browser: synthetic four-second 640×360 MP4 opens and is stored; a manual goal tag increments observed goals/shots; timeline and coaching notes survive page reload; video remains playable; 390px mobile layout fits; invalid MP4 displays a decoder error rather than creating statistics.
- Connected browser export: JSON preview/copy works. Copied payload was parsed and checked: duration 4 seconds, one manually sourced goal, one goal/shot in observed counts, and no fabricated stats field.

- Follow-up: 13 unit tests passed for portable review validation, event corrections, schema/size limits, duplicate IDs, unsafe frame URLs and video matching/fingerprints. Type checking, lint and production build passed again.
- Connected-browser follow-up on port 3001: imported the synthetic JSON fixture, reconnected its original MP4, corrected its goal to an away-team shot at 2.5 seconds, reloaded and verified persistence. Copied export retained the exact timestamp, note and corrected team counts.

## Environment limitations / not verified

- The standalone Playwright suite contains eight end-to-end scenarios, including mocked AI success/failure, but this macOS sandbox aborts direct Chrome launches. One API test passed; the original five browser tests could not launch. Two additional import/edit scenarios were added but have not been run through standalone Playwright. They are not reported as passing. The production server was used for connected-browser checks after the development server hit the host file-watcher limit.
- The in-app browser's download-event wait timed out. Added an explicit export preview, native download link and verified clipboard-copy fallback. Native file download, remove/undo UI and full delete flow still need a standard-browser test run.
- Live Roboflow inference, Firebase sign-in, deployed security rules, cloud upload/processing and deployment were not exercised without service configuration. AI browser tests mock provider responses and do not measure detector accuracy.
- The initial manual-review verification had no real-match video. On September 15 the user supplied TRYZUB vs LUNA; the new local CV worker was tested on it. No independently labelled benchmark exists, so event accuracy and xG remain unvalidated; the older cloud research engine stays disabled.
- Docker image build and full clean Poetry environment installation were not run. Dependency resolution and lock validation passed; backend tests used the local Python 3.12 test environment.

## Reproduce

```sh
cd frontend
npm ci
npm run test:unit
npm run type-check
npm run lint
npm run build
npx playwright install chromium
npm test
```

The included test fixture is a synthetic colour-pattern video. Use representative match footage and configured services for production acceptance testing. A synthetic QA review may remain in the connected browser's local workspace.

## Local computer vision — September 15

- Installed and checksum-verified two real neural models: official YOLO11s for players and the pinned football-specific YOLOv9 ONNX ball model. See `VISION.md` for exact sources and hashes.
- Backend suite: 33 tests passed, followed by a targeted vision suite with all 21 tests passing after adding shutdown and missing-ball-model checks. Tests cover ambiguity/missing evidence, temporal candidates, corrupted uploads, authentication, ranges, cancellation, cleanup, camera translation, team-aware association, cut/reset behaviour and letterbox/NMS coordinates. These are software correctness tests, not detector accuracy tests.
- Frontend production build, TypeScript and lint passed (two existing image-optimization warnings); all 13 portable-review unit tests passed. Installed Python environment reports no broken requirements.
- Browser: selected the user's actual 86 MiB MP4, uploaded through Next.js, navigated automatically to a live worker job, and verified processing progress. Processing continues independently of page navigation and a Next.js server restart. Verified baseline playback at 1:53 with decoder readyState 4, working Range seeking and visible detection overlays. Verified that low-coverage possession shares are withheld.
- Source: H.264, 640×360, 29.97 fps, 67,851 frames, 2,263.964 seconds. The generic-ball baseline processed 11,309 sampled frames over the full duration. After conservative filtering it returned ball candidates in 21.2% of sampled frames and stable proximity for 5.1% of the video. These figures exposed inadequacy; they are not claimed as accurate football analytics.
- A 120–180 second diagnostic compared identical cached player detections: motion association reduced track fragments from 166 to 132. Football ball candidates at threshold 0.10 appeared in 122/300 frames vs 70/300 baseline frames. This small tuning sample is not an independent accuracy benchmark; the full new run uses a stricter 0.15 threshold and newly inferred players/kits.
- CPU is used explicitly. An optional CoreML experiment crashed; no hardware-acceleration performance claim is made.
- The standalone browser suite now has two additional CV scenarios, using mocked job responses. It was not run because direct browser launching remains blocked in this environment. Connected-browser checks above used the actual worker and real footage.

### Final full-match result

The browser-submitted football-model job `ca5cbe3ad5654522a81d5c4a0053bf61` completed the full 2,263.964 seconds at 2.997 analysed frames/sec (6,786 frames). Neural inference took about 29 minutes on this CPU. After recomputing track association from the saved detections with a 0.5 minimum confidence for new identities, the result contains:

- 6,782 frames with player tracks; 1,368 frames with a ball candidate (20.2%).
- 82.11 seconds of stable team proximity; 2,181.85 seconds unknown (3.6% coverage).
- No transfer met the continuous-evidence requirements. This is not a claim of zero passes in the match.
- 5,171 short-term track fragments. The first association pass created 10,442; weak detections may now sustain an existing track but cannot create a new one. Fragment count is not tracking accuracy, and the remaining fragmentation is substantial.

The full run is a successful engineering integration test and an unsuccessful demonstration of reliable football event analytics on this recording. It must not be described as commercial-grade match analysis. No score, xG, speed or pitch-coordinate statistics were generated. The app explicitly withholds possession shares. Camera/kit corrections and model changes do not resolve the tiny-ball and evaluation-data limitations.

A diagnostic that allowed missing-ball intervals produced just two additional pass candidates and was not adopted. We did not loosen the production evidence rules merely to populate the report.

Final software checks after the tracking guard: all 36 backend tests passed; 13 frontend unit tests passed; production build, TypeScript, lint and vision-code Ruff checks passed. The annotated 30-second preview was rendered from the actual saved results at source frame rate and visually inspected. Model weights and source videos are excluded from the source archive and Git.
