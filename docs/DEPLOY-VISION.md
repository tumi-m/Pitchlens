# Put automatic analysis online

The website (Vercel) cannot run the vision models: a serverless function has
no GPU/long-running CPU and rejects request bodies over 4.5 MB. The models run
in the **vision worker** (`backend/app/vision`), the same engine as the local
worker, deployed as a Docker container. The site talks to it server-to-server.

```
Browser ──4 MB chunks──▶ Vercel /api/vision/* ──Bearer token──▶ Vision worker (Railway)
                         (access code, owner key)               YOLO11s + ball model
```

- Videos are uploaded in 4 MB pieces through the website, so Vercel's limit
  never applies and a dropped connection resumes from the last piece.
- The worker's token lives only in Vercel and Railway environment variables.
  The browser never sees it.
- The **access code** (`VISION_ACCESS_CODE`, a Vercel variable) is optional.
  Unset, the site is open to anyone with its link, which suits a small test
  group. Set it before sharing the site wider: the worker is paid compute.
- Each browser gets a random owner key and only lists its own analyses.
- One analysis runs at a time. Others get "Another video is being analysed".
- Uploaded footage is deleted after 7 days by default. Measurements and the
  exportable JSON remain; the report then shows no video overlay.

## 1. Deploy the worker on Railway (about 10 minutes)

