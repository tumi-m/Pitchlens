"""Local file analysis — no Firebase, no signed GCS URL."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_UPLOAD = 500 * 1024 * 1024


@router.post("/analyze-local")
async def analyze_local(
    video: UploadFile = File(...),
    landmarks: str = Form(default=""),
    pitch_kind: str = Form(default="five-a-side"),
    home_defends_left: bool = Form(default=True),
):
    if os.getenv("ENABLE_LOCAL_CV", "true") != "true":
        raise HTTPException(status_code=503, detail="Local CV is disabled")
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
        raise HTTPException(status_code=413, detail="Video empty or over 500 MB")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        try:
            from app.engine.config import EngineConfig, PitchSpec
            from app.engine.pipeline import LocalCVPipeline

            cfg = EngineConfig()
            if pitch_kind == "eleven":
                cfg.pitch = PitchSpec.eleven()
            result = LocalCVPipeline(cfg).run(path, landmarks=parsed_landmarks, home_defends_left=home_defends_left)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            logger.exception("Local CV failed")
            raise HTTPException(status_code=500, detail="Local analysis failed. Check YOLO_WEIGHTS / ultralytics install.")
    return JSONResponse(result)


@router.get("/cv-status")
async def cv_status():
    ultralytics = False
    try:
        import ultralytics  # noqa: F401
        ultralytics = True
    except Exception:
        pass
    return {
        "localCvEnabled": os.getenv("ENABLE_LOCAL_CV", "true") == "true",
        "ultralytics": ultralytics,
        "roboflowConfigured": bool(os.getenv("ROBOFLOW_API_KEY")),
        "weights": os.getenv("YOLO_WEIGHTS", "yolov8n.pt"),
        "device": os.getenv("CV_DEVICE", "cpu"),
    }
