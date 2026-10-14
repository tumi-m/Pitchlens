"""Authenticated vision worker. Runs on loopback locally or as a hosted container.

Models run locally or on the configured Modal GPU; uploaded footage stays private.
The long-lived service token stays server-side (Next.js proxy -> worker).
"""

import asyncio
import errno
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.vision import billing, gpu
from app.vision.access import valid_video_grant
from app.vision.engine import probe, run_video
from app.vision.profiles import available_profiles, player_load_profiles

# Operator log lines (job start and end, a low volume). uvicorn configures only
# its own loggers, so this one has its own handler. Never log owner keys or URLs.
ops_log = logging.getLogger("pitchlens")
if not ops_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    ops_log.addHandler(_handler)
ops_log.setLevel(logging.INFO)
ops_log.propagate = False


class RedactOwnerKeys(logging.Filter):
    """uvicorn's access log prints each path with its query string, and owner keys
    are capabilities for a browser's matches: never write one to the log."""

    pattern = re.compile(r"(?i)(owner=)[^&\s]*")

    def filter(self, record):
        # uvicorn's args: (client_addr, method, full_path, http_version, status_code).
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], self.pattern.sub(r"\1[redacted]", args[2]), *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(RedactOwnerKeys())

ROOT = Path(os.getenv("VISION_DATA_DIR", ".vision")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = os.getenv("VISION_SERVICE_TOKEN", "")
# Original recordings, not reduced copies: at 360p the ball is a few pixels. A
# 40-minute 1080p phone recording is 2-5 GB; size the volume to match
# (check_space refuses an upload that would not fit).
DEFAULT_MAX_BYTES = 2048 * 1024 * 1024


def _max_bytes():
    """Largest upload (and YouTube download) in bytes, default 2 GiB.

    VISION_MAX_BYTES (bytes) takes precedence over VISION_MAX_UPLOAD_MB (MiB,
    at least 50). A value that is not a positive whole number is ignored with a
    warning, so a typo cannot stop the worker from starting.
    """
    import logging

    value = os.getenv("VISION_MAX_BYTES", "").strip()
    if value:
        try:
            if int(value) > 0:
                return int(value)
        except ValueError:
            pass
        logging.warning("VISION_MAX_BYTES must be a positive whole number of bytes; using 2 GiB")
        return DEFAULT_MAX_BYTES
    value = os.getenv("VISION_MAX_UPLOAD_MB", "").strip()
    if value:
        try:
            if int(value) > 0:
                return max(50, int(value)) * 1024 * 1024
        except ValueError:
            pass
        logging.warning("VISION_MAX_UPLOAD_MB must be a positive whole number of MiB; using 2048")
    return DEFAULT_MAX_BYTES


MAX_BYTES = _max_bytes()


def max_size_text():
    """The upload limit in words ("2.0 GB", "500 MB")."""
    gb = MAX_BYTES / 1024**3
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = MAX_BYTES / 1024**2
    return f"{mb:.0f} MB" if mb >= 10 or mb == int(mb) else f"{mb:.1f} MB"


# Chunked uploads keep every request small enough for serverless proxies (Vercel: 4.5 MB).
MAX_CHUNK = 8 * 1024 * 1024
# A chunked upload that stops sending data gives way after this long once another
# owner's start wants the worker (contended).
UPLOAD_IDLE_SECONDS = int(os.getenv("VISION_UPLOAD_IDLE_SECONDS", "300"))
# Alone, a paused upload (a locked phone, another app) is kept this long for its
# browser to resume it; only then is it treated as abandoned and released.
UPLOAD_ABANDON_SECONDS = int(os.getenv("VISION_UPLOAD_ABANDON_SECONDS", "7200"))
# Even a trickling upload cannot hold the single worker longer than this once
# another start is waiting for it (a lone slow upload may finish). A large
# declared upload gets longer, at a 2 Mbit/s floor (2 GB: about 2.4 h), up to
# UPLOAD_SIZED_MAX_SECONDS.
UPLOAD_MAX_SECONDS = int(os.getenv("VISION_UPLOAD_MAX_SECONDS", "3600"))
UPLOAD_FLOOR_BYTES_PER_SECOND = 250_000
UPLOAD_SIZED_MAX_SECONDS = 4 * 3600
# Hosted deployments delete uploaded footage after this many hours (unset = keep forever).
RETENTION_HOURS = float(os.getenv("VISION_RETENTION_HOURS", "0") or 0)
# Open-ended playback ranges are capped so one response never streams a whole match.
RANGE_CAP = 16 * 1024 * 1024
OWNER = re.compile(r"[a-f0-9]{32}")
# Analyses that may start in any 24 hours (0 = no limit). With the site open to
# anyone holding the link, this bounds GPU spending; uploads are refused before
# any bytes are sent once it is reached.
STARTS = "starts.json"
FULL_RERUNS_PER_MATCH = 3
SECTION_RERUNS_PER_MATCH = 10


def daily_cap():
    try:
        return max(0, int(os.getenv("VISION_MAX_JOBS_PER_DAY", "30")))
    except ValueError:
        return 30


def _recent_starts(now):
    data = _read_json(ROOT / STARTS, [])
    return [t for t in data if isinstance(t, (int, float)) and 0 <= now - t < 86400] if isinstance(data, list) else []


class DailyLimit(Exception):
    """The 24-hour start cap: answered as JSON 429 with when a start frees up (see the handler below)."""

    def __init__(self, cap, resets_at):
        super().__init__(cap)
        self.cap, self.resets_at = cap, resets_at


class StorageFull(Exception):
    """No room on the volume: answered as JSON 507 with a code (see the handler below)."""


class FullMatchUnavailable(Exception):
    """A full match that this worker would only refuse later: answered as JSON 503 with a code."""


def check_full_match():
    # Before any bytes are read, the worker is reserved or a start is counted.
    if not full_match_ready():
        raise FullMatchUnavailable()


def check_daily_budget():
    cap = daily_cap()
    recent = sorted(_recent_starts(time.time()))
    if cap and len(recent) >= cap:
        # The window is rolling: a start frees up when enough of the oldest ones leave it.
        raise DailyLimit(cap, recent[len(recent) - cap] + 86400)


def record_start():
    """Count an analysis that has started (call while holding `lock`); returns its stamp."""
    now = time.time()
    stamp = round(now, 3)
    _write_json(ROOT / STARTS, _recent_starts(now) + [stamp])
    return stamp


def refund_start(stamp):
    """Give back a start that cost no compute (the GPU refused it): removes exactly that stamp."""
    if stamp is None:
        return
    with lock:
        data = _read_json(ROOT / STARTS, [])
        if isinstance(data, list) and stamp in data:
            data.remove(stamp)
            try:
                _write_json(ROOT / STARTS, data)
            except OSError:
                logging.exception("Could not give back the start at %s", stamp)


# Ball-model training started from the website, per 24 hours (0 = no limit):
# each run can hold a GPU for up to two hours. Operator runs are not counted.
TRAINING_STARTS = "training-starts.json"
# The most epochs the website may ask for (what its button sends).
SITE_TRAINING_EPOCHS = 60


def training_cap():
    try:
        return max(0, int(os.getenv("VISION_MAX_TRAINING_PER_DAY", "2")))
    except ValueError:
        return 2


def _recent_training_starts(now):
    data = _read_json(ROOT / TRAINING_STARTS, [])
    return [t for t in data if isinstance(t, (int, float)) and 0 <= now - t < 86400] if isinstance(data, list) else []


def check_training_budget():
    cap = training_cap()
    if cap and len(_recent_training_starts(time.time())) >= cap:
        raise HTTPException(429, "Ball-model training has reached today's limit.")


def record_training_start():
    now = time.time()
    stamp = round(now, 3)
    _write_json(ROOT / TRAINING_STARTS, _recent_training_starts(now) + [stamp])
    return stamp


def refund_training_start(stamp):
    """Give back a site training start that Modal refused (call while holding `training_lock`)."""
    data = _read_json(ROOT / TRAINING_STARTS, [])
    if isinstance(data, list) and stamp in data:
        data.remove(stamp)
        try:
            _write_json(ROOT / TRAINING_STARTS, data)
        except OSError:
            logging.exception("Could not give back the training start at %s", stamp)


# Re-entrant: stale-upload release runs while create() already holds the lock.
lock = threading.RLock()
active = None
stopping = False
cancellations = {}
upload_activity = {}
upload_started = {}
upload_sizes = {}  # job id -> size declared when a chunked upload reserved the worker
pool = ThreadPoolExecutor(max_workers=1)
# Analyses inside work(): job id -> (directory, status dict). stop_jobs records the
# shutdown in their status before cancelling them, so every status.json write for
# a running job happens under status_lock.
running = {}
status_lock = threading.Lock()
engines = {}  # job id -> "gpu" or "cpu" while it runs (GET /ops)
# A redeploy cuts the GPU's video download before it cancels the job: a GPU error
# waits this long for that cancellation before it counts as the GPU's.
GPU_ERROR_GRACE = 5
gpu_errors = deque(maxlen=5)  # the latest GPU-unavailable messages (GET /ops)
# Calibration and analytics are CPU post-processing of a finished result: they
# must never queue behind (or block) an upload or a GPU analysis.
post_pool = ThreadPoolExecutor(max_workers=1)
post_lock = threading.Lock()
calibrating = set()  # job ids with a calibration queued or running in this process
calibrating_lock = threading.Lock()
# Pitch checks (previews, and the fit that starts POST /calibration) cost up to
# seconds of CPU each: they get their own threads, never the default executor
# that upload probes and analytics share, and run one at a time (another asks
# again with a 429 rather than queueing). The slot is freed when the request
# ends; a check whose browser went away may still be finishing, so a second
# thread lets the next one start at once.
pitch_pool = ThreadPoolExecutor(max_workers=2)
pitch_slot = threading.BoundedSemaphore(1)
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


@app.exception_handler(DailyLimit)
async def daily_limit(request: Request, exc: DailyLimit):
    # A code, so clients do not retry it like a transient 429, and the time it lifts.
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"This server has reached its limit of {exc.cap} analyses in 24 hours.",
            "code": "daily-limit",
            "resetsAt": round(exc.resets_at),
        },
    )


