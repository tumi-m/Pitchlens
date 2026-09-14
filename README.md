# Pitchlens

Football video review grounded in footage.

## Run the working app

Requires Node 20.9+ (Node 22 LTS recommended) and npm.

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. No Firebase account, paid service or environment file is needed for local video review.

1. Choose an MP4, WebM or MOV video up to 500 MB. The browser must support its codec; H.264 MP4 is a good baseline.
2. Enter the team names and open the review room.
3. Pause/seek the video and tag goals, shots, saves, passes, fouls or corners. Add an optional note. A goal counts as one shot attempt; do not add a duplicate shot tag for that goal.
4. Click timeline timestamps to revisit the footage. Remove incorrect tags and save coaching notes.
5. Export the review as JSON. Delete a review to remove its locally stored footage and metadata.

**Data stays on this device:** IndexedDB stores video; localStorage stores review metadata. Clearing browser data removes reviews. Exported JSON contains timestamps, notes, observed counts and optional frame evidence, but not the video. Keep your original footage. JSON import is not implemented yet.

**Measurements:** duration and resolution come from the video decoder. Event counts come from explicitly labelled manual tags. Untagged events are unknown. The app does not claim automated goals, possession, xG, pass completion or calibrated pitch heatmaps.

## Optional AI frame inspection

Copy `frontend/.env.example` to `frontend/.env.local` and set `ROBOFLOW_API_KEY` on the server. Restart Next.js. The upload page will offer AI frame inspection.

Six evenly spaced JPEG frames are sent to Roboflow. The review shows the returned boxes, class labels, confidence and timestamps. A successful empty result means no objects were detected in that sample. Provider failures are reported as partial/failed; they never create substitute match statistics. Frame positions are camera-image coordinates and are not pitch coordinates.

Development permits local guest inference. **Production requires Firebase sign-in** and Firebase Admin Application Default Credentials to verify the user's token. Configure the public Firebase web fields and server ADC for the same project. Never put the Roboflow key or service-account private key in a `NEXT_PUBLIC_` variable. The per-process budget is 120 frames/hour with two in-flight requests. Add distributed per-user quotas before scaling across replicas.

## Repository map

- `frontend/app/upload`: decode video, optional frame inspection, persist review.
- `frontend/components/review/ReviewRoom.tsx`: player, timeline, manual tags, notes, export, deletion.
- `frontend/lib/review`: typed evidence contract, summary calculations, IndexedDB video storage.
- `frontend/app/api/infer`: bounded JPEG proxy, explicit errors, production token verification.
- `frontend/lib/firebase`: optional authentication and legacy cloud access. Local reviews are not silently synced to Firestore.
- `frontend/app/dashboard` and `report`: current review room; older statistics are clearly labelled unverified, including PDF exports.
- `firebase`: optional research deployment rules and storage-to-engine dispatch.
- `backend`: experimental tracking/analytics code; disabled by default and not part of the local review flow.
- `docs/IMPROVEMENTS.md`: findings, implemented fixes and phased product/engineering roadmap.

## Tests

```sh
cd frontend
npm run type-check
npm run lint
npm run build
npx playwright install chromium
npm test
```

On a machine with Google Chrome installed, use `PLAYWRIGHT_CHANNEL=chrome npm test` instead of downloading Chromium. Browser tests use a synthetic four-second video fixture, not real match footage. AI success/failure tests mock the provider; they test application behavior, not detector accuracy.

```sh
cd firebase/functions
npm ci
npm run build
```

## Experimental Python engine

The research engine is **not production match analytics**. Its spatial projection, team assignment, event heuristics and xG coefficients are not calibrated or validated. Existing experimental output is labelled `experimental-uncalibrated`. Default API behavior rejects analysis requests until explicitly enabled.

Requires Python 3.11 or 3.12, Poetry 1.8+ and FFmpeg for video processing:

```sh
cd backend
poetry install
poetry run pytest tests -v
poetry run uvicorn app.main:app --port 8080
```

Health: `GET /api/v1/health` works without cloud credentials. To run research processing, copy `backend/.env.example`, export its fields (or use uvicorn `--env-file .env`), configure ADC, and explicitly set `ENABLE_EXPERIMENTAL_ANALYTICS=true`. `POST /api/v1/process-match` requires the server secret, a safe match ID, and a signed URL under `https://storage.googleapis.com/<FIREBASE_STORAGE_BUCKET>/`. The request stays open until processing finishes. This is a bounded research deployment pattern, not a durable distributed queue.

Firebase Functions declare `API_SECRET_KEY` and `PYTHON_API_URL` as secrets. Configure them with Firebase CLI. The storage trigger claims a match transactionally, verifies ownership, and invokes only the Python engine. There is no Node.js simulated fallback. Delete failures are surfaced. Retrying an expired URL is rejected; upload again. Deploy rules and functions only to a configured project, and verify them in the emulator first.

## Scope and limitations

No live cloud deployment was performed by this change. Local review is the supported working path. Production inference requires credentials. Real match footage, labelled events, pitch calibration and detector evaluation are required before promising automated football statistics. No speed/accuracy improvement multiplier has been measured.
