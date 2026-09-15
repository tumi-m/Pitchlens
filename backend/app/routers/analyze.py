"""Cloud match analysis hosted on Railway."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _job(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown analysis job")
    return job


@router.get("/cv-status")
@router.get("/analyze/status")
async def cv_status():
    ultralytics = False
    try:
        import ultralytics  # noqa: F401
        ultralytics = True
    except Exception:
        pass
    return {
        "cloud": True,
        "enabled": os.getenv("ENABLE_CV", "true") == "true",
        "ultralytics": ultralytics,
        "roboflowConfigured": bool(os.getenv("ROBOFLOW_API_KEY")),
        "weights": os.getenv("YOLO_WEIGHTS", "yolov8n.pt"),
        "device": os.getenv("CV_DEVICE", "cpu"),
        "host": "railway",
    }


@router.post("/analyze")
@router.post("/analyze-local")
async def start_analyze(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    landmarks: str = Form(default=""),
    pitch_kind: str = Form(default="five-a-side"),
    home_defends_left: bool = Form(default=True),
):
    if os.getenv("ENABLE_CV", "true") != "true":
        raise HTTPException(status_code=503, detail="Cloud CV is disabled")
    if not video.filename:
        raise HTTPException(status_code=400, detail="Missing video filename")
    suffix = Path(video.filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".avi"}:
        raise HTTPException(status_code=400, detail="Unsupported video type")
    parsed_landmarks = None
    if landmarks.strip():
        try:
            parsed_landmarks = json.loads(landmarks)
            if not isinstance(parsed_landmarks, list):
                raise ValueError("landmarks must be a list")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid landmarks: {exc}") from exc
    data = await video.read()
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Video empty or over upload cap")
    tmp = Path(tempfile.mkdtemp(prefix="pitchlens_"))
    path = tmp / f"upload{suffix}"
    path.write_bytes(data)
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"id": job_id, "status": "queued", "progress": 0, "message": "Queued on Railway", "result": None, "error": None}
    background_tasks.add_task(_run_job, job_id, str(path), parsed_landmarks, pitch_kind, home_defends_left, str(tmp))
    return {"jobId": job_id, "status": "queued"}


@router.get("/analyze/{job_id}")
async def get_analyze(job_id: str):
    return _job(job_id)


def _run_job(job_id, video_path, landmarks, pitch_kind, home_defends_left, tmp_dir):
    def progress(pct, msg):
        with _lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "running"
                job["progress"] = pct
                job["message"] = msg
    try:
        from app.engine.config import EngineConfig, PitchSpec
        from app.engine.pipeline import LocalCVPipeline
        cfg = EngineConfig()
        if pitch_kind == "eleven":
            cfg.pitch = PitchSpec.eleven()
        result = LocalCVPipeline(cfg, progress=progress).run(
            Path(video_path), landmarks=landmarks, home_defends_left=home_defends_left
        )
        with _lock:
            _jobs[job_id] = {"id": job_id, "status": "completed", "progress": 100, "message": "Analysis complete", "result": result, "error": None}
    except Exception as exc:
        logger.exception("Cloud analysis failed")
        with _lock:
            _jobs[job_id] = {"id": job_id, "status": "failed", "progress": 0, "message": "Analysis failed", "result": None, "error": str(exc)[:400]}
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