@app.exception_handler(StorageFull)
async def storage_full(request: Request, exc: StorageFull):
    # A code, so clients show the out-of-storage message instead of retrying the upload.
    return JSONResponse(
        status_code=507,
        content={"detail": "The analysis server is out of storage. Try again later.", "code": "out-of-storage"},
    )


@app.exception_handler(FullMatchUnavailable)
async def full_match_unavailable(request: Request, exc: FullMatchUnavailable):
    # A code, so clients say so at once instead of retrying a 503.
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Full-match analysis is not set up on this server yet. A 20-second test still works.",
            "code": "full-match-unavailable",
        },
    )


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
        data.update(status="interrupted", stage="Worker stopped before the analysis finished.")
    return data


def with_retryable(directory, data):
    """Whether Retry can work (the checks retry_job makes): a stopped job whose complete
    upload is still saved, with retries left. The page offers it only then."""
    return {
        **data,
        "retryable": data.get("status") in ("failed", "interrupted", "cancelled")
        and data.get("id") != active
        and (directory / "video").is_file()
        and bool(data.get("video"))
        and not expired(data)
        and (data.get("attempts", 1) < 3 or not_an_attempt(data)),
    }


def not_an_attempt(data):
    """A redeploy, or a GPU that refused the job before any compute: Retry does not count it."""
    return data.get("interruptedBy") == "shutdown" or bool(data.get("refusedBy"))


def public(data):
    """Owner keys are listing capabilities; never echo them back (nor operator-only details)."""
    return {k: v for k, v in data.items() if k not in (
        "owner", "uploadRequestId", "uploadRequestDigest", "rerunRequestId", "rerunDigest",
        "countedAt", "deployment", "error",
    )}


def _mark_deployment(status):
    """The deployment running this job: one that later finds it unfinished was a redeploy."""
    deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "")
    if deployment:
        status["deployment"] = deployment
    else:
        status.pop("deployment", None)


def local_full_match_ok():
    """This machine may analyse a full match itself: a local worker, a GPU host, or
    a hosted CPU worker whose operator accepts hours per match."""
    return (
        not gpu.public_base()
        or os.getenv("VISION_DEVICE", "").startswith("cuda")
        or os.getenv("VISION_ALLOW_CPU_FALLBACK", "0") == "1"
    )


def full_match_ready():
    """A full match can run here (Modal or this machine); /health reports it as fullMatch."""
    return gpu.modal_enabled() or local_full_match_ok()


def _gpu_only(status):
    """A hosted full match never falls back to hours on the CPU (unless the operator allows it)."""
    return not status.get("maxSeconds") and not local_full_match_ok()


OUT_OF_STORAGE = "The analysis server is out of storage. Your upload is saved; retry later."
RESTARTED = "Worker restarted before the analysis finished."


# Team names, a title and the match date the owner set after the upload. Kept in
# details.json, never status.json: work(), expire_footage and _interrupt rewrite
# the status from memory, which would lose a rename made meanwhile.
DETAILS = "details.json"
DETAILS_KEYS = {"names", "title", "matchDate"}
MAX_DETAILS_BODY = 4096
details_lock = threading.Lock()


def with_details(directory, data):
    """The job as listed: the owner's team names, match date and title, when set."""
    details = _read_json(directory / DETAILS, {})
    if not isinstance(details, dict) or not details:
        return data
    data = dict(data)
    if details.get("names"):
        data["teamNames"] = details["names"]
    if "matchDate" in details:
        data["matchDate"] = details["matchDate"]
    if details.get("title"):
        data["title"] = details["title"]
    return data


def work(directory, status, event):
    global active
    job_id = status["id"]
    began = time.monotonic()
    engine = "gpu" if gpu.modal_enabled() else "cpu"
    gpu_sent = {"item": False}  # whether the GPU sent anything (it then cost compute)
    refused = None  # "gpu" or "config": a hosted refusal that cost no compute

    def update(**kw):
        # stop_jobs records a shutdown before this thread notices it: keep that
        # record (a redeploy is not a failed attempt) unless the analysis completed.
        with status_lock:
            if status.get("interruptedBy") == "shutdown" and kw.get("status") != "completed":
                return
            status.update(kw)
            if kw.get("status") == "completed":
                status.pop("interruptedBy", None)
            write_status(directory, status)

    _mark_deployment(status)  # also set by begin, retry and rerun; a backstop
    with status_lock:
        running[job_id] = (directory, status)
    engines[job_id] = engine
    ops_log.info("job=%s event=start engine=%s", job_id, engine)
    try:
        update(status="processing", stage="Opening video", progress=0)
        options = dict(
            progress=update,
            cancelled=event.is_set,
            profile=status.get("profile", "general"),
            sample_fps=status.get("sampleFps", 3),
            max_seconds=status.get("maxSeconds"),
            start_seconds=status.get("startSeconds", 0),
            ball_search=status.get("ballSearch", "exhaustive"),
            # A rerun keeps the source job's Kit A / Kit B order (and so its team names).
            reference_colours=status.get("referenceColours"),
            reference_job=status.get("sourceJobId") if status.get("referenceColours") else None,
        )
        ran_on_gpu = False
        if gpu.modal_enabled():

            def gpu_progress(**kw):
                gpu_sent["item"] = True
                update(**kw)

            try:
                update(stage="Starting a GPU", progress=0)
                gpu.run_on_modal(
                    job_id, directory / "result.json", TOKEN, **{**options, "progress": gpu_progress}
                )
                ran_on_gpu = True
            except gpu.GPUUnavailable as exc:
                gpu_errors.append({"job": job_id, "at": round(time.time(), 3), "error": str(exc)[:300]})
                # A redeploy cuts the GPU's video download before it cancels the job.
                if event.wait(GPU_ERROR_GRACE):
                    raise InterruptedError("Analysis cancelled") from exc
                if _gpu_only(status):
                    refused = None if gpu_sent["item"] else "gpu"
                    raise ValueError("GPU is unavailable. Your upload is saved; retry when GPU service returns.") from exc
                logging.warning("GPU unavailable, using CPU: %s", exc)
                update(stage="GPU unavailable; analysing on the CPU (slower)", progress=0)
        if not ran_on_gpu:
            if _gpu_only(status):
                refused = "config"
                raise ValueError("GPU processing is not configured. Your upload is saved; contact the service owner.")
            engine = engines[job_id] = "cpu"
            run_video(directory / "video", directory / "result.json", **options)
        update(
            status="completed",
            stage="Analysis complete",
            progress=100,
            engine="gpu" if ran_on_gpu else "cpu",
        )
        threading.Thread(target=_auto_venue, args=(directory, dict(status)), daemon=True).start()
    except InterruptedError:
        if stopping:
            # A redeploy or restart is not the user's cancellation: allow a retry,
            # and do not count this attempt against the retry limit.
            update(status="interrupted", stage=RESTARTED, interruptedBy="shutdown")
        else:
            update(status="cancelled", stage="Analysis cancelled")
    except Exception as exc:
        # The GPU's own message, not the refusal shown to the user (GET /ops only).
        cause = exc.__cause__ if isinstance(exc.__cause__, gpu.GPUUnavailable) else exc
        error = f"{type(cause).__name__}: {cause}"[:300]
        if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
            # A full volume: never re-run on the CPU for it, and leave no partial files.
            logging.exception("Vision job %s ran out of storage", job_id)
            for name in ("result.tmp", "status.tmp"):
                (directory / name).unlink(missing_ok=True)
            try:
                update(status="failed", stage=OUT_OF_STORAGE, error=error)
            except OSError:
                logging.exception("Could not record that job %s ran out of storage", job_id)
        elif stopping:
            # Failed because the worker is shutting down (a cut connection): a redeploy.
            update(status="interrupted", stage=RESTARTED, interruptedBy="shutdown", error=error)
        else:
            logging.exception("Vision job %s failed", job_id)
            stage = str(exc) if isinstance(exc, ValueError) else "Vision processing failed. Check the worker log."
            if refused and status.get("interruptedBy") != "shutdown":
                # Refused before any compute: the start and the attempt are given back.
                with status_lock:
                    stamp = status.pop("countedAt", None)
                refund_start(stamp)
                update(status="failed", stage=stage, refusedBy=refused, error=error)
            else:
                update(status="failed", stage=stage, error=error)
    finally:
        with status_lock:
            running.pop(job_id, None)
        engines.pop(job_id, None)
        with lock:
            if active == job_id:
                active = None
            cancellations.pop(job_id, None)
        outcome = status.get("status")
        ops_log.info(
            "job=%s event=%s engine=%s seconds=%d reason=%s",
            job_id,
            outcome if outcome in ("completed", "interrupted", "cancelled") else "failed",
            engine,
            round(time.monotonic() - began),
            str(status.get("stage", ""))[:80],
        )


