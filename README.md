# Pitchlens

**Automatic local computer vision is now available.** Start with [the vision setup guide](docs/VISION.md). Upload a video to run player/ball detection, kit grouping, tracking and evidence-linked possession/pass candidates. Manual review remains a secondary workflow.

Football video review grounded in footage.

## Automatic analysis

Follow [docs/VISION.md](docs/VISION.md) to install the local model and start the vision worker. The default upload page creates a background analysis job and opens a detection report. Videos/results are stored on this computer, outside browser storage.

## Manual review without a vision worker

Requires Node 20.9+ (Node 22 LTS recommended) and npm.

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. No Firebase account, paid service or environment file is needed for local video review.

1. Select **Open manual review instead**, then choose an MP4, WebM or MOV video up to 500 MB. The browser must support its codec; H.264 MP4 is a good baseline.
2. Enter the team names and open the review room.
3. Pause/seek the video and tag goals, shots, saves, passes, fouls or corners. Add an optional note. A goal counts as one shot attempt; do not add a duplicate shot tag for that goal.
4. Click timeline timestamps to revisit the footage. Edit a tag’s time, team, type or note; remove incorrect tags with one-step undo; save coaching notes.
5. Export the review as JSON. Delete a review to remove its locally stored footage and metadata.

**Data stays on this device:** IndexedDB stores video; localStorage stores review metadata. Clearing browser data removes reviews. Exported JSON contains timestamps, notes, observed counts and optional frame evidence, but not the video. Keep your original footage. Import the exported JSON from Your Matches, then reconnect the original video. Imports create a separate review and are labelled as supplied, unverified observations. Files up to 3 MB and 10,000 tags are supported.

**Manual-mode measurements:** duration and resolution come from the video decoder. Event counts come from explicitly labelled manual tags. Untagged events are unknown. This mode does not calculate automatic analytics. The vision mode separately estimates observed possession and pass candidates, with explicit coverage; it does not measure goals, xG or calibrated pitch heatmaps.

## Optional AI frame inspection

Copy `frontend/.env.example` to `frontend/.env.local` and set `ROBOFLOW_API_KEY` on the server. Restart Next.js. The manual upload mode will offer AI frame inspection.

Six evenly spaced JPEG frames are sent to Roboflow. The review shows the returned boxes, class labels, confidence and timestamps. A successful empty result means no objects were detected in that sample. Provider failures are reported as partial/failed; they never create substitute match statistics. Frame positions are camera-image coordinates and are not pitch coordinates.

Development permits local guest inference. **Production requires Firebase sign-in** and Firebase Admin Application Default Credentials to verify the user's token. Configure the public Firebase web fields and server ADC for the same project. Never put the Roboflow key or service-account private key in a `NEXT_PUBLIC_` variable. The per-process budget is 120 frames/hour with two in-flight requests. Add distributed per-user quotas before scaling across replicas.

## Repository map

- `frontend/app/upload`: automatic vision upload by default; optional manual review.
- `frontend/app/vision`, `components/vision`, `app/api/vision`: job progress, detection overlays, estimates and local-worker proxy.
- `backend/app/vision`: local YOLO + football ONNX engine with camera-motion tracking, temporal measurements and authenticated worker.
- `frontend/components/review/ReviewRoom.tsx`: player, timeline, manual tags, notes, export, deletion.
- `frontend/lib/review`: typed evidence contract, summary calculations, IndexedDB video storage.
- `frontend/app/api/infer`: bounded JPEG proxy, explicit errors, production token verification.
- `frontend/lib/firebase`: optional authentication and legacy cloud access. Local reviews are not silently synced to Firestore.
- `frontend/app/dashboard` and `report`: current review room; older statistics are clearly labelled unverified, including PDF exports.
- `firebase`: optional research deployment rules and storage-to-engine dispatch.
- `backend/app/services/pipeline.py`: older experimental cloud analytics; disabled by default and separate from the local vision engine.
- `docs/IMPROVEMENTS.md`: findings, implemented fixes and phased product/engineering roadmap.

## Tests

```sh
cd frontend
npm run test:unit
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

## Portable reviews

Use **Your Matches → Import review** to open a version 1 JSON export. The importer validates bounds, event identities, embedded JPEG frames and source fields; it ignores supplied user IDs, record IDs, remote URLs and summary totals. Existing reviews are never overwritten. Imported counts are recomputed from the supplied event list.

Choose **Reconnect the original video** in the imported review. Size, decoded duration and dimensions must match. New reviews also include a SHA-256 fingerprint of the first/last 64 KiB plus file size. This bounded sample helps catch wrong clips without reading a 500 MB video into memory, but is not a full-file hash. Older exports have no fingerprint: matching metadata does not prove identical footage. Imported observations remain visibly unverified.

The independent portability unit suite runs without a browser (`npm run test:unit`). The end-to-end suite includes import/reconnection/edit/undo/export and invalid import recovery.
