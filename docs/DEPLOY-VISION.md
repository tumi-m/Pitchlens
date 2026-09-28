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
- An **access code** is required before anyone can start an analysis. The
  worker is paid compute; without a code, strangers could keep it busy.
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

   Optional: `VISION_RETENTION_HOURS` (default `168`), `VISION_ALLOWED_HOSTS`
   (default `*`; set to your Railway domain plus `healthcheck.railway.app` to
   lock it down).
5. **Add a volume** (right-click the service → Attach volume) with mount path
   **`/data`**. Without it every redeploy deletes all jobs and results. Hobby
   volumes are 5 GB; that holds about ten 500 MB uploads within the retention
   window. The worker answers "out of storage" instead of filling the disk.
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
| `VISION_ACCESS_CODE` | A code you give to people allowed to analyse videos. Use at least 12 random characters: wrong guesses are not rate-limited. The same code also unlocks the optional Roboflow frame inspection in manual review when Firebase sign-in is not configured. |

Then **Deployments → ⋯ → Redeploy**. Environment variables only reach new
deployments.

## 3. Check it

1. Open `https://pitchlens1.vercel.app/api/vision/health`. Expect
   `"available":true,"hosted":true,"accessRequired":true`.
   - `"configured":false` → a Vercel variable is missing, or you did not redeploy.
   - `"Cannot reach the vision server"` → wrong URL or the Railway service is down.
   - `"Vision service authentication required"` → the two tokens differ.
2. Go to `/upload`, choose an MP4 (or iPhone MOV), enter the access code and
   press **Analyse video automatically**. Upload progress is shown piece by
   piece; then the report page shows live analysis progress with a time
   estimate. You can close the tab and return via **Your matches**.

## Speed and cost

Measured on the real TRYZUB vs LUNA FC recording (640×360, 29.97 fps) with the
quick setting (3 analysed frames/second) on a 4-vCPU machine: 60 seconds of
footage took 99 seconds. A 38-minute match therefore needs roughly an hour on
4 vCPUs. More vCPUs (with `VISION_THREADS` set to match) shorten this; the
6 and 10 frames/second settings take proportionally longer. Railway bills CPU
time while an analysis runs; an idle worker costs very little.

For much faster analysis, run the same image on a GPU host with a CUDA build of
PyTorch and `VISION_DEVICE=cuda`. The ball model currently runs through ONNX
Runtime on CPU; switching it to `onnxruntime-gpu` is a separate change.

## Redeploys and failures

A redeploy restarts the worker. An analysis in progress is marked
**interrupted** (not cancelled); submit the video again. Uploads that stop
half-way free the worker after five minutes (`VISION_UPLOAD_IDLE_SECONDS`).
Check the Railway **Deploy logs** for model or decoding errors.

## Run the same container anywhere

```sh
cd backend
docker build -f vision.Dockerfile -t pitchlens-vision .
docker run -p 8100:8100 -e VISION_SERVICE_TOKEN=change-me -e VISION_THREADS=4 \
  -v pitchlens-data:/data pitchlens-vision
```

Put it behind HTTPS (the website refuses a non-loopback `http://` worker) and
set the three Vercel variables as above.

## YouTube links

The upload page also accepts a YouTube link. The worker fetches the video itself
with yt-dlp (plus the Deno runtime it now requires), up to 1080p and 500 MB, so
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
cached). If Modal fails (bad token, no credit), the job falls back to the CPU
and says so. `VISION_USE_MODAL=0` switches the GPU off. Set a spend limit in
Modal → Settings → Usage & billing.

### Faster or cheaper GPU runs

- `VISION_MODAL_GPU` on Railway picks the card: `L4` (default, good value),
  `A10G`, or `L40S` (fastest, about 2–3× the hourly price). Detectors run in
  FP16 on any GPU.
- The upload page defaults to 6 analysed frames/second when a GPU is
  available; 10 fps follows the ball best and costs proportionally more.
- Modal → Settings → Usage & billing shows the cost per run ("Ephemeral Apps").