def _shutdown_wait():
    try:
        return max(0.0, float(os.getenv("VISION_SHUTDOWN_WAIT_SECONDS", "10")))
    except ValueError:
        return 10.0


def stop_jobs():
    """Shutdown (a redeploy): record each running analysis as interrupted by it,
    then cancel them and wait briefly for them to stop.

    The record comes first and is written here, so it survives a process that
    exits (or is killed) before the job thread reacts. The wait lets a GPU job
    leave its Modal app, which stops the GPU.
    """
    global stopping
    stopping = True
    with status_lock:
        for directory, status in list(running.values()):
            status.update(status="interrupted", stage=RESTARTED, interruptedBy="shutdown")
            try:
                write_status(directory, status)
            except OSError:
                logging.exception("Could not record the interruption of job %s", status.get("id"))
    for event in list(cancellations.values()):
        event.set()
    deadline = time.monotonic() + _shutdown_wait()
    while running and time.monotonic() < deadline:
        time.sleep(0.05)


def recover_calibrations():
    """A calibration running when the worker stopped will never finish: say so."""
    for status in ROOT.glob("*/calibration-job.json"):
        data = _read_json(status) or {}
        try:
            if data.get("state") == "processing" and data.get("auto"):
                status.unlink(missing_ok=True)  # an automatic attempt simply did not happen
            elif data.get("state") == "processing":
                _write_json(status, {"state": "failed", "error": "The analysis server restarted. Apply the pitch setup again.", "finishedAt": time.time()})
        except OSError:
            # A full volume must not stop the worker from starting.
            logging.exception("Could not record the stopped calibration %s", status)


