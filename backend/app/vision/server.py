"""Authenticated vision worker. Runs on loopback locally or as a hosted container.

Video never goes to a hosted inference provider: models run inside this process.
The long-lived service token stays server-side (Next.js proxy -> worker).
"""

import asyncio
import hmac
import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.vision import gpu
from app.vision.engine import probe, run_video
from app.vision.profiles import available_profiles

ROOT = Path(os.getenv("VISION_DATA_DIR", ".vision")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = os.getenv("VISION_SERVICE_TOKEN", "")
MAX_BYTES = 500 * 1024 * 1024
# Chunked uploads keep every request small enough for serverless proxies (Vercel: 4.5 MB).
MAX_CHUNK = 8 * 1024 * 1024
# A chunked upload that stops sending data releases the worker after this long.
UPLOAD_IDLE_SECONDS = int(os.getenv("VISION_UPLOAD_IDLE_SECONDS", "300"))
# Even a trickling upload cannot hold the single worker longer than this.
UPLOAD_MAX_SECONDS = int(os.getenv("VISION_UPLOAD_MAX_SECONDS", "3600"))
# Hosted deployments delete uploaded footage after this many hours (unset = keep forever).
RETENTION_HOURS = float(os.getenv("VISION_RETENTION_HOURS", "0") or 0)
# Open-ended playback ranges are capped so one response never streams a whole match.
RANGE_CAP = 16 * 1024 * 1024
OWNER = re.compile(r"[a-f0-9]{32}")
# Re-entrant: stale-upload release runs while create() already holds the lock.
lock = threading.RLock()
active = None
stopping = False
cancellations = {}
upload_activity = {}
upload_started = {}
pool = ThreadPoolExecutor(max_workers=1)
# Calibration and analytics are CPU post-processing of a finished result: they
# must never queue behind (or block) an upload or a GPU analysis.
post_pool = ThreadPoolExecutor(max_workers=1)
post_lock = threading.Lock()
MAX_JSON_BODY = 256 * 1024


def auth(request: Request):
    # Platform health checks (Railway, Docker) carry no credentials and learn nothing.
    if request.url.path == "/healthz":
        return
    if not TOKEN or not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {TOKEN}"
    ):
        raise HTTPException(401, "Vision service authentication required")


def allowed_hosts():
    value = os.getenv("VISION_ALLOWED_HOSTS", "")
    hosts = [h.strip() for h in value.split(",") if h.strip()]
    return hosts or ["127.0.0.1", "localhost", "testserver"]


app = FastAPI(
    title="Pitchlens vision worker", docs_url=None, redoc_url=None, dependencies=[Depends(auth)]
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())


