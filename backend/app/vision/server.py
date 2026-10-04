"""Authenticated vision worker. Runs on loopback locally or as a hosted container.

Models run locally or on the configured Modal GPU; uploaded footage stays private.
The long-lived service token stays server-side (Next.js proxy -> worker).
"""

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.vision import billing, gpu
from app.vision.access import valid_video_grant
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
calibrating = set()  # job ids with a calibration queued or running in this process
calibrating_lock = threading.Lock()
MAX_JSON_BODY = 256 * 1024


def auth(request: Request):
    # Platform health checks (Railway, Docker) carry no credentials and learn nothing.
    if request.url.path == "/healthz":
        return
    if not TOKEN or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), f"Bearer {TOKEN}".encode()
    ):
        raise HTTPException(401, "Vision service authentication required")
    job_id = request.path_params.get("job_id")
    if job_id:
        worker_read = (
            request.method == "GET"
            and request.url.path == f"/jobs/{job_id}/video"
            and valid_video_grant(TOKEN, job_id, request.headers.get("x-pitchlens-video-grant"))
        )
        if not worker_read:
            authorize_job(job_id, request)


def require_owner(request):
    return (
        os.getenv("VISION_REQUIRE_OWNER", "0") == "1"
        or bool(gpu.public_base())
        or request.headers.get("x-pitchlens-require-owner") == "1"
    )


def authorize_job(job_id, request):
    directory = folder(job_id)
    data = json.loads((directory / "status.json").read_text())
    expected = data.get("owner")
    given = request.headers.get("x-pitchlens-owner", "")
    # Legacy ownerless data remains local-only; it is never public by UUID.
    if (expected and (not OWNER.fullmatch(given) or not hmac.compare_digest(expected, given))) or (
        not expected and require_owner(request)
    ):
        raise HTTPException(404, "Job not found")
    return directory


def allowed_hosts():
    value = os.getenv("VISION_ALLOWED_HOSTS", "")
    hosts = [h.strip() for h in value.split(",") if h.strip()]
    return hosts or ["127.0.0.1", "localhost", "testserver"]


app = FastAPI(
    title="Pitchlens vision worker", docs_url=None, redoc_url=None, dependencies=[Depends(auth)]
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
app.include_router(billing.router(lambda: ROOT))


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
    return {k: v for k, v in data.items() if k not in (
        "owner", "uploadRequestId", "uploadRequestDigest", "rerunRequestId", "rerunDigest"
    )}


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

                if gpu.public_base() and not status.get("maxSeconds") and os.getenv("VISION_ALLOW_CPU_FALLBACK", "0") != "1":
                    raise ValueError("GPU is unavailable. Your upload is saved; retry when GPU service returns.") from exc
                logging.warning("GPU unavailable, using CPU: %s", exc)
                update(stage="GPU unavailable; analysing on the CPU (slower)", progress=0)
        if not ran_on_gpu:
            if gpu.public_base() and not status.get("maxSeconds") and os.getenv("VISION_ALLOW_CPU_FALLBACK", "0") != "1":
                raise ValueError("GPU processing is not configured. Your upload is saved; contact the service owner.")
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


def recover_calibrations():
    """A calibration running when the worker stopped will never finish: say so."""
    for status in ROOT.glob("*/calibration-job.json"):
        data = _read_json(status) or {}
        if data.get("state") == "processing":
            _write_json(status, {"state": "failed", "error": "The analysis server restarted. Apply the pitch setup again.", "finishedAt": time.time()})


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
app.add_event_handler("startup", recover_calibrations)
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
        "gpu": gpu.modal_enabled() or os.getenv("VISION_DEVICE", "").startswith(("cuda", "mps")),
        # Pitch calibration, event detection and reviewer decisions.
        "analytics": True,
        "privateJobs": True,
        "reviewIdempotency": True,
        "savedVideoReruns": True,
    }


