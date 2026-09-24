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
        run_video(
            directory / "video",
            directory / "result.json",
            progress=update,
            cancelled=event.is_set,
            profile=status.get("profile", "general"),
            sample_fps=status.get("sampleFps", 3),
        )
        update(status="completed", stage="Analysis complete", progress=100)
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
        "maxBytes": MAX_BYTES,
        "maxChunk": MAX_CHUNK,
        "retentionHours": RETENTION_HOURS or None,
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
    if profile not in ("general", "broadcast"):
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