def folder(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Job not found")
    directory = ROOT / job_id
    if not (directory / "status.json").is_file():
        raise HTTPException(404, "Job not found")
    return directory


def write_status(directory, data):
    temporary = directory / "status.tmp"
    temporary.write_text(json.dumps(data))
    temporary.replace(directory / "status.json")


def load_status(directory):
    data = json.loads((directory / "status.json").read_text())
    if data["status"] in ("uploading", "processing") and data["id"] != active:
        data.update(status="interrupted", stage="Worker stopped. Submit the video again to retry.")
    return data


def public(data):
    """Owner keys are listing capabilities; never echo them back."""
    return {k: v for k, v in data.items() if k != "owner"}


def work(directory, status, event):
    global active

    def update(**kw):
        status.update(kw)
        write_status(directory, status)

    try:
        status["status"] = "processing"
        update(stage="Opening video", progress=0)
        options = dict(
            progress=update,
            cancelled=event.is_set,
            profile=status.get("profile", "general"),
            sample_fps=status.get("sampleFps", 3),
            max_seconds=status.get("maxSeconds"),
            start_seconds=status.get("startSeconds", 0),
            ball_search=status.get("ballSearch", "exhaustive"),
        )
        ran_on_gpu = False
        if gpu.modal_enabled():
            try:
                update(stage="Starting a GPU", progress=0)
                gpu.run_on_modal(
                    status["id"], directory / "result.json", TOKEN, **options
                )
                ran_on_gpu = True
            except gpu.GPUUnavailable as exc:
                import logging

                logging.warning("GPU unavailable, using CPU: %s", exc)
                update(stage="GPU unavailable; analysing on the CPU (slower)", progress=0)
        if not ran_on_gpu:
            run_video(directory / "video", directory / "result.json", **options)
        update(
            status="completed",
            stage="Analysis complete",
            progress=100,
            engine="gpu" if ran_on_gpu else "cpu",
        )
    except InterruptedError:
        if stopping:
            # A redeploy or restart is not the user's cancellation: allow a retry.
            update(status="interrupted", stage="Worker restarted. Submit the video again to retry.")
        else:
            update(status="cancelled", stage="Analysis cancelled")
    except Exception as exc:
        import logging

        logging.exception("Vision job failed")
        update(
            status="failed",
            stage=str(exc)
            if isinstance(exc, ValueError)
            else "Vision processing failed. Check the worker log.",
        )
    finally:
        with lock:
            if active == status["id"]:
                active = None
            cancellations.pop(status["id"], None)


def stop_jobs():
    global stopping
    stopping = True
    for event in list(cancellations.values()):
        event.set()


def recover_after_restart():
    """Persist what load_status infers: work in flight when the process died is interrupted."""
    for path in ROOT.glob("*/status.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("status") not in ("uploading", "processing") or data.get("id") == active:
            continue
        if data["status"] == "uploading":
            (path.parent / "video").unlink(missing_ok=True)
        data.update(status="interrupted", stage="Worker restarted. Submit the video again to retry.")
        write_status(path.parent, data)
    for temporary in ROOT.glob("*/*.tmp"):
        temporary.unlink(missing_ok=True)


app.add_event_handler("shutdown", stop_jobs)


def release(job_id, directory, status, stage):
    """Fail an unfinished upload, delete its partial video and free the worker."""
    global active
    with lock:
        status.update(status="failed", stage=stage)
        if directory.is_dir():
            write_status(directory, status)
            (directory / "video").unlink(missing_ok=True)
        upload_activity.pop(job_id, None)
        upload_started.pop(job_id, None)
        if active == job_id:
            active = None


def release_stale_upload():
    """A browser that closed mid-upload must not block the worker forever."""
    job_id = active
    if job_id is None or job_id in cancellations:
        return
    last = upload_activity.get(job_id)
    if last is None:
        return
    now = time.monotonic()
    idle = now - last >= UPLOAD_IDLE_SECONDS
    overdue = now - upload_started.get(job_id, now) >= UPLOAD_MAX_SECONDS
    if not (idle or overdue):
        return
    directory = ROOT / job_id
    try:
        status = json.loads((directory / "status.json").read_text())
    except (OSError, ValueError):
        status = {"id": job_id, "createdAt": time.time()}
    if status.get("status", "uploading") == "uploading":
        release(job_id, directory, status, "Upload stopped before the video was complete.")


def sweep_stale():
    with lock:
        release_stale_upload()


def expired(data):
    return (
        RETENTION_HOURS > 0
        and data.get("id") != active
        and data.get("createdAt", time.time()) < time.time() - RETENTION_HOURS * 3600
    )


def delete_expired():
    """Remove uploaded footage after the retention period; keep the measured result."""
    sweep_stale()
    if RETENTION_HOURS <= 0:
        return
    for path in ROOT.glob("*/status.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not expired(data):
            continue
        video_path = path.parent / "video"
        if video_path.exists():
            video_path.unlink(missing_ok=True)
            data["videoDeleted"] = True
            write_status(path.parent, data)


def start_retention_sweeper():
    """Footage must expire on schedule even when nobody uploads or restarts the worker."""
    if RETENTION_HOURS <= 0:
        return

    def sweep():
        while not stopping:
            try:
                delete_expired()
            except Exception:
                import logging

                logging.exception("Retention sweep failed")
            time.sleep(3600)

    threading.Thread(target=sweep, name="retention-sweeper", daemon=True).start()


app.add_event_handler("startup", recover_after_restart)
app.add_event_handler("startup", start_retention_sweeper)


def check_space(size):
    free = shutil.disk_usage(ROOT).free
    if free < size + 256 * 1024 * 1024:
        raise HTTPException(507, "The analysis server is out of storage. Try again later.")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/health")
def health():
    sweep_stale()
    return {
        "available": "general" in available_profiles(),
        "profiles": available_profiles(),
        "activeJob": active is not None,
        "mode": "computer-vision",
        "diagnostics": True,
        "maxBytes": MAX_BYTES,
        "maxChunk": MAX_CHUNK,
        "retentionHours": RETENTION_HOURS or None,
        "gpu": gpu.modal_enabled(),
        # Pitch calibration, event detection and reviewer decisions.
        "analytics": True,
    }


@app.get("/jobs")
def jobs(request: Request):
    owner = request.query_params.get("owner")
    if owner is not None and not OWNER.fullmatch(owner):
        raise HTTPException(400, "Invalid owner")
    sweep_stale()
    entries = []
    for p in ROOT.glob("*/status.json"):
        try:
            data = load_status(p.parent)
        except (OSError, ValueError, KeyError):
            continue
        # Jobs created before ownership existed (local installs) stay visible.
        if owner is not None and data.get("owner") not in (owner, None):
            continue
        entries.append(public(data))
    return sorted(entries, key=lambda x: x["createdAt"], reverse=True)[:100]


def begin(directory, status, size):
    """Decode-check the stored video and hand it to the single inference thread."""
    metadata = probe(directory / "video")
    if status.get("startSeconds", 0) >= metadata["duration"]:
        raise ValueError("Diagnostic start must be inside the video")
    job_id = status["id"]
    with lock:
        # Cancelled or released while the probe ran: never queue work for it.
        if active != job_id or job_id not in upload_activity:
            raise HTTPException(409, "This upload was cancelled. Start again.")
        status.update(video=metadata, fileSize=size, status="processing")
        status.pop("receivedBytes", None)
        status.pop("expectedBytes", None)
        write_status(directory, status)
        upload_activity.pop(job_id, None)
        upload_started.pop(job_id, None)
        event = threading.Event()
        cancellations[job_id] = event
        pool.submit(work, directory, status, event)
    return public(status)


@app.post("/jobs")
async def create(request: Request):
    global active
    if request.headers.get("content-type", "").split(";")[0] not in (
        "video/mp4",
        "video/quicktime",
        "application/octet-stream",
    ):
        raise HTTPException(415, "Upload a video file")
    profile = request.query_params.get("profile", "general")
    if profile not in ("general", "broadcast", "small-ball"):
        raise HTTPException(400, "Unknown footage profile")
    if profile not in available_profiles():
        raise HTTPException(503, "The selected vision models are not installed")
    try:
        sample_fps = int(request.query_params.get("fps", "3"))
    except ValueError as exc:
        raise HTTPException(400, "Choose 3, 6 or 10 analysed frames per second") from exc
    if sample_fps not in (3, 6, 10):
        raise HTTPException(400, "Choose 3, 6 or 10 analysed frames per second")
    ball_search = request.query_params.get("search", "exhaustive")
    if ball_search not in ("exhaustive", "adaptive"):
        raise HTTPException(400, "Invalid ball search mode")
    diagnostic = request.query_params.get("diagnostic", "false")
    if diagnostic not in ("true", "false"):
        raise HTTPException(400, "Invalid diagnostic option")
    try:
        start_seconds = int(request.query_params.get("start", "0"))
    except ValueError as exc:
        raise HTTPException(400, "Diagnostic start must be a whole number of seconds") from exc
    if not 0 <= start_seconds < 4 * 3600 or (diagnostic == "false" and start_seconds != 0):
        raise HTTPException(400, "Invalid diagnostic start")
    owner = request.query_params.get("owner")
    if owner is not None and not OWNER.fullmatch(owner):
        raise HTTPException(400, "Invalid owner")
    # A declared size means a chunked upload: this request only reserves the worker.
    expected = request.query_params.get("size")
    if expected is not None:
        try:
            expected = int(expected)
        except ValueError as exc:
            raise HTTPException(400, "Invalid video size") from exc
        if not 0 < expected <= MAX_BYTES:
            raise HTTPException(413, "Maximum video size is 500 MB")
    await asyncio.to_thread(delete_expired)
    job_id = uuid.uuid4().hex
    with lock:
        release_stale_upload()
        if active is not None:
            raise HTTPException(
                409, "Another video is being analysed. Wait for it to finish or cancel it."
            )
        active = job_id
    upload_activity[job_id] = upload_started[job_id] = time.monotonic()
    directory = ROOT / job_id
    title = request.query_params.get("title", "Football match")[:200]
    status = {
        "id": job_id,
        "title": title,
        "createdAt": time.time(),
        "status": "uploading",
        "stage": "Receiving video",
        "progress": 0,
        "profile": profile,
        "sampleFps": sample_fps,
        "maxSeconds": 20 if diagnostic == "true" else None,
        "startSeconds": start_seconds,
        "ballSearch": ball_search,
    }
    if owner:
        status["owner"] = owner
    if expected is not None:
        status.update(expectedBytes=expected, receivedBytes=0)
    try:
        directory.mkdir()
        write_status(directory, status)
        check_space(expected or 0)
        if expected is not None:
            (directory / "video").touch()
            return public(status)
        size = 0
        with (directory / "video").open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Maximum video size is 500 MB")
                f.write(chunk)
                upload_activity[job_id] = time.monotonic()
        if not size:
            raise HTTPException(400, "Video file is empty")
        return await asyncio.to_thread(begin, directory, status, size)
    except BaseException as exc:
        release(job_id, directory, status, "Upload failed or video could not be decoded")
        if isinstance(exc, ValueError):
            raise HTTPException(400, str(exc)) from exc
        raise


def fetch_and_work(directory, status, event, url):
    """Runs on the single inference thread: download, decode-check, then analyse."""
    global active
    from app.vision.youtube import download

    def update(**kw):
        status.update(kw)
        write_status(directory, status)

    handed_over = False
    try:
        update(stage="Downloading from YouTube", progress=0)
        path, info = download(
            url, directory, MAX_BYTES, progress=update, cancelled=event.is_set
        )
        path.replace(directory / "video")
        size = (directory / "video").stat().st_size
        metadata = probe(directory / "video")
        if info.get("title") and status.get("title") == "YouTube match":
            status["title"] = str(info["title"])[:200]
        status.pop("receivedBytes", None)
        status.update(video=metadata, fileSize=size, source=info)
        handed_over = True
        work(directory, status, event)
    except InterruptedError:
        update(status="cancelled", stage="Download cancelled")
    except ValueError as exc:
        update(status="failed", stage=str(exc))
    except Exception:
        import logging

        logging.exception("YouTube fetch failed")
        update(status="failed", stage="Could not fetch this YouTube video.")
    finally:
        if not handed_over:
            for leftover in directory.glob("download.*"):
                leftover.unlink(missing_ok=True)
            (directory / "video").unlink(missing_ok=True)
            with lock:
                if active == status["id"]:
                    active = None
                cancellations.pop(status["id"], None)


@app.post("/jobs/from-url")
async def create_from_url(request: Request):
    """Analyse a YouTube video by link: the worker downloads it itself."""
    global active
    from app.vision.youtube import video_id

    try:
        vid = video_id(request.query_params.get("url", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    profile = request.query_params.get("profile", "general")
    if profile not in ("general", "broadcast", "small-ball"):
        raise HTTPException(400, "Unknown footage profile")
    if profile not in available_profiles():
        raise HTTPException(503, "The selected vision models are not installed")
    try:
        sample_fps = int(request.query_params.get("fps", "3"))
    except ValueError as exc:
        raise HTTPException(400, "Choose 3, 6 or 10 analysed frames per second") from exc
    if sample_fps not in (3, 6, 10):
        raise HTTPException(400, "Choose 3, 6 or 10 analysed frames per second")
    owner = request.query_params.get("owner")
    if owner is not None and not OWNER.fullmatch(owner):
        raise HTTPException(400, "Invalid owner")
    await asyncio.to_thread(delete_expired)
    check_space(MAX_BYTES)
    job_id = uuid.uuid4().hex
    with lock:
        release_stale_upload()
        if active is not None:
            raise HTTPException(
                409, "Another video is being analysed. Wait for it to finish or cancel it."
            )
        active = job_id
        directory = ROOT / job_id
        directory.mkdir()
        status = {
            "id": job_id,
            "title": request.query_params.get("title", "").strip()[:200] or "YouTube match",
            "createdAt": time.time(),
            "status": "uploading",
            "stage": "Waiting to download from YouTube",
            "progress": 0,
            "profile": profile,
            "sampleFps": sample_fps,
            "sourceUrl": f"https://www.youtube.com/watch?v={vid}",
        }
        if owner:
            status["owner"] = owner
        write_status(directory, status)
        event = threading.Event()
        cancellations[job_id] = event
        pool.submit(fetch_and_work, directory, status, event, status["sourceUrl"])
    return public(status)


def receiving(job_id):
    directory = folder(job_id)
    status = json.loads((directory / "status.json").read_text())
    if job_id != active or status.get("status") != "uploading" or "expectedBytes" not in status:
        raise HTTPException(409, "This upload is no longer accepting data. Start again.")
    return directory, status


@app.put("/jobs/{job_id}/video")
async def append(job_id: str, request: Request):
    receiving(job_id)
    try:
        offset = int(request.query_params.get("offset", ""))
    except ValueError as exc:
        raise HTTPException(400, "Missing chunk offset") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_CHUNK:
            raise HTTPException(413, "Upload chunk is too large")
    if not body:
        raise HTTPException(400, "Upload chunk is empty")
    # Re-check after the body arrived: a cancel, idle release or duplicate retry
    # may have run meanwhile. No await inside this block.
    with lock:
        directory, status = receiving(job_id)
        received = status["receivedBytes"]
        # Retried chunk that already arrived: acknowledge without writing twice.
        if offset + len(body) <= received:
            return {"received": received}
        if offset != received:
            raise HTTPException(409, f"Expected offset {received}")
        if received + len(body) > status["expectedBytes"]:
            raise HTTPException(413, "Upload is larger than the declared video size")
        # r+b never recreates a file that a release has deleted.
        with (directory / "video").open("r+b") as f:
            f.seek(received)
            f.write(body)
            f.truncate()
        status.update(
            receivedBytes=received + len(body),
            progress=round((received + len(body)) / status["expectedBytes"] * 100, 1),
        )
        write_status(directory, status)
        upload_activity[job_id] = time.monotonic()
        return {"received": status["receivedBytes"]}


@app.post("/jobs/{job_id}/start")
async def start(job_id: str):
    directory, status = receiving(job_id)
    if status["receivedBytes"] != status["expectedBytes"]:
        raise HTTPException(
            409, f"Upload incomplete: {status['receivedBytes']} of {status['expectedBytes']} bytes"
        )
    try:
        return await asyncio.to_thread(begin, directory, status, status["receivedBytes"])
    except ValueError as exc:
        release(job_id, directory, status, "Upload failed or video could not be decoded")
        raise HTTPException(400, str(exc)) from exc


@app.get("/jobs/{job_id}")
def status(job_id: str):
    sweep_stale()
    return public(load_status(folder(job_id)))


@app.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    directory = folder(job_id)
    with lock:
        if job_id in cancellations:
            cancellations[job_id].set()
            return {"requested": True}
        # Cancelling an unfinished chunked upload frees the worker immediately.
        if job_id == active and job_id in upload_activity:
            status = json.loads((directory / "status.json").read_text())
            release(job_id, directory, status, "Upload cancelled")
            return {"requested": True}
    return {"requested": False}


@app.get("/jobs/{job_id}/result")
def result(job_id: str):
    path = folder(job_id) / "result.json"
    if not path.is_file():
        raise HTTPException(409, "Analysis is not complete")
    return FileResponse(path, media_type="application/json", filename="pitchlens-vision.json")


@app.get("/models/{name}")
def model_file(name: str):
    """Lets the GPU worker copy installed weights it could not download itself."""
    from app.vision.profiles import model_paths

    for profile in ("general", "broadcast"):
        for path in model_paths(profile):
            if path.name == name and path.is_file():
                return FileResponse(path, media_type="application/octet-stream")
    raise HTTPException(404, "Model not installed")


@app.get("/jobs/{job_id}/video")
def video(job_id: str, request: Request):
    directory = folder(job_id)
    path = directory / "video"
    if not path.is_file() or job_id == active and job_id in upload_activity:
        raise HTTPException(404, "Video is unavailable")
    data = json.loads((directory / "status.json").read_text())
    if expired(data):
        path.unlink(missing_ok=True)
        data["videoDeleted"] = True
        write_status(directory, data)
        raise HTTPException(404, "Video is unavailable")
    size = path.stat().st_size
    start = 0
    end = size - 1
    code = 200
    value = request.headers.get("range")
    if value:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if not match or not any(match.groups()):
            raise HTTPException(416, "Invalid range", headers={"Content-Range": f"bytes */{size}"})
        left, right = match.groups()
        if left:
            start = int(left)
            end = min(end, int(right)) if right else min(end, start + RANGE_CAP - 1)
        else:
            start = max(0, size - int(right))
        if start > end or start >= size:
            raise HTTPException(416, "Invalid range", headers={"Content-Range": f"bytes */{size}"})
        code = 206

    def stream():
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
    }
    if code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    # Browser decodes the actual container; MP4 is the supported reference-video format.
    return StreamingResponse(stream(), status_code=code, headers=headers, media_type="video/mp4")


# ------------------------------------------------------------------ analytics


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")))
    temporary.replace(path)


async def _json_body(request: Request):
    body = await request.body()
    if len(body) > MAX_JSON_BODY:
        raise HTTPException(413, "Request is too large")
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object")
    return data


def _finished_result(job_id):
    directory = folder(job_id)
    path = directory / "result.json"
    if not path.is_file():
        raise HTTPException(409, "Analysis is not complete")
    return directory, path


def _calibration_ready(directory):
    data = _read_json(directory / "calibration.json")
    return data if data and data.get("state") == "ready" else None


def compute_analysis(directory):
    """analysis.json from result + calibration + review; cached until an input changes."""
    from app.vision.analytics import analyse

    inputs = [directory / "result.json", directory / "calibration.json", directory / "review.json"]
    stamp = [p.stat().st_mtime_ns if p.is_file() else 0 for p in inputs]
    cached = _read_json(directory / "analysis.json")
    if cached and cached.get("inputs") == stamp:
        return cached
    with post_lock:
        result = json.loads(inputs[0].read_text())
        analysis = analyse(result, _calibration_ready(directory), _read_json(inputs[2]))
        analysis["inputs"] = stamp
        _write_json(directory / "analysis.json", analysis)
    return analysis


@app.get("/jobs/{job_id}/analysis")
def analysis(job_id: str):
    directory, _ = _finished_result(job_id)
    try:
        return compute_analysis(directory)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"Analytics could not be computed: {exc}") from exc


@app.get("/jobs/{job_id}/calibration")
def calibration(job_id: str):
    """The saved calibration (per-frame homographies) plus any run in progress."""
    directory = folder(job_id)
    data = _calibration_ready(directory) or {"state": "none"}
    job = _read_json(directory / "calibration-job.json")
    if job:
        data = {**data, "job": job}
    return data


@app.post("/jobs/{job_id}/calibration/preview")
async def calibration_preview(job_id: str, request: Request):
    from app.vision.calibrate import preview

    _, path = _finished_result(job_id)
    body = await _json_body(request)
    result = await asyncio.to_thread(lambda: json.loads(path.read_text()))
    try:
        return await asyncio.to_thread(preview, result, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _run_calibration(directory, body):
    from app.vision.calibrate import build

    status_path = directory / "calibration-job.json"

    def progress(fraction):
        current = _read_json(status_path) or {}
        if current.get("state") == "processing":
            current["progress"] = round(min(0.99, fraction) * 100, 1)
            _write_json(status_path, current)

    try:
        result = json.loads((directory / "result.json").read_text())
        video = directory / "video"
        data = build(result, body, video if video.is_file() else None, progress)
        # The previous calibration stays in force until this one is complete.
        _write_json(directory / "calibration.json", data)
        compute_analysis(directory)
        _write_json(status_path, {"state": "done", "progress": 100, "finishedAt": time.time()})
    except Exception as exc:  # noqa: BLE001 - reported to the user; previous calibration kept
        _write_json(status_path, {"state": "failed", "error": str(exc)[:300], "finishedAt": time.time()})


@app.post("/jobs/{job_id}/calibration")
async def save_calibration(job_id: str, request: Request):
    from app.vision.calibrate import preview

    directory, path = _finished_result(job_id)
    body = await _json_body(request)
    running = _read_json(directory / "calibration-job.json") or {}
    if running.get("state") == "processing" and time.time() - running.get("startedAt", 0) < 1800:
        raise HTTPException(409, "A calibration for this match is already being applied")
    result = await asyncio.to_thread(lambda: json.loads(path.read_text()))
    try:
        fit = await asyncio.to_thread(preview, result, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if fit["fit"]["quality"] == "poor":
        raise HTTPException(
            400,
            f"These clicks do not fit a flat pitch (error {fit['fit']['rmsPixels']:.1f} px). "
            "Check each landmark is the right one and the pitch size.",
        )
    _write_json(
        directory / "calibration-job.json",
        {"state": "processing", "progress": 0, "startedAt": time.time(), "fit": fit["fit"]},
    )
    post_pool.submit(_run_calibration, directory, body)
    return {"state": "processing", **fit}


REVIEW_ACTIONS = {"accept", "reject", "reset", "team", "type", "outcome", "add", "direction", "score"}
EVENT_TYPES = {"pass", "shot", "goal", "goal-candidate", "interception", "tackle", "out", "save", "foul", "note"}


def _clean_decision(d, index):
    if not isinstance(d, dict) or d.get("action") not in REVIEW_ACTIONS:
        raise HTTPException(400, "Unknown review action")
    out = {"action": d["action"], "at": round(time.time(), 3)}
    if d["action"] == "add":
        if d.get("type") not in EVENT_TYPES:
            raise HTTPException(400, "Unknown event type")
        t = float(d.get("t", -1))
        if not (0 <= t <= 6 * 3600):
            raise HTTPException(400, "Invalid event time")
        out.update(type=d["type"], t=round(t, 2), id=f"added-{uuid.uuid4().hex[:10]}")
        if d.get("team") in (0, 1):
            out["team"] = d["team"]
        if isinstance(d.get("outcome"), str):
            out["outcome"] = d["outcome"][:40]
        for key in ("x", "y"):
            if isinstance(d.get(key), (int, float)) and abs(d[key]) < 200:
                out[key] = float(d[key])
        return out
    if d["action"] == "direction":
        if d.get("value") not in ("left", "right"):
            raise HTTPException(400, "Direction must be left or right")
        out["value"] = d["value"]
        return out
    if d["action"] == "score":
        value = d.get("value")
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 99 for v in value)
        ):
            raise HTTPException(400, "Score must be two whole numbers from 0 to 99")
        out["value"] = value
        return out
    event = d.get("eventId")
    if not isinstance(event, str) or not re.fullmatch(r"(ev|added)-[a-z0-9-]{1,40}", event):
        raise HTTPException(400, "Unknown event")
    out["eventId"] = event
    if d["action"] == "team":
        if d.get("value") not in (0, 1):
            raise HTTPException(400, "Team must be 0 or 1")
        out["value"] = d["value"]
    elif d["action"] in ("type", "outcome"):
        allowed = EVENT_TYPES if d["action"] == "type" else None
        value = d.get("value")
        if not isinstance(value, str) or (allowed and value not in allowed):
            raise HTTPException(400, "Invalid value")
        out["value"] = value[:40]
    return out


@app.get("/jobs/{job_id}/review")
def review(job_id: str):
    directory = folder(job_id)
    return _read_json(directory / "review.json", {"decisions": []})


@app.post("/jobs/{job_id}/review")
async def add_review(job_id: str, request: Request):
    directory, _ = _finished_result(job_id)
    body = await _json_body(request)
    decisions = body.get("decisions")
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= 200:
        raise HTTPException(400, "Send between 1 and 200 decisions")
    cleaned = [_clean_decision(d, i) for i, d in enumerate(decisions)]
    with post_lock:
        # Append-only: the model's output and every change stay auditable.
        current = _read_json(directory / "review.json", {"decisions": []})
        if len(current["decisions"]) + len(cleaned) > 20000:
            raise HTTPException(409, "Too many review decisions for one match")
        current["decisions"].extend(cleaned)
        _write_json(directory / "review.json", current)
    data = await asyncio.to_thread(compute_analysis, directory)
    return {"decisions": len(current["decisions"]), "added": [c for c in cleaned if c["action"] == "add"], "analysis": data}