1. Sign in at [railway.com](https://railway.com) with GitHub. The Hobby plan is
   enough to start (usage-based; a busy CPU costs money while it analyses).
2. **New Project → Deploy from GitHub repo →** `tumi-m/Pitchlens`.
3. Open the new service → **Settings**:
   - **Root Directory:** `backend`.
   - **Config-as-code → Railway Config File:** `/backend/railway.toml`. This
     path starts from the repository root: Railway does **not** look inside
     the Root Directory for it. Without this step Railway builds the older
     `backend/Dockerfile` (a different API) instead of the vision worker.
   - **Networking → Generate Domain.** Copy the `https://….up.railway.app` URL.
4. **Variables** tab, add:

   | Variable | Value |
   | --- | --- |
   | `VISION_SERVICE_TOKEN` | A long random secret, e.g. from `openssl rand -hex 32` |
   | `VISION_THREADS` | Number of vCPUs you give the service (e.g. `8`) |
   | `RAILWAY_DOCKERFILE_PATH` | `vision.Dockerfile` (a safeguard that selects the right image even if the config-file step was missed) |
   | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | Required for full matches on a hosted worker (modal.com → Settings → API Tokens; see GPU analysis with Modal). The CPU-only alternative is `VISION_ALLOW_CPU_FALLBACK=1` (about an hour per 40-minute match on 4 vCPUs). With neither, the worker runs only 20-second tests: `/health` shows `"fullMatch": false`, the upload page says so, and a full match is refused before its upload (503, code `full-match-unavailable`). |

   Optional: `VISION_RETENTION_HOURS` (default `168`), `VISION_ALLOWED_HOSTS`
   (default `*`; set to your Railway domain plus `healthcheck.railway.app` to
   lock it down), `VISION_MAX_BYTES` (see below), `VISION_SITE_TRAINING=1`
   (lets the site start ball-model training; see docs/BALL-TRAINING.md) with
   `VISION_MAX_TRAINING_PER_DAY` (site-started training runs per 24 hours,
   default `2`, `0` = no limit; only site-started runs count, and a run that
   Modal refused is not counted).
5. **Add a volume** (right-click the service → Attach volume) with mount path
   **`/data`**. Without it every redeploy deletes all jobs and results. Size it
   for the upload limit below: a few matches within the retention window (for
   example 20-50 GB at the 2 GB default; a 5 GB Hobby volume holds only two
   such uploads), or lower the limit or the retention. The worker answers "out
   of storage" instead of filling the disk.

### Upload size limit (`VISION_MAX_BYTES`, `VISION_MAX_UPLOAD_MB`)

The largest accepted video: `VISION_MAX_BYTES` in bytes, or
`VISION_MAX_UPLOAD_MB` in MiB (at least 50); `VISION_MAX_BYTES` wins when both
are set. The default is 2 GiB (`2147483648`). Upload the camera's original
file: original 1080p recordings of a 40-minute match are 2-5 GB, and reduced
copies (360p) make the ball too small to track. An iPhone's default 1080p HEVC
is about 60 MB per minute, so 500 MB is only about 8 minutes. The upload page
reads the limit from `/health` and refuses a larger file before sending it,
showing how many minutes fit at that file's bitrate. Before raising it:

- Size the volume for about **ten uploads of that size** within the retention
  window (a 2 GB limit needs a volume of about 20 GB).
- The Modal GPU downloads the **whole file** for every analysis and every
  rerun, so a larger limit means longer fetches and more transfer.
- YouTube downloads share the same limit (and the free space; see YouTube
  links).

A value that is not a positive whole number is ignored (with a warning in the
log) and the default applies.

Once another start is waiting for the worker, an upload must finish within
`VISION_UPLOAD_MAX_SECONDS` (default one hour; a large declared file gets
longer at a 2 Mbit/s floor, so 2 GB gets about 2.4 hours, up to 4 hours). The
uploader is told the limit and how far it got. A lone slow upload is allowed
to finish.
6. Deploy. The first build downloads PyTorch and the model weights and takes
   several minutes. Check the build log mentions `vision.Dockerfile` and
   `Model: /app/models/yolo11s.pt`. When it is live,
   `https://<your-domain>/healthz` returns `{"ok":true}`. (If it returns
   `{"detail":"Not Found"}` or mentions `/api/v1`, the old image was built:
   redo step 3.)

The build downloads the checksum-pinned YOLO11s weights (GitHub) and the
football ball model (Hugging Face). The optional full-pitch broadcast models
come from Google Drive; if Drive refuses, the build continues and that profile
is disabled in the upload screen.

## 2. Connect the website (Vercel)

Vercel → project `pitchlens_1` → **Settings → Environments → Production →
Environment Variables** (tick Production and Preview):

| Variable | Value |
| --- | --- |
| `VISION_SERVICE_URL` | The Railway URL, e.g. `https://pitchlens-vision.up.railway.app` (no trailing slash needed) |
| `VISION_SERVICE_TOKEN` | Exactly the same secret as on Railway |
| `VISION_ACCESS_CODE` | Optional. A code you give to people allowed to analyse videos. Use at least 12 random characters: wrong guesses are not rate-limited. The same code also unlocks the optional Roboflow frame inspection in manual review when Firebase sign-in is not configured. **Leave it unset only for a small test group:** anyone who has the site's link can then start analyses on your paid worker (each browser still sees only its own matches). While it is unset, AI frame inspection and site training stay off. With or without a code, the worker starts at most `VISION_MAX_JOBS_PER_DAY` analyses per 24 hours (a Railway variable; default 30, 0 = no limit). Set a spending limit in Modal too. To open a running site, deploy a build that supports it first, confirm it shows Ready, then delete the variable and redeploy: an older build refuses all analysis without a code. |

Then **Deployments → ⋯ → Redeploy**. Environment variables only reach new
deployments.

## 3. Check it

1. Open `https://pitchlens1.vercel.app/api/vision/health`. Expect
   `"available":true,"hosted":true`, `"fullMatch":true` and
   `"accessRequired":true` when an access code is set (`false` when the site
   is open).
   - `"fullMatch":false` → the worker has neither the Modal tokens nor
     `VISION_ALLOW_CPU_FALLBACK=1` (section 1): only 20-second tests run.
   - `"configured":false` → a Vercel variable is missing, or you did not redeploy.
   - `"Cannot reach the vision server"` → wrong URL or the Railway service is down.
   - `"Vision service authentication required"` → the two tokens differ.
2. Go to `/upload`, choose an MP4 (or iPhone MOV), enter the access code and
   press **Analyse video automatically**. Upload progress is shown piece by
   piece; then the report page shows live analysis progress with a time
   estimate. You can close the tab and return via **Your matches**.

## Speed and cost

A hosted worker analyses full matches on the Modal GPU (see GPU analysis with
Modal). The CPU timing below applies to a local worker, to 20-second tests and
section re-runs, and to a hosted worker with `VISION_ALLOW_CPU_FALLBACK=1`.

Measured on the real TRYZUB vs LUNA FC recording (640×360, 29.97 fps) with the
quick setting (3 analysed frames/second) on a 4-vCPU machine: 60 seconds of
footage took 99 seconds. A 38-minute match therefore needs roughly an hour on
4 vCPUs. More vCPUs (with `VISION_THREADS` set to match) shorten this; the
6 and 10 frames/second settings take proportionally longer. The quick setting
costs ball-event accuracy: ball statistics need 6 frames/second, about twice
the CPU time. Railway bills CPU time while an analysis runs; an idle worker
costs very little.

To run full matches on your own GPU host instead, use a CUDA build of PyTorch
and `VISION_DEVICE=cuda`. The ball model runs through ONNX Runtime: on Modal it
runs on CUDA (the GPU image installs `onnxruntime-gpu`). `vision.Dockerfile`
ships the CPU `onnxruntime`, so a self-hosted GPU host must install
`onnxruntime-gpu==1.30.0` for the ball model to use CUDA; no code change is
needed (the worker asks for CUDA whenever `VISION_DEVICE=cuda`).

## Redeploys and failures

A redeploy restarts the worker. An analysis in progress is marked
**interrupted** (not cancelled); its saved upload can be retried from the
report. An upload in progress survives: after the restart the worker keeps
the newest unfinished upload (trimmed to the bytes it had confirmed) and
reserves itself for it again. The browser keeps retrying through the outage
(a 502/503/504 from the site counts as a dropped connection, for up to four
minutes) and continues from the server's offset. A page reloaded meanwhile
resumes too when the same file is chosen again. Any older unfinished upload is
marked interrupted and its partial file deleted. An upload that stops half-way
(a phone that locked or switched apps) keeps what it received, so pressing
Analyse with the same file continues from there. After five minutes without data
(`VISION_UPLOAD_IDLE_SECONDS`) it gives way to another browser's analysis, which
deletes the partial file; with nobody else waiting it is kept for two hours
(`VISION_UPLOAD_ABANDON_SECONDS`), including one kept across a restart, and only
then released as abandoned. The same browser starting a different upload is told
about its paused one instead of losing it.

`railway.toml` sets `drainingSeconds = 40` (the variable form is
`RAILWAY_DEPLOYMENT_DRAINING_SECONDS`): Railway waits that long between
stopping the old container and killing it, so the worker records a running
analysis as interrupted by the redeploy, and its retry does not use up one of
the three attempts. A redeploy can therefore take up to about 40 s longer to
switch over. Each job also records `RAILWAY_DEPLOYMENT_ID`: a job that an
earlier deployment left unfinished counts as stopped by a redeploy even when
that process was killed before it could say so, so that retry is not charged
an attempt either (a crash restarts the same deployment and still counts).
`watchPatterns = ["/backend/**"]` means pushes that change only the frontend or
the docs do not redeploy the worker; changing a Railway variable still does.

Cancel takes effect within a second, also while the GPU is still starting.
`VISION_GPU_START_SECONDS` (default `900`) is how long a job waits for the
GPU's first reply (scheduling, cold start) before it counts the GPU as
unavailable. `VISION_SHUTDOWN_WAIT_SECONDS` (default `10`) is how long a
shutdown waits for running jobs to stop, so a GPU job can leave its Modal app
(which stops the GPU).
Check the Railway **Deploy logs** for model or decoding errors.

## Operator checks

The worker's operator view needs the service token and is never forwarded by
the site:

```sh
curl -H "Authorization: Bearer $VISION_SERVICE_TOKEN" https://<worker>/ops
```

It shows the running job (stage, progress, GPU or CPU), the analyses started in
the last 24 hours against `VISION_MAX_JOBS_PER_DAY` and when the next start
frees up, site training starts against `VISION_MAX_TRAINING_PER_DAY`, the
training state, free and total storage (`storage.low`), the number of jobs,
whether Modal and a public URL are set, the latest GPU errors, the failed or
interrupted jobs of the last 24 hours with their reason, and the deployed
commit. It never shows owner keys, request IDs, video grants or tokens.

The deploy log has one line when each job starts and ends
(`job=… event=start engine=gpu`, then `event=completed`, `failed`,
`interrupted` or `cancelled` with its duration and reason). At startup and then
hourly, the worker logs `Volume low: N MB free of M MB` when the volume has
less free space than one upload at the size limit plus 256 MB; attach a larger
volume, lower `VISION_MAX_BYTES`, or delete old matches before uploads are
refused as out of storage.

## Run the same container anywhere

```sh
cd backend
docker build -f vision.Dockerfile -t pitchlens-vision .
docker run -p 8100:8100 -e VISION_SERVICE_TOKEN=change-me -e VISION_THREADS=4 \
  -v pitchlens-data:/data pitchlens-vision
```

Put it behind HTTPS (the website refuses a non-loopback `http://` worker) and
set the three Vercel variables as above. With `VISION_PUBLIC_URL` set, the
worker counts as hosted: full matches then need the Modal tokens,
`VISION_DEVICE=cuda` on a GPU host, or `VISION_ALLOW_CPU_FALLBACK=1`.

## YouTube links

The upload page also accepts a YouTube link. The worker fetches the video itself
with yt-dlp (plus the Deno runtime it now requires): the sharpest version up to
1080p that fits the upload limit (`VISION_MAX_BYTES`, 2 GB by default) and the
free space, stepping down to 720p and 480p, so
nothing large passes through the browser or Vercel. Users must confirm they
filmed the video or have the owner's permission.

Two limits to know about:

- YouTube's terms only allow downloads where YouTube offers a download button.
  Keep this to footage your users own (their own channel or club uploads).
- YouTube often blocks downloads from datacenter addresses such as Railway's
  ("Sign in to confirm you're not a bot"). The job then fails with a message
  asking the user to upload the file instead. `VISION_YOUTUBE_PROXY` and
  `VISION_YOUTUBE_COOKIES` exist for operators, but cookies can get the Google
  account banned; direct upload remains the reliable route.

## GPU analysis with Modal (recommended)

Add `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` (modal.com → Settings → API
Tokens) to the Railway service variables. Each job's inference then runs on a
Modal GPU (`VISION_MODAL_GPU`, default `L4`); Railway keeps uploads, storage,
status and playback. The GPU fetches the video from the worker's public
address (`RAILWAY_PUBLIC_DOMAIN`, or `VISION_PUBLIC_URL`) with the service
token. The first job builds the GPU image on Modal (a few minutes, then
cached). On a hosted worker, a full match whose GPU fails (bad token, no
credit, no GPU within `VISION_GPU_START_SECONDS`) ends with "GPU is
unavailable. Your upload is saved; retry when GPU service returns." unless
`VISION_ALLOW_CPU_FALLBACK=1` is set; when no GPU ran, neither the daily start
nor the attempt is charged. 20-second tests and section re-runs fall back to
the CPU and say so. An engine crash on the GPU is not re-run on the CPU.
`VISION_USE_MODAL=0` switches the GPU off. Set a spend limit in Modal →
Settings → Usage & billing.

### Faster or cheaper GPU runs

- `VISION_MODAL_GPU` on Railway picks the card: `L4` (default, good value),
  `A10G`, or `L40S` (fastest, about 2–3× the hourly price). Detectors run in
  FP16 on any GPU.
- The upload page defaults to 6 analysed frames/second when a GPU is
  available. Detailed (10 frames/second) takes about two-thirds more GPU time
  than 6; in tests on labelled tracking data it found somewhat more passes
  (simulated, E20). The quick 3 frames/second setting misses many passes and
  shots: use it for player load only.
- Modal → Settings → Usage & billing shows the cost per run ("Ephemeral Apps").
- [COST.md](COST.md): what a match costs per stage and per GPU type, and why
  `L4` and scale-to-zero stay the defaults.

### Automatic pitch setup for fixed venue cameras

When a match finishes, the worker tries the owner's saved venues with the same
video size, newest first. Each is checked against the painted lines in the new
footage, and the first that lines up is applied automatically (the pitch panel
says "Set up automatically from saved venue …"). Nothing is applied without
the footage to check against or over a setup the reviewer made.
`VISION_AUTO_VENUE=0` turns this off.

### Engine tuning variables

- `VISION_DEVICE`: `auto` selects CUDA, Apple MPS, then CPU; an explicit device overrides selection. Modal sets `cuda`.
- `VISION_BATCH`: frames per player-model call, including kit-colour sampling (default 8 on CUDA, 4 on MPS, 2 on CPU).
- `VISION_FAINT=0`: turn off track-before-detect ball recovery (default on).
- `VISION_TRAJECTORY=0`: turn off recovery of weak candidates near strong moving-ball observations (default on).
- `VISION_THREADS`: CPU threads for inference.
- `VISION_PIPELINE`: overlap model inference with per-frame tracking. Default
  on with a CUDA GPU, off on CPU and Apple MPS; `1` forces it on, `0` off.
  Results are identical; one extra batch of frames is held in memory (about
  200 MB at 4K with batch 8).
- `VISION_TRACKER=legacy`: previous player tracker (default is the
  ByteTrack-style tracker from pipeline 2.2).
- `VISION_KEEP_CANDIDATES=1`: keep every ball candidate in result.json (large;
  for tuning only).

### Match analytics, pitch setup and venues (no new configuration)

Pitch calibration, event detection and reviewer decisions run on the Railway
worker itself (CPU, a few seconds per match; applying a pitch setup to a
moving camera re-reads the video once, about a minute). They are stored next
to each job on the `/data` volume: `calibration.json`, `review.json`
(append-only), `details.json` (team names, title, match date), `analysis.json`
(a cache). Saved venues live in
`/data/venues/`. The website needs no new variables; redeploy both the worker
and the site so the new `/analysis`, `/calibration`, `/review` and `/venues`
routes exist on both sides.


## Private reports and recovery (commercial hardening)

Deploy the worker and frontend from the same release. Set `VISION_REQUIRE_OWNER=1`
on any hosted worker; it is also enabled by the public worker URL/Railway domain.
The proxy forwards the browser capability on every match request. Playback/export
use a secure HttpOnly SameSite cookie established by the successful status request.
Raw match links no longer grant access. Keep the original browser storage until
account migration exists. Ownerless legacy hosted jobs are intentionally withheld;
do not auto-assign them to the first caller. Local unscoped access remains available
for an operator-led migration after ownership verification.

Modal receives a four-hour, per-video signed read grant in addition to the service
token. The proxy never forwards browser-supplied GPU grants. Keep service keys secret.

Hosted full matches fail visibly when GPU execution is unavailable, preserving the
complete upload for retry. CPU diagnostics remain possible; setting
`VISION_ALLOW_CPU_FALLBACK=1` explicitly restores full-match CPU fallback, with the
associated latency risk. A hosted worker with neither Modal nor that flag refuses
full matches, full retries and full re-runs before anything is uploaded or counted. `/jobs/{id}/retry` reuses saved complete footage for stopped
jobs and caps attempts at three. It does not recover partial interrupted uploads
(the browser resumes those itself; see Redeploys and failures). Job reads carry
`retryable`, so the report offers Retry only when it can work.

`DELETE /jobs/{id}` removes the match directory, derived venue setups and in-memory
analysis cache; it refuses while upload/inference/calibration is active. External
volume backups remain subject to the operator's backup-expiry policy.

Review requests accept `requestId` and `expectedRevision` (number of prior decisions).
Replaying the same ID/body returns the original added-event IDs without duplication;
conflicting reuse or a stale revision returns 409. This is durable on the configured
volume and serialized in one worker process, not a multi-replica transaction system.

## South Africa payment sandbox

The `/billing` page prepares Paystack checkout in ZAR. It stays disabled until a
test merchant key, callback and test amount are configured on the worker. Live
keys are refused. The SQLite sandbox ledger lives under `VISION_DATA_DIR` on the
persistent volume; it is separate from match deletion. Follow
[Paystack setup](market-ready/PAYSTACK-SETUP.md) for environment variables,
webhook configuration, recovery and the remaining live-launch requirements.