@app.get("/jobs")
def jobs(request: Request):
    owner = request.headers.get("x-pitchlens-owner") or request.query_params.get("owner")
    if require_owner(request) and not owner:
        raise HTTPException(400, "Match ownership is required")
    if owner is not None and not OWNER.fullmatch(owner):
        raise HTTPException(400, "Invalid owner")
    sweep_stale()
    entries = []
    for p in ROOT.glob("*/status.json"):
        try:
            data = load_status(p.parent)
        except (OSError, ValueError, KeyError):
            continue
        # Ownerless legacy jobs are visible only to a local, unscoped listing.
        if owner is not None and data.get("owner") != owner:
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
    owner = request.headers.get("x-pitchlens-owner") or request.query_params.get("owner")
    if require_owner(request) and not owner:
        raise HTTPException(400, "Match ownership is required")
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
    request_id = request.query_params.get("requestId")
    if request_id is not None and (
        not re.fullmatch(r"[a-f0-9-]{32,36}", request_id) or not owner or expected is None
    ):
        raise HTTPException(400, "A resumable upload needs a valid request ID, owner and video size")
    title = request.query_params.get("title", "Football match")[:200]
    digest = hashlib.sha256(json.dumps(
        [expected, title, profile, sample_fps, diagnostic, start_seconds, ball_search]
    ).encode()).hexdigest()
    await asyncio.to_thread(delete_expired)
    job_id = uuid.uuid4().hex
    with lock:
        release_stale_upload()
        if request_id:
            # The first response may have been lost after reserving the worker.
            # Persisted IDs also prevent a restart from silently creating a second job.
            for path in ROOT.glob("*/status.json"):
                previous = _read_json(path, {})
                if not isinstance(previous, dict):
                    continue
                if previous.get("owner") != owner or previous.get("uploadRequestId") != request_id:
                    continue
                if previous.get("uploadRequestDigest") != digest:
                    raise HTTPException(409, "Upload request ID already used for different options")
                if previous.get("status") == "uploading" and previous.get("id") == active:
                    return public(previous)
                raise HTTPException(409, "The previous upload stopped. Start the upload again.")
        if active is not None:
            raise HTTPException(
                409, "Another video is being analysed. Wait for it to finish or cancel it."
            )
        active = job_id
    upload_activity[job_id] = upload_started[job_id] = time.monotonic()
    directory = ROOT / job_id
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
    if request_id:
        status.update(uploadRequestId=request_id, uploadRequestDigest=digest)
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
    owner = request.headers.get("x-pitchlens-owner") or request.query_params.get("owner")
    if require_owner(request) and not owner:
        raise HTTPException(400, "Match ownership is required")
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
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_JSON_BODY:
        raise HTTPException(413, "Request is too large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_JSON_BODY:
            raise HTTPException(413, "Request is too large")
    def reject_constant(value):
        raise ValueError(f"{value} is not allowed")

    try:
        # NaN/Infinity are not JSON; refusing them keeps stored files valid.
        data = json.loads(body or b"{}", parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
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


# Bump when analytics logic changes so cached analyses are recomputed on deploy.
ANALYTICS_VERSION = 6
_prepared = OrderedDict()  # (job, result stamp, calibration stamp, direction) -> prepared analysis
PREPARED_CACHE = 3


def _stamp(path):
    return [path.stat().st_mtime_ns, path.stat().st_size] if path.is_file() else 0


def compute_analysis(directory, include_positions=True):
    """analysis.json from result + calibration + review; cached until an input changes.

    The expensive stage (projection, tracks, possession, events) is kept in
    memory per match, so a review decision only re-applies decisions and
    re-summarises: a keypress, not a full re-analysis.
    """
    from app.vision.analytics import direction_override, finish, prepare

    inputs = [directory / "result.json", directory / "calibration.json", directory / "review.json"]

    def stamp():
        return [ANALYTICS_VERSION] + [_stamp(p) for p in inputs]

    cached = _read_json(directory / "analysis.json")
    if cached and cached.get("inputs") == stamp():
        return cached
    with post_lock:
        # Another request may have computed it while we waited.
        cached = _read_json(directory / "analysis.json")
        if cached and cached.get("inputs") == stamp():
            return cached
        key = stamp()
        review = _read_json(inputs[2])
        base_key = (directory.name, ANALYTICS_VERSION, json.dumps(key[1:3]), direction_override(review))
        base = _prepared.get(base_key)
        if base is None:
            result = json.loads(inputs[0].read_text())
            base = prepare(result, _calibration_ready(directory), direction_override(review))
            _prepared[base_key] = base
            while len(_prepared) > PREPARED_CACHE:
                _prepared.popitem(last=False)
        else:
            _prepared.move_to_end(base_key)
        analysis = finish(base, review)
        analysis["inputs"] = key
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
def calibration(job_id: str, request: Request):
    """The saved calibration (per-frame homographies) plus any run in progress.

    ?frames=0 leaves out the per-frame homographies (for polling a running job).
    """
    directory = folder(job_id)
    data = _calibration_ready(directory) or {"state": "none"}
    if request.query_params.get("frames") == "0":
        data = {k: v for k, v in data.items() if k != "frames"}
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
    finally:
        with calibrating_lock:
            calibrating.discard(directory.name)


def _run_venue_calibration(directory, venue):
    from app.vision.calibrate import build_from_venue

    status_path = directory / "calibration-job.json"
    try:
        result = json.loads((directory / "result.json").read_text())
        video = directory / "video"
        data = build_from_venue(result, venue, video if video.is_file() else None)
        _write_json(directory / "calibration.json", data)
        compute_analysis(directory)
        _write_json(status_path, {"state": "done", "progress": 100, "finishedAt": time.time(), "venue": venue.get("id")})
    except Exception as exc:  # noqa: BLE001 - reported to the user; previous calibration kept
        _write_json(status_path, {"state": "failed", "error": str(exc)[:300], "finishedAt": time.time()})
    finally:
        with calibrating_lock:
            calibrating.discard(directory.name)


@app.post("/jobs/{job_id}/calibration")
async def save_calibration(job_id: str, request: Request):
    directory, path = _finished_result(job_id)
    body = await _json_body(request)
    body.pop("_owner", None)
    if "venue" in body:
        body["_owner"] = _owner(request)
    # In-memory, so a calibration interrupted by a restart never blocks a retry.
    with calibrating_lock:
        if job_id in calibrating:
            raise HTTPException(409, "A calibration for this match is already being applied")
        calibrating.add(job_id)
    try:
        return await _start_calibration(job_id, directory, path, body)
    except BaseException:
        with calibrating_lock:
            calibrating.discard(job_id)
        raise


async def _start_calibration(job_id, directory, path, body):
    from app.vision.calibrate import preview

    if "venue" in body:
        venue_id = body.get("venue")
        if not isinstance(venue_id, str) or not re.fullmatch(r"[a-f0-9]{32}", venue_id):
            raise HTTPException(404, "Venue not found")
        venue = _read_json(_venue_dir() / f"{venue_id}.json")
        if not venue or venue.get("owner", "") != body.get("_owner", ""):
            raise HTTPException(404, "Venue not found")
        _write_json(directory / "calibration-job.json", {"state": "processing", "progress": 0, "startedAt": time.time(), "venue": venue_id})
        post_pool.submit(_run_venue_calibration, directory, venue)
        return {"state": "processing", "venue": {"id": venue_id, "name": venue.get("name")}}

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
    if not isinstance(d, dict) or not isinstance(d.get("action"), str) or d["action"] not in REVIEW_ACTIONS:
        raise HTTPException(400, "Unknown review action")
    out = {"action": d["action"], "at": round(time.time(), 3)}
    if d["action"] == "add":
        if not isinstance(d.get("type"), str) or d["type"] not in EVENT_TYPES:
            raise HTTPException(400, "Unknown event type")
        t = d.get("t", -1)
        # Range first: math.isfinite overflows on a huge JSON integer.
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not (0 <= t <= 6 * 3600) or not math.isfinite(t):
            raise HTTPException(400, "Invalid event time")
        t = float(t)
        out.update(type=d["type"], t=round(t, 2), id=f"added-{uuid.uuid4().hex[:10]}")
        if isinstance(d.get("team"), int) and not isinstance(d.get("team"), bool) and d["team"] in (0, 1):
            out["team"] = d["team"]
        if isinstance(d.get("outcome"), str):
            out["outcome"] = d["outcome"][:40]
        x, y = d.get("x"), d.get("y")
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 200 and math.isfinite(v) for v in (x, y)):
            out["x"], out["y"] = float(x), float(y)
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
    if not isinstance(event, str) or not re.fullmatch(r"(ev|added|kept-ev|kept-added)-[a-z0-9-]{1,60}", event):
        raise HTTPException(400, "Unknown event")
    out["eventId"] = event
    # The moment the reviewer was looking at (their list may be older than ours).
    seen = d.get("event")
    if isinstance(seen, dict):
        t = seen.get("t")
        if isinstance(seen.get("type"), str) and seen["type"] in EVENT_TYPES and isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= 6 * 3600 and math.isfinite(t):
            fingerprint = {"type": seen["type"], "t": round(float(t), 2)}
            if isinstance(seen.get("team"), int) and not isinstance(seen.get("team"), bool) and seen["team"] in (0, 1):
                fingerprint["team"] = seen["team"]
            if isinstance(seen.get("outcome"), str):
                fingerprint["outcome"] = seen["outcome"][:40]
            out["event"] = fingerprint
    if d["action"] == "team":
        if isinstance(d.get("value"), bool) or d.get("value") not in (0, 1):
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
    request_id = body.get("requestId")
    revision = body.get("expectedRevision")
    if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9-]{32,36}", request_id)):
        raise HTTPException(400, "Invalid review request ID")
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise HTTPException(400, "Invalid review revision")
    decisions = body.get("decisions")
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= 200:
        raise HTTPException(400, "Send between 1 and 200 decisions")
    digest = hashlib.sha256(json.dumps(decisions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cleaned = [_clean_decision(d, i) for i, d in enumerate(decisions)]
    # Record which moment each decision was made on, so it survives re-analysis
    # (event ids are positions and change when the analysis is recomputed).
    current = await asyncio.to_thread(compute_analysis, directory)
    by_id = {e["id"]: e for e in current.get("events", [])}
    for d in cleaned:
        event = by_id.get(d.get("eventId"))
        # Prefer what the reviewer saw; fall back to our current list (older clients).
        if event is not None and "event" not in d:
            d["event"] = {k: event.get(k) for k in ("type", "t", "team", "outcome")}
    def append():
        with post_lock:
            # Append-only: the model's output and every change stay auditable.
            log = _read_json(directory / "review.json", {"decisions": []})
            requests = log.setdefault("requests", {})
            if request_id in requests:
                previous = requests[request_id]
                if previous["digest"] != digest:
                    raise HTTPException(409, "This request ID was already used for different decisions")
                return len(log["decisions"]), previous["added"]
            if revision is not None and revision != len(log["decisions"]):
                raise HTTPException(409, "This match was reviewed in another tab. Reload before saving.")
            if len(log["decisions"]) + len(cleaned) > 20000:
                raise HTTPException(409, "Too many review decisions for one match")
            added = [c for c in cleaned if c["action"] == "add"]
            log["decisions"].extend(cleaned)
            if request_id:
                requests[request_id] = {"digest": digest, "added": added}
            _write_json(directory / "review.json", log)
            return len(log["decisions"]), added

    # The lock is taken in a worker thread: blocking the event loop would stall
    # uploads and every other request while an analysis is being computed.
    total, added = await asyncio.to_thread(append)
    data = await asyncio.to_thread(compute_analysis, directory)
    # The per-frame positions do not change with a decision; the client keeps its copy.
    data = {k: v for k, v in data.items() if k != "positions"}
    return {"decisions": total, "added": added, "analysis": data}


# ------------------------------------------------------------------ venues

VENUES = "venues"


def _venue_dir():
    directory = ROOT / VENUES
    directory.mkdir(exist_ok=True)
    return directory


def _owner(request):
    owner = request.headers.get("x-pitchlens-owner") or request.query_params.get("owner", "")
    if require_owner(request) and not OWNER.fullmatch(owner):
        raise HTTPException(400, "Match ownership is required")
    return owner if OWNER.fullmatch(owner) else ""


@app.get("/venues")
def venues(request: Request):
    """Venues saved from this browser (owner key); a local worker without keys sees all."""
    owner = _owner(request)
    out = []
    for path in sorted(_venue_dir().glob("*.json")):
        data = _read_json(path) or {}
        if data.get("id") and (data.get("owner", "") == owner):
            out.append({k: data.get(k) for k in ("id", "name", "template", "size", "static", "createdAt")})
    return out


@app.post("/venues")
async def create_venue(request: Request):
    from app.vision.calibrate import venue_from_calibration

    body = await _json_body(request)
    name = body.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise HTTPException(400, "Give the venue a name (up to 80 characters)")
    job_id = body.get("jobId")
    if not isinstance(job_id, str):
        raise HTTPException(400, "Unknown match")
    directory = authorize_job(job_id, request)
    calibration = _calibration_ready(directory)
    if not calibration:
        raise HTTPException(409, "Set up the pitch for this match first")
    try:
        venue = venue_from_calibration(calibration, name.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    owner = _owner(request)
    mine = [p for p in _venue_dir().glob("*.json") if (_read_json(p) or {}).get("owner", "") == owner]
    if len(mine) >= 50:
        raise HTTPException(409, "Too many saved venues")
    venue.update(id=uuid.uuid4().hex, createdAt=time.time(), sourceJob=job_id, owner=owner)
    _write_json(_venue_dir() / f"{venue['id']}.json", venue)
    return {k: venue[k] for k in ("id", "name", "template", "size", "static", "createdAt")}



@app.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    directory = folder(job_id)
    # Never remove files beneath a live upload, inference or calibration.
    with calibrating_lock, lock:
        if job_id == active or job_id in calibrating:
            raise HTTPException(409, "Cancel processing and wait for it to stop before deleting")
        with post_lock:
            shutil.rmtree(directory)
            for key in list(_prepared):
                if key[0] == job_id:
                    del _prepared[key]
            # Saved venues derived from this match are personal derived data too.
            for path in _venue_dir().glob("*.json"):
                if (_read_json(path) or {}).get("sourceJob") == job_id:
                    path.unlink(missing_ok=True)
    return {"deleted": True}


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    global active
    with lock:
        directory = folder(job_id)
        status = load_status(directory)
        # A lost retry response is safe: acknowledge the existing active attempt.
        if active == job_id and status["status"] == "processing":
            return public(status)
        if status["status"] not in ("failed", "interrupted", "cancelled"):
            raise HTTPException(409, "Only stopped analyses can be retried")
        if active is not None:
            raise HTTPException(409, "Another video is being analysed. Try again when it finishes.")
        if not (directory / "video").is_file() or expired(status) or not status.get("video"):
            raise HTTPException(409, "No complete upload is available. Upload the video again.")
        if status.get("attempts", 1) >= 3:
            raise HTTPException(409, "Retry limit reached. Contact support before another attempt.")
        if status.get("profile", "general") not in available_profiles():
            raise HTTPException(503, "The selected vision models are not installed")
        event = threading.Event()
        status.update(status="processing", stage="Retrying saved upload", progress=0, attempts=status.get("attempts", 1) + 1)
        write_status(directory, status)
        active = job_id
        cancellations[job_id] = event
        pool.submit(work, directory, status, event)
        return public(status)


@app.post("/jobs/{job_id}/rerun")
def rerun_job(job_id: str, request: Request):
    """Start a fresh report from private saved footage; preserve the original report."""
    global active
    mode = request.query_params.get("mode", "section")
    request_id = request.query_params.get("requestId", "")
    if mode not in ("section", "full") or not re.fullmatch(r"[a-f0-9-]{32,36}", request_id):
        raise HTTPException(400, "Choose a section or full match and a valid request ID")
    try:
        start = int(request.query_params.get("start", "0"))
    except ValueError as exc:
        raise HTTPException(400, "Test start must be a whole number of seconds") from exc
    digest = f"{mode}:{start}"
    with lock:
        source = folder(job_id)
        previous = load_status(source)
        # Owner authorization runs before this endpoint, including retry replies.
        for path in ROOT.glob("*/status.json"):
            existing = _read_json(path, {})
            if not isinstance(existing, dict):
                continue
            if existing.get("sourceJobId") == job_id and existing.get("rerunRequestId") == request_id:
                if existing.get("rerunDigest") != digest:
                    raise HTTPException(409, "Rerun request ID already used for different options")
                return public(load_status(path.parent))
        if previous["status"] not in ("completed", "failed", "interrupted", "cancelled"):
            raise HTTPException(409, "Wait for this analysis to finish before starting another")
        metadata = previous.get("video")
        if not metadata or not (source / "video").is_file() or expired(previous):
            raise HTTPException(409, "Saved footage is no longer available. Upload the video again.")
        if not 0 <= start < metadata["duration"] or (mode == "full" and start != 0):
            raise HTTPException(400, "Test start must be inside the video; full matches start at zero")
        if previous.get("profile", "general") not in available_profiles():
            raise HTTPException(503, "The selected vision models are not installed")
        release_stale_upload()
        if active is not None:
            raise HTTPException(409, "Another video is being analysed. Try again when it finishes.")
        new_id = uuid.uuid4().hex
        directory = ROOT / new_id
        status = {
            "id": new_id, "sourceJobId": job_id, "title": previous["title"],
            "createdAt": time.time(), "status": "processing", "stage": "Opening saved footage",
            "progress": 0, "video": metadata, "fileSize": (source / "video").stat().st_size,
            "profile": previous.get("profile", "general"), "sampleFps": previous.get("sampleFps", 3),
            "ballSearch": previous.get("ballSearch", "exhaustive"),
            "maxSeconds": 20 if mode == "section" else None, "startSeconds": start,
            "rerunRequestId": request_id, "rerunDigest": digest,
        }
        if previous.get("owner"):
            status["owner"] = previous["owner"]
        try:
            directory.mkdir()
            # Both jobs are read-only consumers; deleting either link preserves
            # the other. This avoids another upload and another 500 MB disk copy.
            try:
                os.link(source / "video", directory / "video")
            except OSError:
                check_space(status["fileSize"])
                shutil.copyfile(source / "video", directory / "video")
            write_status(directory, status)
            active = new_id
            event = threading.Event()
            cancellations[new_id] = event
            pool.submit(work, directory, status, event)
        except BaseException:
            if active == new_id:
                active = None
            cancellations.pop(new_id, None)
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return public(status)