def recover_after_restart():
    """Persist what load_status infers: work in flight when the process died is interrupted.

    One exception: the newest unfinished chunked upload is kept and the worker
    reserved for it again, so a phone that was sending it through a redeploy
    resumes (append writes at receivedBytes). The idle release frees it as
    usual if that browser never comes back.

    Space is freed first and no write here may stop the worker from starting:
    on a full volume, DELETE /jobs is the only way to free space.
    """
    global active
    for temporary in ROOT.glob("*/*.tmp"):
        temporary.unlink(missing_ok=True)
    # A training run killed with the process never reached its cleanup: frame
    # copies of customers' footage must not stay behind.
    shutil.rmtree(ROOT / "training" / "dataset", ignore_errors=True)
    shutil.rmtree(ROOT / "training" / "runs", ignore_errors=True)
    _recover_training()
    # A job a previous deployment was running stopped for a redeploy (even one
    # killed before it could record that); a crash restarts the same deployment.
    deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "")
    uploads = []
    for path in ROOT.glob("*/status.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("status") not in ("uploading", "processing") or data.get("id") == active:
            continue
        if data["status"] == "uploading" and "expectedBytes" in data and (path.parent / "video").is_file():
            uploads.append((data.get("createdAt", 0), path, data))
            continue
        redeployed = data["status"] == "processing" and deployment and data.get("deployment") not in (None, "", deployment)
        _interrupt(path, data, "shutdown" if redeployed else None)
    uploads.sort(key=lambda u: u[0])
    if uploads and active is None:
        _, path, data = uploads.pop()
        video = path.parent / "video"
        try:
            # status.json is written after each chunk: bytes past receivedBytes are unconfirmed.
            received = min(int(data.get("receivedBytes", 0)), video.stat().st_size)
            with video.open("r+b") as f:
                f.truncate(received)
            data.update(receivedBytes=received, progress=round(received / (data["expectedBytes"] or 1) * 100, 1))
            write_status(path.parent, data)
        except OSError:
            # Its saved receivedBytes could now exceed the file: never resume it.
            logging.exception("Could not keep upload %s across the restart", data.get("id"))
            _interrupt(path, data)
        else:
            with lock:
                active = data["id"]
                upload_activity[active] = upload_started[active] = time.monotonic()
                upload_sizes[active] = data["expectedBytes"]
    for _, path, data in uploads:
        _interrupt(path, data)


def _interrupt(path, data, interrupted_by=None):
    if data["status"] == "uploading":
        (path.parent / "video").unlink(missing_ok=True)
    # A YouTube download stopped by the restart (yt-dlp's download.*.part, up to 2 GB).
    for leftover in path.parent.glob("download.*"):
        leftover.unlink(missing_ok=True)
    data.update(status="interrupted", stage=RESTARTED)
    if interrupted_by:
        data["interruptedBy"] = interrupted_by
    try:
        write_status(path.parent, data)
    except OSError:
        # load_status still reports it as interrupted (it is not the active job).
        logging.exception("Could not record the interruption of job %s", data.get("id"))


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
        upload_sizes.pop(job_id, None)
        if active == job_id:
            active = None


def upload_deadline(job_id=None, size=None):
    """Seconds an upload may hold the worker: longer for a large declared size."""
    size = upload_sizes.get(job_id) if size is None else size
    if not size:
        return UPLOAD_MAX_SECONDS
    return max(UPLOAD_MAX_SECONDS, min(UPLOAD_SIZED_MAX_SECONDS, size / UPLOAD_FLOOR_BYTES_PER_SECOND))


def upload_minutes(job_id=None, size=None):
    return max(1, round(upload_deadline(job_id, size) / 60))


def release_stale_upload(contended=False):
    """A browser that closed mid-upload must not block the worker forever.

    The overall time limit (upload_deadline) only matters when another start
    wants the worker (contended): a lone slow upload is allowed to finish. A
    paused upload gives way after UPLOAD_IDLE_SECONDS only to such a start;
    otherwise (polls, its own owner) it is kept for UPLOAD_ABANDON_SECONDS so a
    phone that locked or switched apps resumes instead of starting from 0%.
    """
    job_id = active
    if job_id is None or job_id in cancellations:
        return
    last = upload_activity.get(job_id)
    if last is None:
        return
    now = time.monotonic()
    idle = now - last >= (UPLOAD_IDLE_SECONDS if contended else UPLOAD_ABANDON_SECONDS)
    overdue = contended and now - upload_started.get(job_id, now) >= upload_deadline(job_id)
    if not (idle or overdue):
        return
    directory = ROOT / job_id
    try:
        status = json.loads((directory / "status.json").read_text())
    except (OSError, ValueError):
        status = {"id": job_id, "createdAt": time.time()}
    if status.get("status", "uploading") != "uploading":
        return
    if idle:
        release(job_id, directory, status, "Upload stopped before the video was complete.")
    else:
        status["releasedReason"] = "overdue"
        # The limit this upload had (it grows with the declared size; see upload_deadline).
        minutes = upload_minutes(job_id, status.get("expectedBytes"))
        release(job_id, directory, status, f"Upload took longer than the {minutes}-minute limit.")


def sweep_stale():
    with lock:
        release_stale_upload()


def expired(data):
    # From when the footage arrived: a rerun links the same file, so it shares the
    # upload's clock (footageCreatedAt) and a chain of reruns cannot extend it.
    # "uploadedAt" is the same clock under its earlier name.
    created = data.get("footageCreatedAt", data.get("uploadedAt", data.get("createdAt", time.time())))
    return (
        RETENTION_HOURS > 0
        and data.get("id") != active
        and created < time.time() - RETENTION_HOURS * 3600
    )


def expire_footage(directory, data):
    """Delete every copy of the footage (video, label frames, a stopped download); keep the measured result."""
    (directory / "video").unlink(missing_ok=True)
    for leftover in directory.glob("download.*"):
        leftover.unlink(missing_ok=True)
    _remove_label_frames(directory)  # balllabels.frame_cache
    data["videoDeleted"] = True
    write_status(directory, data)


def expire_if_due(directory, data):
    """Apply retention when a job is read, not only at the hourly sweep."""
    if expired(data) and ((directory / "video").exists() or (directory / "labelframes").exists()):
        expire_footage(directory, data)
    elif data.get("videoDeleted") and (directory / "labelframes").exists():
        # Label stills are full-resolution pictures of the footage: they go with
        # it (also on deployments that expired footage before stills were swept).
        _remove_label_frames(directory)
    return data


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
        expire_if_due(path.parent, data)


def _remove_label_frames(directory):
    with label_lock:
        shutil.rmtree(directory / "labelframes", ignore_errors=True)


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


SPACE_MARGIN = 256 * 1024 * 1024


def check_space(size):
    free = shutil.disk_usage(ROOT).free
    if free < size + SPACE_MARGIN:
        raise StorageFull()


def check_models(profile, ball_search):
    """Refuse a job whose weights are missing; player load never needs the ball model."""
    if profile not in (player_load_profiles() if ball_search == "none" else available_profiles()):
        raise HTTPException(503, "The selected vision models are not installed")


def download_budget():
    """Largest download that fits now: the upload limit, or the free space if smaller."""
    return min(MAX_BYTES, shutil.disk_usage(ROOT).free - SPACE_MARGIN)


def check_volume():
    """Warn before the volume refuses a full-size upload (results and label stills also use it)."""
    usage = shutil.disk_usage(ROOT)
    low = usage.free < MAX_BYTES + SPACE_MARGIN
    if low:
        ops_log.warning("Volume low: %d MB free of %d MB", usage.free // 2**20, usage.total // 2**20)
    return low


_volume_watch = None


def start_volume_watch():
    """At startup, then hourly (also when footage is kept forever and no sweeper runs)."""
    global _volume_watch

    def check():
        try:
            check_volume()
        except OSError:
            logging.exception("Volume check failed")

    check()
    if _volume_watch is not None and _volume_watch.is_alive():
        return

    def watch():
        while not stopping:
            time.sleep(3600)
            if not stopping:
                check()

    _volume_watch = threading.Thread(target=watch, name="volume-watch", daemon=True)
    _volume_watch.start()


app.add_event_handler("startup", start_volume_watch)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/health")
def health():
    sweep_stale()
    load_only = player_load_profiles()
    return {
        # Uploads default to player load, which needs only the player weights;
        # "profiles" lists those that can also search for the ball.
        "available": "general" in load_only,
        "profiles": available_profiles(),
        "playerLoadProfiles": load_only,
        "activeJob": active is not None,
        "mode": "computer-vision",
        "diagnostics": True,
        "maxBytes": MAX_BYTES,
        "maxChunk": MAX_CHUNK,
        # A paused upload gives way to another start after uploadIdleSeconds; alone
        # it is kept for uploadAbandonSeconds, so the browser can resume it.
        "uploadIdleSeconds": UPLOAD_IDLE_SECONDS,
        "uploadAbandonSeconds": UPLOAD_ABANDON_SECONDS,
        "retentionHours": RETENTION_HOURS or None,
        "gpu": gpu.modal_enabled() or os.getenv("VISION_DEVICE", "").startswith(("cuda", "mps")),
        # False on a hosted CPU worker without Modal: only 20-second tests run there.
        "fullMatch": full_match_ready(),
        # Pitch calibration, event detection and reviewer decisions.
        "analytics": True,
        "privateJobs": True,
        "reviewIdempotency": True,
        "savedVideoReruns": True,
        # Whether the website may start ball-model training (otherwise an operator action).
        "siteTraining": _site_training(),
    }


@app.get("/ops")
def ops():
    """Operator view (service token only: the site's proxy never forwards it).

    What is running, today's starts, storage, GPU errors and recent problems.
    Never owner keys, request ids, video grants or tokens.
    """
    now = time.time()
    current = None
    job = active
    if job is not None:
        data = _read_json(ROOT / job / "status.json", {})
        data = data if isinstance(data, dict) else {}
        current = {
            "id": job, "status": data.get("status"), "stage": data.get("stage"),
            "progress": data.get("progress"), "engine": engines.get(job) or data.get("engine"),
        }
    starts = sorted(_recent_starts(now))
    cap = daily_cap()
    usage = shutil.disk_usage(ROOT)
    count, problems = 0, []
    for path in ROOT.glob("*/status.json"):
        count += 1
        try:
            changed = path.stat().st_mtime
            if now - changed > 86400:
                continue
            data = load_status(path.parent)
        except (OSError, ValueError, KeyError):
            continue
        if data.get("status") in ("failed", "interrupted"):
            keys = ("id", "status", "stage", "attempts", "interruptedBy", "refusedBy", "error")
            problems.append((changed, {k: data.get(k) for k in keys}))
    problems.sort(key=lambda p: -p[0])
    state = dict(training)
    if "run" in state:
        state["run"] = _private_run(state["run"])
    return {
        "active": current,
        "dailyStarts": {
            "used": len(starts), "cap": cap,
            # When the next start frees up (rolling 24 hours), as in the 429 answer.
            "resetsAt": round(starts[max(0, len(starts) - cap)] + 86400) if starts and cap else None,
        },
        "trainingStarts": {"used": len(_recent_training_starts(now)), "cap": training_cap()},
        "training": state,
        "storage": {
            "freeBytes": usage.free, "totalBytes": usage.total, "low": usage.free < MAX_BYTES + SPACE_MARGIN,
        },
        "jobs": count,
        "gpu": {
            "enabled": gpu.modal_enabled(),
            "publicUrl": bool(gpu.public_base()),
            "recentErrors": list(gpu_errors),
        },
        "recentProblems": [p for _, p in problems[:10]],
        "release": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
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
            data = expire_if_due(p.parent, load_status(p.parent))
        except (OSError, ValueError, KeyError):
            continue
        # Ownerless legacy jobs are visible only to a local, unscoped listing.
        if owner is not None and data.get("owner") != owner:
            continue
        entries.append(public(with_details(p.parent, with_retryable(p.parent, data))))
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
        # countedAt: the start this run used, given back if the GPU refuses it.
        status.update(video=metadata, fileSize=size, status="processing", countedAt=record_start())
        status.pop("receivedBytes", None)
        status.pop("expectedBytes", None)
        _mark_deployment(status)
        write_status(directory, status)
        upload_activity.pop(job_id, None)
        upload_started.pop(job_id, None)
        upload_sizes.pop(job_id, None)
        event = threading.Event()
        cancellations[job_id] = event
        pool.submit(work, directory, status, event)
    return public(status)


@app.post("/jobs")
async def create(request: Request):
    global active
    check_daily_budget()
    if request.headers.get("content-type", "").split(";")[0] not in (
        "video/mp4",
        "video/quicktime",
        "application/octet-stream",
    ):
        raise HTTPException(415, "Upload a video file")
    profile = request.query_params.get("profile", "general")
    if profile not in ("general", "broadcast", "small-ball"):
        raise HTTPException(400, "Unknown footage profile")
    ball_search = request.query_params.get("search", "exhaustive")
    if ball_search not in ("exhaustive", "adaptive", "none"):
        raise HTTPException(400, "Invalid ball search mode")
    check_models(profile, ball_search)
    try:
        sample_fps = int(request.query_params.get("fps", "3"))
    except ValueError as exc:
        raise HTTPException(400, "Choose 3, 6 or 10 analysed frames per second") from exc
    if sample_fps not in (3, 6, 10):
        raise HTTPException(400, "Choose 3, 6 or 10 analysed frames per second")
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
            raise HTTPException(413, f"Maximum video size is {max_size_text()}")
    request_id = request.query_params.get("requestId")
    if request_id is not None and (
        not re.fullmatch(r"[a-f0-9-]{32,36}", request_id) or not owner or expected is None
    ):
        raise HTTPException(400, "A resumable upload needs a valid request ID, owner and video size")
    if diagnostic == "false":
        check_full_match()
    title = request.query_params.get("title", "Football match")[:200]
    digest = hashlib.sha256(json.dumps(
        [expected, title, profile, sample_fps, diagnostic, start_seconds, ball_search]
    ).encode()).hexdigest()
    await asyncio.to_thread(delete_expired)
    job_id = uuid.uuid4().hex
    with lock:
        # The browser resuming the upload it reserved (same owner, request ID and
        # options) is never contention, however long it paused.
        if request_id and active is not None and active in upload_activity:
            current = _read_json(ROOT / active / "status.json", {})
            if (
                isinstance(current, dict)
                and current.get("status") == "uploading"
                and current.get("owner") == owner
                and current.get("uploadRequestId") == request_id
                and current.get("uploadRequestDigest") == digest
            ):
                upload_activity[active] = time.monotonic()
                return public(current)
        # This owner's own paused upload is not released by its own new request
        # (a reload with other options gets the own-upload answer below instead);
        # another owner's start still frees an upload idle for UPLOAD_IDLE_SECONDS.
        release_stale_upload(contended=not (active is not None and own_upload(active, owner)))
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
            # This browser's own stopped upload (a reload, a closed tab): say so, and
            # how to free it. It is not released here: another tab may be sending it.
            mine = own_upload(active, owner)
            if mine:
                return JSONResponse(status_code=409, content=mine)
            raise HTTPException(
                409, "Another video is being analysed. Wait for it to finish or cancel it."
            )
        # Before anything is written or reserved: a refusal leaves no failed match
        # behind and no request ID that a resend would collide with.
        check_space(expected or 0)
        active = job_id
    upload_activity[job_id] = upload_started[job_id] = time.monotonic()
    if expected is not None:
        upload_sizes[job_id] = expected
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
        if expected is not None:
            (directory / "video").touch()
            return public(status)
        size = 0
        with (directory / "video").open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"Maximum video size is {max_size_text()}")
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


def own_upload(job_id, owner):
    """The 409 body for an unfinished chunked upload that belongs to this owner, else None."""
    if not owner or job_id not in upload_activity or job_id in cancellations:
        return None
    data = _read_json(ROOT / job_id / "status.json", {})
    if not isinstance(data, dict) or data.get("status") != "uploading" or "expectedBytes" not in data:
        return None
    if not data.get("owner") or not hmac.compare_digest(data["owner"], owner):
        return None
    received, expected = data.get("receivedBytes", 0), data["expectedBytes"]
    return {
        "detail": f"This browser's earlier upload is still reserved ({int(100 * received / (expected or 1))}%). "
        "Choose the same file to resume, or cancel it.",
        "code": "own-upload",
        "jobId": job_id,
        "receivedBytes": received,
        "expectedBytes": expected,
    }


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
            url, directory, max(1, download_budget()), progress=update, cancelled=event.is_set
        )
        path.replace(directory / "video")
        size = (directory / "video").stat().st_size
        metadata = probe(directory / "video")
        if info.get("title") and status.get("title") == "YouTube match":
            status["title"] = str(info["title"])[:200]
        status.pop("receivedBytes", None)
        status.update(video=metadata, fileSize=size, source=info)
        # Counted like begin(): only once there is a decodable video to analyse
        # (a blocked download costs no GPU). The worker stayed reserved for the
        # whole download, so no other start can have used the allowance meanwhile.
        with lock:
            status["countedAt"] = record_start()
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
    check_daily_budget()
    from app.vision.youtube import video_id

    try:
        vid = video_id(request.query_params.get("url", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    profile = request.query_params.get("profile", "general")
    if profile not in ("general", "broadcast", "small-ball"):
        raise HTTPException(400, "Unknown footage profile")
    ball_search = request.query_params.get("search", "none")
    if ball_search not in ("exhaustive", "adaptive", "none"):
        raise HTTPException(400, "Invalid ball search mode")
    check_models(profile, ball_search)
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
    check_full_match()  # a link is always analysed in full
    await asyncio.to_thread(delete_expired)
    # The size is unknown until YouTube answers: the download steps down in
    # resolution to fit the space there is, so only refuse when nearly full.
    check_space(100 * 1024 * 1024)
    job_id = uuid.uuid4().hex
    with lock:
        release_stale_upload(contended=True)
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
            "ballSearch": ball_search,
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
        if status.get("releasedReason") == "overdue":
            # Say why: the time limit, not a dropped connection, ended this upload.
            percent = int(100 * status.get("receivedBytes", 0) / (status.get("expectedBytes") or 1))
            minutes = upload_minutes(size=status.get("expectedBytes"))
            raise HTTPException(
                409,
                f"Uploads must finish within {minutes} minute{'' if minutes == 1 else 's'}; "
                f"this one stopped at {percent}%. Use Wi-Fi or a smaller export.",
            )
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
    directory = folder(job_id)
    return public(with_details(directory, with_retryable(directory, expire_if_due(directory, load_status(directory)))))


def _clean_text(value, limit, what):
    """Printable text only (control, format and private-use characters removed), trimmed."""
    if not isinstance(value, str):
        raise HTTPException(400, f"{what} must be text")
    text = "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()
    if not 1 <= len(text) <= limit:
        raise HTTPException(400, f"{what} must be 1 to {limit} characters")
    return text


@app.post("/jobs/{job_id}/details")
async def save_details(job_id: str, request: Request):
    """Team names, title and match date for the owner (authorized like every job route).

    Every key is optional; the ones sent replace the saved ones. Not copied to reruns.
    """
    directory = folder(job_id)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_DETAILS_BODY:
            raise HTTPException(400, "Details are too large")

    def reject_constant(value):
        raise ValueError(f"{value} is not allowed")

    try:
        patch = json.loads(body or b"{}", parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    if not isinstance(patch, dict):
        raise HTTPException(400, "Expected a JSON object")
    unknown = set(patch) - DETAILS_KEYS
    if unknown:
        raise HTTPException(400, f"Unknown detail: {sorted(unknown)[0][:40]}")
    clean = {}
    if "names" in patch:
        names = patch["names"]
        if not isinstance(names, list) or len(names) != 2:
            raise HTTPException(400, "Send two team names")
        clean["names"] = [_clean_text(n, 80, "A team name") for n in names]
    if "title" in patch:
        clean["title"] = _clean_text(patch["title"], 200, "The title")
    if "matchDate" in patch:
        value = patch["matchDate"]
        if value is not None:
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError(value)
                day = date.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(400, "The match date must be a date (YYYY-MM-DD)") from exc
            if not date(2000, 1, 1) <= day <= date.today() + timedelta(days=1):
                raise HTTPException(400, "The match date must be between 2000 and today")
        clean["matchDate"] = value

    def merge():
        with details_lock:
            if not (directory / "status.json").is_file():
                raise HTTPException(404, "Job not found")  # deleted meanwhile
            saved = _read_json(directory / DETAILS, {})
            merged = {**(saved if isinstance(saved, dict) else {}), **clean}
            try:
                _write_json(directory / DETAILS, merged)
            except FileNotFoundError as exc:
                raise HTTPException(404, "Job not found") from exc
            return merged

    merged = await asyncio.to_thread(merge)
    return {"teamNames": merged.get("names"), "title": merged.get("title"), "matchDate": merged.get("matchDate")}


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

    from app.vision.profiles import CUSTOM_BALL, models_dir

    for profile in ("general", "broadcast"):
        for path in model_paths(profile):
            if path.name == name and path.is_file():
                return FileResponse(path, media_type="application/octet-stream")
    # Fine-tuned ball weights (a GPU may still be finishing a job started before a switch).
    if CUSTOM_BALL.fullmatch(name) and (models_dir() / name).is_file():
        return FileResponse(models_dir() / name, media_type="application/octet-stream")
    raise HTTPException(404, "Model not installed")


@app.get("/jobs/{job_id}/video")
def video(job_id: str, request: Request):
    directory = folder(job_id)
    path = directory / "video"
    if not path.is_file() or job_id == active and job_id in upload_activity:
        raise HTTPException(404, "Video is unavailable")
    data = json.loads((directory / "status.json").read_text())
    if expired(data):
        expire_footage(directory, data)
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
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:  # deep nesting fits in 256 KB
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
ANALYTICS_VERSION = 11  # 10 merged rounds 1 and 2; 11 adds kit colours, span, ball search and player load
_prepared = OrderedDict()  # (job, result stamp, calibration stamp, direction) -> prepared analysis
PREPARED_CACHE = 3


def _stamp(path):
    return [path.stat().st_mtime_ns, path.stat().st_size] if path.is_file() else 0


# Labelling and pitch previews read little of result.json, but parsing a
# 90-minute result takes about a second (and 160 MB live). Keep a slim view per
# result version: video, sampling and each frame's time, number and ball.
_slim = OrderedDict()  # (job, result stamp) -> slim result
_slim_lock = threading.Lock()
SLIM_CACHE = 3
SLIM_KEYS = ("video", "sampleFps", "ballSearch", "ballInference")


def _slim_result(directory):
    path = directory / "result.json"
    stamp = _stamp(path)
    key = (directory.name, tuple(stamp) if stamp else ())
    with _slim_lock:
        cached = _slim.get(key)
        if cached is not None:
            _slim.move_to_end(key)
            return cached
    full = json.loads(path.read_text())
    view = {k: full[k] for k in SLIM_KEYS if k in full}
    view["frames"] = [{k: f[k] for k in ("t", "frame", "ball") if k in f} for f in full.get("frames") or []]
    del full
    with _slim_lock:
        _slim[key] = view
        _slim.move_to_end(key)
        while len(_slim) > SLIM_CACHE:
            _slim.popitem(last=False)
    return view


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
        try:
            _write_json(directory / "analysis.json", analysis)
        except OSError:
            # Only the cache failed (a full volume): the report still opens.
            logging.exception("Could not cache the analysis of %s", directory.name)
            (directory / "analysis.tmp").unlink(missing_ok=True)
    return analysis


@app.get("/jobs/{job_id}/analysis")
def analysis(job_id: str, request: Request):
    """The match analytics. ?positions=0 leaves out the per-frame pitch positions
    (most of a calibrated analysis), so the report can draw before they arrive;
    the cached analysis keeps them either way."""
    directory, _ = _finished_result(job_id)
    try:
        data = compute_analysis(directory)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"Analytics could not be computed: {exc}") from exc
    if request.query_params.get("positions") == "0":
        data = {k: v for k, v in data.items() if k != "positions"}
        data["positionsOmitted"] = True
    return data


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


async def _pitch_check(fn, *args):
    if not pitch_slot.acquire(blocking=False):
        raise HTTPException(429, "Another pitch check is running; try again in a moment.")
    try:
        return await asyncio.get_running_loop().run_in_executor(pitch_pool, fn, *args)
    finally:
        pitch_slot.release()


@app.post("/jobs/{job_id}/calibration/preview")
async def calibration_preview(job_id: str, request: Request):
    from app.vision.calibrate import preview

    directory, _ = _finished_result(job_id)
    body = await _json_body(request)
    result = await asyncio.to_thread(_slim_result, directory)  # only the video size is read
    try:
        return await _pitch_check(preview, result, body)
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


def _auto_venue(directory, status, limit=5, margin=0.1):
    """Set up the pitch of a finished match from a saved venue, when one fits.

    A fixed venue camera sees the same pitch every match: each of the owner's
    venues with the same video size is checked against this match's painted
    lines (build_from_venue refuses one that does not line up), and the one
    that fits them best is applied. A neighbouring court of a similar size can
    also line up, so when the runner-up fits almost as well (median line score
    within `margin`), nothing is applied and the reviewer chooses. Nothing is
    applied without the footage to check against. VISION_AUTO_VENUE=0 turns it off.
    """
    from app.vision.calibrate import build_from_venue

    if os.getenv("VISION_AUTO_VENUE", "1") == "0":
        return None
    video = directory / "video"
    if not video.is_file() or (directory / "calibration.json").is_file():
        return None
    try:
        result = json.loads((directory / "result.json").read_text())
        size = [result["video"]["width"], result["video"]["height"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    owner = status.get("owner", "")
    venues = [
        v for v in (_read_json(p) for p in _venue_dir().glob("*.json"))
        if isinstance(v, dict) and v.get("id") and v.get("owner", "") == owner and list(v.get("size") or []) == size
    ]
    if not venues:
        return None
    venues.sort(key=lambda v: -(v.get("createdAt") or 0))
    with calibrating_lock:
        if directory.name in calibrating:
            return None
        calibrating.add(directory.name)
    job_path = directory / "calibration-job.json"
    applied = None
    explained = False
    try:
        _write_json(job_path, {"state": "processing", "progress": 0, "startedAt": time.time(), "auto": True})
        fits = []
        for venue in venues[:limit]:
            try:
                data = build_from_venue(result, venue, video)
            except ValueError:
                continue  # the lines do not match this venue
            check = data.get("venue") or {}
            if check.get("verified") and isinstance(check.get("lineScore"), (int, float)):
                fits.append((check["lineScore"], venue, data))
        if not fits:
            return None
        fits.sort(key=lambda fit: -fit[0])
        score, venue, data = fits[0]
        if len(fits) > 1 and score - fits[1][0] < margin:
            names = " and ".join(f'"{v.get("name")}"' for _, v, _ in fits[:2])
            _write_json(job_path, {
                "state": "failed", "auto": True, "finishedAt": time.time(),
                "error": f"Saved venues {names} both fit this match's lines (scores {score:.2f} and {fits[1][0]:.2f}), "
                         "so neither was applied automatically. Choose one, or set up the pitch.",
            })
            explained = True
            return None
        data["auto"] = True
        if (directory / "calibration.json").is_file() or not (directory / "status.json").is_file():
            return None  # the reviewer set one up meanwhile, or the match was deleted
        _write_json(directory / "calibration.json", data)
        compute_analysis(directory)
        _write_json(job_path, {"state": "done", "progress": 100, "finishedAt": time.time(), "venue": venue["id"], "auto": True})
        applied = venue["id"]
        return applied
    except Exception:  # never fail a finished analysis over an optional setup
        import logging

        logging.exception("Automatic venue setup failed")
        return None
    finally:
        if applied is None and not explained and (_read_json(job_path) or {}).get("auto"):
            job_path.unlink(missing_ok=True)  # nothing matched: no setup to report
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
    from app.vision.calibrate import poor_fit_message, preview

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

    result = await asyncio.to_thread(_slim_result, directory)  # only the video size is read
    try:
        fit = await _pitch_check(preview, result, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if fit["fit"]["quality"] == "poor":
        raise HTTPException(400, poor_fit_message(fit["fit"]))
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
    if d.get("sample") is True:
        out["sample"] = True  # decided in the random-sample view (unbiased estimates)
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


# ------------------------------------------------------------------ ball labels
# Ground truth for the ball from the reviewer: measured accuracy per match and
# training data for the ball detector (app/vision/balllabels.py).


def _label_seed(job_id):
    return int(job_id[:8], 16)


def _video_path(directory):
    path = directory / "video"
    data = _read_json(directory / "status.json", {})
    return path if path.is_file() and not expired(data) else None


label_lock = threading.Lock()  # balllabels.json writes; never held during analysis
preparing_frames = set()  # job ids whose label frames are being extracted


def _prepare_label_frames(job_id, directory, result, picks):
    """Extract the label frames in one background pass (exact engine numbering)."""
    from app.vision import balllabels

    video_path = _video_path(directory)
    if video_path is None:
        return False
    cache = balllabels.frame_cache(directory)
    if all((cache / f"{i}.jpg").is_file() for i in picks):
        return True
    with label_lock:
        if job_id in preparing_frames:
            return False
        preparing_frames.add(job_id)

    def run():
        try:
            balllabels.prepare_frames(directory, result, picks, video_path)
        except Exception:  # the next listing retries; frames already written stay
            pass
        finally:
            with label_lock:
                preparing_frames.discard(job_id)

    threading.Thread(target=run, daemon=True).start()
    return False


def _ball_labels_response(job_id, directory, result, prepare=True):
    from app.vision import balllabels

    store = balllabels.load(directory / "balllabels.json")
    picks = balllabels.sample_frames(result, seed=_label_seed(job_id))
    frames = result.get("frames") or []
    ready = _prepare_label_frames(job_id, directory, result, picks) if prepare else True
    return {
        "framesReady": ready,
        "frames": [{"index": i, "t": frames[i]["t"], "guess": balllabels.guess(frames[i])} for i in picks],
        "labels": store.get("labels", {}),
        "metrics": balllabels.metrics(result, store),
        "videoAvailable": _video_path(directory) is not None,
        "size": [(result.get("video") or {}).get("width"), (result.get("video") or {}).get("height")],
    }


@app.get("/jobs/{job_id}/ball-labels")
def ball_labels(job_id: str):
    directory, _ = _finished_result(job_id)
    return _ball_labels_response(job_id, directory, _slim_result(directory))


@app.post("/jobs/{job_id}/ball-labels")
async def add_ball_label(job_id: str, request: Request):
    from app.vision import balllabels

    directory, _ = _finished_result(job_id)
    body = await _json_body(request)
    result = await asyncio.to_thread(_slim_result, directory)
    size = ((result.get("video") or {}).get("width") or 0, (result.get("video") or {}).get("height") or 0)
    try:
        label = balllabels.clean_label(body, size)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if label["index"] >= len(result.get("frames") or []):
        raise HTTPException(400, "Unknown frame")

    def save():
        with label_lock:
            # The match may have been deleted while this request waited.
            if not (directory / "status.json").is_file():
                raise HTTPException(404, "Job not found")
            store = balllabels.load(directory / "balllabels.json")
            try:
                balllabels.apply_label(store, label, round(time.time(), 3))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            _write_json(directory / "balllabels.json", store)

    await asyncio.to_thread(save)
    return await asyncio.to_thread(_ball_labels_response, job_id, directory, result, False)


@app.get("/jobs/{job_id}/frames/{index}")
def frame_image(job_id: str, index: int):
    """One label frame as JPEG at the video's own resolution (prepared by the listing)."""
    from fastapi.responses import Response

    from app.vision import balllabels

    directory, _ = _finished_result(job_id)
    # A frame is a copy of the footage: it goes with it (expired or deleted), even
    # if a file is left behind, before any is served. This also catches a
    # background extraction that wrote after the sweep.
    data = _read_json(directory / "status.json", {})
    if not isinstance(data, dict) or expired(data) or data.get("videoDeleted") or _video_path(directory) is None:
        _remove_label_frames(directory)
        raise HTTPException(404, "Video is unavailable")
    path = balllabels.frame_cache(directory) / f"{index}.jpg"
    if index >= 0 and path.is_file():
        return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    raise HTTPException(409, "This frame is still being prepared")


training = {"state": "idle"}  # one ball-model training at a time, in this process
training_lock = threading.Lock()
# A copy of `training` on the volume: a restart then says the run stopped instead
# of forgetting it (the run itself is a thread of this process).
TRAINING_STATE = "training-state.json"
TRAINING_RESTARTED = "The analysis server restarted during training. Start it again."
TRAINING_FAILED = "Training failed on the GPU. Your labels are kept; try again later."
training_state_lock = threading.Lock()  # one writer of `training` and its copy at a time


def _set_training(clear=False, **kw):
    """Update the training state and save it (best-effort: a full volume must not stop a run)."""
    with training_state_lock:
        if clear:
            training.clear()
        training.update(kw)
        try:
            _write_json(ROOT / TRAINING_STATE, training)
        except OSError:
            logging.exception("Could not save the ball-model training state")


def _recover_training():
    """At startup: a run that was going when the process stopped has failed."""
    data = _read_json(ROOT / TRAINING_STATE)
    if not isinstance(data, dict) or data.get("state") not in ("running", "done", "failed"):
        return
    if data["state"] == "running":
        _set_training(clear=True, state="failed", stage=TRAINING_RESTARTED)
    else:
        with training_state_lock:
            training.clear()
            training.update(data)


def _private_run(run):
    # Owner keys are capabilities for a browser's matches: never hand them out.
    return {k: v for k, v in run.items() if k not in ("owner", "ownerHash")} if isinstance(run, dict) else run


def _models_dir():
    from app.vision.profiles import models_dir

    return models_dir()


@app.get("/ball-model")
def ball_model():
    """Fine-tuned ball models: which one is active and how each run validated.

    Browser owner keys are stripped from the response.
    """
    from app.vision import balltrain

    registry = balltrain.registry(_models_dir())
    state = dict(training)
    if "run" in state:
        state["run"] = _private_run(state["run"])
    return {
        **registry, "runs": [_private_run(r) for r in registry.get("runs", [])], "training": state,
        # Whether the website may start a run (see train_ball_model): the page hides the button otherwise.
        "siteTraining": _site_training(),
    }


def _site_training():
    return os.getenv("VISION_SITE_TRAINING", "0") == "1"


@app.post("/ball-model/train")
async def train_ball_model(request: Request):
    """Fine-tune the ball detector on every labelled match (Modal GPU when configured)."""
    from app.vision import balltrain
    from app.vision.profiles import model_paths
    from app.vision.runtime import inference_device

    body = await _json_body(request) if request.headers.get("content-length") not in (None, "0") else {}
    epochs = body.get("epochs", 60)
    if isinstance(epochs, bool) or not isinstance(epochs, int) or not 1 <= epochs <= 300:
        raise HTTPException(400, "Epochs must be a whole number from 1 to 300")
    owner = body.get("owner")
    if owner is not None and (not isinstance(owner, str) or not OWNER.fullmatch(owner)):
        raise HTTPException(400, "Invalid owner")
    # Switching the ball model changes every later analysis on this worker, and a
    # run costs GPU time: an operator action. A single-owner deployment can allow
    # its website to start it (VISION_SITE_TRAINING=1), on that owner's matches.
    # Only the website's proxy adds these headers (server-side); an operator's
    # terminal request with the service token carries neither, even on a hosted
    # worker (where require_owner() is always true).
    from_site = request.headers.get("x-pitchlens-require-owner") == "1" or bool(request.headers.get("x-pitchlens-owner"))
    if from_site and not _site_training():
        # Shown to a team owner: say what happens to their labels, not how to configure the server.
        raise HTTPException(403, "Training is not available on this site. Your labels are saved for the next ball-model update.")
    if from_site:
        owner = _owner(request)
        epochs = min(epochs, SITE_TRAINING_EPOCHS)
    base = model_paths("broadcast")[1]
    if not base.is_file():
        raise HTTPException(409, "No tiled ball model is installed to start from")
    with training_lock:
        if training.get("state") == "running":
            raise HTTPException(409, "A training run is already in progress")
        if from_site:
            check_training_budget()
        _set_training(clear=True, state="running", stage="Starting", startedAt=round(time.time(), 3))
    use_remote = gpu.modal_enabled()
    counted = {}  # the site start this run used, and whether Modal accepted the run

    def started():
        # Counted only once the labels passed every check and a GPU (or this
        # machine) is about to train: a "too few labels" refusal costs nothing.
        if from_site:
            with training_lock:
                counted["stamp"] = record_training_start()

    def remote(dataset_zip, base_bytes, n):
        from app.vision.modal_train import app as modal_app, train_ball

        with modal_app.run():
            counted["accepted"] = True  # past auth, quota and the image build
            return train_ball.remote(dataset_zip, base_bytes, n)

    def run():
        try:
            outcome = balltrain.run_training(
                ROOT,
                _models_dir(),
                base,
                epochs=epochs,
                owner=owner,
                remote=remote if use_remote else None,
                # The device matters only for training here (and may import torch).
                device=None if use_remote else inference_device(),
                now=round(time.time(), 3),
                progress=lambda message: _set_training(stage=message),
                on_start=started,
                usable=lambda d: balltrain._usable(d) and _video_path(d) is not None,
            )
            # A run refused for too few held-out balls says why (nothing was trained).
            _set_training(state="done", stage=outcome.get("message") or "Finished", run=outcome)
        except Exception as exc:  # nothing is switched
            logging.exception("Ball-model training failed")
            if isinstance(exc, ValueError):
                stage = str(exc)  # the label-count refusals are written for the team owner
            elif from_site:
                stage = TRAINING_FAILED
            else:
                stage = str(exc) or type(exc).__name__  # the operator; never a blank reason
            if use_remote and "stamp" in counted and not counted.get("accepted"):
                # Modal refused the run before any GPU time: the site start is given back.
                with training_lock:
                    refund_training_start(counted["stamp"])
            _set_training(state="failed", stage=stage)

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


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
            # sourceJob: deleting that match deletes this venue (the page names it first).
            # The listing is this owner's own, so no other browser's job ids appear.
            out.append({k: data.get(k) for k in ("id", "name", "template", "size", "static", "createdAt", "sourceJob")})
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
    # Analytics hold post_lock for up to half a minute: wait for it first, never
    # while holding `lock` or calibrating_lock, which the event loop takes for
    # upload chunks and pitch setups (every request would stall). Nothing takes
    # post_lock or label_lock while holding those, so this order cannot deadlock.
    with post_lock, label_lock:
        # Never remove files beneath a live upload, inference or calibration.
        with calibrating_lock, lock:
            if job_id == active or job_id in calibrating:
                raise HTTPException(409, "Cancel processing and wait for it to stop before deleting")
            # A concurrent delete may have removed it during the wait.
            shutil.rmtree(directory, ignore_errors=True)
            for key in list(_prepared):
                if key[0] == job_id:
                    del _prepared[key]
            with _slim_lock:
                for key in list(_slim):
                    if key[0] == job_id:
                        del _slim[key]
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
        if not status.get("maxSeconds"):
            check_full_match()
        # A retry wants the worker like any start: a stale upload reservation gives way.
        release_stale_upload(contended=True)
        if active is not None:
            raise HTTPException(409, "Another video is being analysed. Try again when it finishes.")
        if not (directory / "video").is_file() or expired(status) or not status.get("video"):
            raise HTTPException(409, "No complete upload is available. Upload the video again.")
        # A graceful shutdown (redeploy) or a GPU refusal before any compute is not
        # a failed attempt. Crashes still count: a video that kills the worker must
        # not crash-loop it at our expense.
        free = not_an_attempt(status)
        if status.get("attempts", 1) >= 3 and not free:
            raise HTTPException(409, "This video has stopped three times. Upload it again, or test a shorter section first.")
        check_daily_budget()
        check_models(status.get("profile", "general"), status.get("ballSearch", "exhaustive"))
        event = threading.Event()
        for key in ("interruptedBy", "refusedBy", "error"):
            status.pop(key, None)
        # Every retry is a new run: it is counted, also after a shutdown.
        status.update(
            status="processing", stage="Retrying saved upload", progress=0,
            attempts=status.get("attempts", 1) + (0 if free else 1), countedAt=record_start(),
        )
        _mark_deployment(status)
        write_status(directory, status)
        active = job_id
        cancellations[job_id] = event
        pool.submit(work, directory, status, event)
        return public(status)


def kit_colours(path):
    """A finished result's two kit colours (Kit A first), or None."""
    teams = (_read_json(path, {}) or {}).get("teams")
    colours = [t.get("colour") for t in teams if isinstance(t, dict)] if isinstance(teams, list) else []
    if len(colours) == 2 and all(isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in colours):
        return colours
    return None


@app.post("/jobs/{job_id}/rerun")
def rerun_job(job_id: str, request: Request):
    """Start a fresh report from private saved footage; preserve the original report."""
    global active
    mode = request.query_params.get("mode", "section")
    request_id = request.query_params.get("requestId", "")
    if mode not in ("section", "full") or not re.fullmatch(r"[a-f0-9-]{32,36}", request_id):
        raise HTTPException(400, "Choose a section or full match and a valid request ID")
    if mode == "full":
        check_full_match()
    try:
        start = int(request.query_params.get("start", "0"))
    except ValueError as exc:
        raise HTTPException(400, "Test start must be a whole number of seconds") from exc
    # Same choices as an upload (create): a player-load job can be rerun with the ball model.
    search = request.query_params.get("search")
    if search is not None and search not in ("exhaustive", "adaptive", "none"):
        raise HTTPException(400, "Invalid ball search mode")
    # Read before taking the lock: a full match's result is large.
    reference = kit_colours(folder(job_id) / "result.json")
    with lock:
        source = folder(job_id)
        previous = load_status(source)
        search = search or previous.get("ballSearch", "exhaustive")
        digest = f"{mode}:{start}:{search}"
        # Owner authorization runs before this endpoint, including retry replies.
        for path in ROOT.glob("*/status.json"):
            existing = _read_json(path, {})
            if not isinstance(existing, dict):
                continue
            if existing.get("sourceJobId") == job_id and existing.get("rerunRequestId") == request_id:
                # Reruns started before the search option stored "mode:start".
                legacy = existing.get("rerunDigest") == f"{mode}:{start}" and existing.get("ballSearch") == search
                if existing.get("rerunDigest") != digest and not legacy:
                    raise HTTPException(409, "Rerun request ID already used for different options")
                return public(load_status(path.parent))
        if previous["status"] not in ("completed", "failed", "interrupted", "cancelled"):
            raise HTTPException(409, "Wait for this analysis to finish before starting another")
        # Limits cover the whole chain from the upload: a rerun of a rerun does not reset them.
        root = previous.get("rootJobId") or previous.get("sourceJobId") or job_id
        earlier = [
            e for e in (_read_json(p, {}) for p in ROOT.glob("*/status.json"))
            if isinstance(e, dict) and (e.get("rootJobId") or e.get("sourceJobId")) == root
        ]
        full = sum(1 for e in earlier if str(e.get("rerunDigest", "")).startswith("full:"))
        sections = len(earlier) - full
        if mode == "full" and full >= FULL_RERUNS_PER_MATCH:
            raise HTTPException(409, f"This match has already been re-analysed in full {full} times.")
        if mode == "section" and sections >= SECTION_RERUNS_PER_MATCH:
            raise HTTPException(409, f"This match already has {sections} test sections. Delete some before testing another.")
        check_daily_budget()
        metadata = previous.get("video")
        if not metadata or not (source / "video").is_file() or expired(previous):
            raise HTTPException(409, "Saved footage is no longer available. Upload the video again.")
        if not 0 <= start < metadata["duration"] or (mode == "full" and start != 0):
            raise HTTPException(400, "Test start must be inside the video; full matches start at zero")
        # The chosen search decides which weights the rerun needs.
        check_models(previous.get("profile", "general"), search)
        release_stale_upload(contended=True)
        if active is not None:
            raise HTTPException(409, "Another video is being analysed. Try again when it finishes.")
        new_id = uuid.uuid4().hex
        directory = ROOT / new_id
        status = {
            "id": new_id, "sourceJobId": job_id, "rootJobId": root, "title": previous["title"],
            # Same bytes, same retention clock: a rerun never extends how long footage is kept.
            "footageCreatedAt": previous.get("footageCreatedAt", previous.get("uploadedAt", previous["createdAt"])),
            "createdAt": time.time(), "status": "processing", "stage": "Opening saved footage",
            "progress": 0, "video": metadata, "fileSize": (source / "video").stat().st_size,
            "profile": previous.get("profile", "general"), "sampleFps": previous.get("sampleFps", 3),
            "ballSearch": search,
            "maxSeconds": 20 if mode == "section" else None, "startSeconds": start,
            "rerunRequestId": request_id, "rerunDigest": digest,
        }
        if previous.get("owner"):
            status["owner"] = previous["owner"]
        if reference:
            # Kit A / Kit B follow the source report, so its team names carry over.
            status["referenceColours"] = reference
        try:
            directory.mkdir()
            # Both jobs are read-only consumers; deleting either link preserves
            # the other. This avoids another upload and another full-size disk copy.
            try:
                os.link(source / "video", directory / "video")
            except OSError:
                check_space(status["fileSize"])
                shutil.copyfile(source / "video", directory / "video")
            status["countedAt"] = record_start()
            _mark_deployment(status)
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
            refund_start(status.get("countedAt"))
            raise
        return public(status)
