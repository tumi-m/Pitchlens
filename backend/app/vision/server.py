"""Loopback-only local worker. Video never goes to a hosted inference provider."""

import asyncio
import hmac
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.vision.engine import probe, run_video

ROOT = Path(os.getenv("VISION_DATA_DIR", ".vision")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = os.getenv("VISION_SERVICE_TOKEN", "")
MAX_BYTES = 500 * 1024 * 1024
lock = threading.Lock()
active = None
cancellations = {}
pool = ThreadPoolExecutor(max_workers=1)


def auth(request: Request):
    if not TOKEN or not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {TOKEN}"
    ):
        raise HTTPException(401, "Local service authentication required")


app = FastAPI(
    title="Pitchlens local vision", docs_url=None, redoc_url=None, dependencies=[Depends(auth)]
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


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


def work(directory, status, event):
    global active

    def update(**kw):
        status.update(kw)
        write_status(directory, status)

    try:
        status["status"] = "processing"
        update(stage="Opening video", progress=0)
        run_video(
            directory / "video", directory / "result.json", progress=update, cancelled=event.is_set
        )
        update(status="completed", stage="Analysis complete", progress=100)
    except InterruptedError:
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
            active = None
            cancellations.pop(status["id"], None)


def stop_jobs():
    for event in list(cancellations.values()):
        event.set()


app.add_event_handler("shutdown", stop_jobs)


@app.get("/health")
def health():
    return {
        "available": Path(os.getenv("VISION_MODEL_PATH", "models/yolo11s.pt")).is_file()
        and Path(os.getenv("VISION_BALL_MODEL_PATH", "models/football-ball.onnx")).is_file(),
        "activeJob": active,
        "mode": "local-computer-vision",
    }


@app.get("/jobs")
def jobs():
    entries = [load_status(p.parent) for p in ROOT.glob("*/status.json")]
    return sorted(entries, key=lambda x: x["createdAt"], reverse=True)[:100]


@app.post("/jobs")
async def create(request: Request):
    global active
    if request.headers.get("content-type", "").split(";")[0] not in (
        "video/mp4",
        "application/octet-stream",
    ):
        raise HTTPException(415, "Upload a video file")
    if not health()["available"]:
        raise HTTPException(503, "Vision models are not installed")
    job_id = uuid.uuid4().hex
    with lock:
        if active is not None:
            raise HTTPException(
                409, "Another video is being analysed. Wait for it to finish or cancel it."
            )
        active = job_id
    directory = ROOT / job_id
    directory.mkdir()
    title = request.query_params.get("title", "Football match")[:200]
    status = {
        "id": job_id,
        "title": title,
        "createdAt": time.time(),
        "status": "uploading",
        "stage": "Receiving video",
        "progress": 0,
    }
    write_status(directory, status)
    try:
        size = 0
        with (directory / "video").open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Maximum video size is 500 MB")
                f.write(chunk)
        if not size:
            raise HTTPException(400, "Video file is empty")
        metadata = await asyncio.to_thread(probe, directory / "video")
        status.update(video=metadata, fileSize=size, status="processing")
        write_status(directory, status)
        event = threading.Event()
        cancellations[job_id] = event
        pool.submit(work, directory, status, event)
        return status
    except BaseException as exc:
        with lock:
            active = None
        status.update(status="failed", stage="Upload failed or video could not be decoded")
        write_status(directory, status)
        (directory / "video").unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(400, str(exc)) from exc
        raise


@app.get("/jobs/{job_id}")
def status(job_id: str):
    return load_status(folder(job_id))


@app.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    folder(job_id)
    if job_id in cancellations:
        cancellations[job_id].set()
    return {"requested": job_id in cancellations}


@app.get("/jobs/{job_id}/result")
def result(job_id: str):
    path = folder(job_id) / "result.json"
    if not path.is_file():
        raise HTTPException(409, "Analysis is not complete")
    return FileResponse(path, media_type="application/json", filename="pitchlens-vision.json")


@app.get("/jobs/{job_id}/video")
def video(job_id: str, request: Request):
    path = folder(job_id) / "video"
    if not path.is_file():
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
            end = min(end, int(right)) if right else end
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
