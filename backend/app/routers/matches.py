"""
Match processing router — POST /process-match
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, HTTPException, Security, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.models.match import ProcessMatchRequest, ProcessMatchResponse, MatchStatus
from app.services.pipeline import MatchPipeline
from app.services.firestore_client import (
    update_match_status,
    write_match_analytics,
    append_audit_log,
)
import os

router = APIRouter()
logger = logging.getLogger(__name__)
security = HTTPBearer()

API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

# Thread pool for CPU/IO-intensive pipeline work
_executor = ThreadPoolExecutor(max_workers=int(os.getenv("PIPELINE_WORKERS", "2")))


def _verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    if not API_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Server misconfiguration: missing API_SECRET_KEY")
    if credentials.credentials != API_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return credentials.credentials


def _run_pipeline(match_id: str, video_url: str, team_colors=None, user_id: str = "") -> None:
    """
    Blocking pipeline execution — runs in thread pool.
    Updates Firestore at each stage via callbacks and on completion/error.
    """

    def progress_cb(pct: int, message: str) -> None:
        try:
            update_match_status(
                match_id,
                MatchStatus.PROCESSING,
                progress=pct,
            )
            logger.info(f"[{match_id}] {pct}% — {message}")
        except Exception as e:
            logger.warning(f"Progress update failed: {e}")

    try:
        pipeline = MatchPipeline(progress_callback=progress_cb)
        analytics = pipeline.run(match_id, video_url, team_colors)

        write_match_analytics(match_id, analytics)

        if user_id:
            append_audit_log(match_id, user_id, "pipeline_completed", {
                "score": analytics.score.model_dump(),
                "xG_home": analytics.shots.home.xG,
                "xG_away": analytics.shots.away.xG,
            })

        logger.info(f"Pipeline succeeded for match {match_id}")

    except Exception as exc:
        logger.exception(f"Pipeline failed for match {match_id}: {exc}")
        try:
            update_match_status(
                match_id,
                MatchStatus.ERROR,
                error_message=f"Processing failed: {str(exc)[:200]}",
            )
            if user_id:
                append_audit_log(match_id, user_id, "pipeline_failed", {"error": str(exc)[:200]})
        except Exception as inner:
            logger.error(f"Failed to update error status: {inner}")


@router.post("/process-match", response_model=ProcessMatchResponse)
async def process_match(
    request: ProcessMatchRequest,
    background_tasks: BackgroundTasks,
    token: str = Security(_verify_token),
):
    """
    Accepts a match processing request, immediately queues it, and returns.
    The pipeline runs asynchronously — Firestore is updated on completion.
    """
    logger.info(f"Received process-match request for matchId={request.matchId}")

    # Update status to processing immediately
    try:
        update_match_status(request.matchId, MatchStatus.PROCESSING, progress=0)
    except Exception as e:
        logger.error(f"Failed to update Firestore before queuing: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialise match processing")

    # Queue background work
    loop = asyncio.get_event_loop()
    fn = partial(
        _run_pipeline,
        request.matchId,
        request.videoUrl,
        request.teamColors,
        request.userId or "",
    )
    loop.run_in_executor(_executor, fn)

    return ProcessMatchResponse(status="queued", message="Match queued for processing")


@router.get("/health")
async def health():
    return {"status": "ok", "service": "pitchlens-api"}
