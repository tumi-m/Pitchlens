"""Send a job's heavy inference to a Modal GPU; the worker keeps everything else."""

import gzip
import logging
import math
import os
import queue
import threading
import time
import zlib
from pathlib import Path

from app.vision.access import MAX_GRANT_SECONDS, model_grant, video_grant


class GPUUnavailable(Exception):
    """Modal could not run the job (auth, quota, image build, no GPU in time, a cut
    connection): the CPU may take over."""


class GPUEngineError(RuntimeError):
    """The engine itself failed on the GPU: the CPU would fail the same way."""


class GPUTimeout(ValueError):
    """Modal stopped the run at its time limit. Not GPUUnavailable: the GPU time was
    billed, a retry would hit the same limit and the CPU would take many hours."""


# modal_app.analyse's own timeout, and Modal's ceiling for one function call.
MODAL_TIMEOUT = 3 * 3600
MODAL_MAX_TIMEOUT = 24 * 3600


def gpu_timeout_seconds(video, sample_fps, ball_search, max_seconds=None, start_seconds=0):
    """Time limit for one GPU run: 1.5x its expected L4 cost plus 15 minutes for
    scheduling and the video download, between MODAL_TIMEOUT and MODAL_MAX_TIMEOUT.
    None without the upload's metadata (analyse's own 3 h then applies).

    Per analysed frame: 0.06 s, 0.0135 s per ball tile (tile_layout) and 0.08 s
    more above 720p. 640x360 (2 tiles) gives 0.087 s, against 87.8 ms measured on
    an L4 (989 s for 11,272 frames); 1080p (12 tiles) about 0.30 s, projected.
    """
    from app.vision.ball import tile_layout

    try:
        fps, width, height = float(video["fps"]), int(video["width"]), int(video["height"])
        duration, sample_fps = float(video["duration"]), float(sample_fps)
        start_seconds, max_seconds = float(start_seconds or 0), float(max_seconds or math.inf)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) and v > 0 for v in (fps, width, height, duration, sample_fps)):
        return None
    stride = max(1, math.ceil(fps / sample_fps))  # the engine's rule
    analysed = max(0.0, min(duration - start_seconds, max_seconds))
    frames = math.ceil(analysed * fps / stride)
    if ball_search == "none":
        tiles = 0
    else:
        _, cols, rows = tile_layout(width, height)
        tiles = cols * rows
    per_frame = 0.06 + 0.0135 * tiles + (0.08 if width * height > 1280 * 720 else 0)
    return min(MODAL_MAX_TIMEOUT, max(MODAL_TIMEOUT, int(1.5 * frames * per_frame + 900)))


def start_seconds_limit():
    """How long a run may wait for its first item from the GPU (scheduling, cold start)."""
    try:
        return float(os.getenv("VISION_GPU_START_SECONDS", "900"))
    except ValueError:
        return 900.0


def _reason(exc):
    return (str(exc) or type(exc).__name__)[:300]


def modal_enabled():
    return (
        os.getenv("VISION_USE_MODAL", "1") != "0"
        and bool(os.getenv("MODAL_TOKEN_ID"))
        and bool(os.getenv("MODAL_TOKEN_SECRET"))
    )


def active_ball_name():
    """File name of the fine-tuned ball model in use ("" for the stock weights)."""
    from app.vision.profiles import CUSTOM_BALL, active_ball_weights

    path = active_ball_weights()
    # Only hash-named fine-tuned weights can be verified on the GPU; any other
    # VISION_BALL_WEIGHTS override applies to CPU runs on this worker only.
    return path.name if path and CUSTOM_BALL.fullmatch(path.name) else ""


def run_model_names(profile, ball_search, ball_name=""):
    """Weight files a GPU run may copy from the worker (GET /models/{name})."""
    from app.vision.profiles import model_paths

    player, ball = model_paths(profile)
    if ball_search == "none":
        return [player.name]
    return sorted({player.name, ball.name} | ({ball_name} if ball_name else set()))


def encrypted(base):
    """Whether the GPU reaches this worker address over TLS (or on this machine, for tests)."""
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"))


def public_base():
    explicit = os.getenv("VISION_PUBLIC_URL", "").rstrip("/")
    if explicit:
        return explicit
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{domain}" if domain else ""


def run_on_modal(
    job_id,
    output,
    token,
    progress,
    cancelled,
    profile,
    sample_fps,
    max_seconds=None,
    start_seconds=0,
    ball_search="exhaustive",
    reference_colours=None,
    reference_job=None,
    timeout_seconds=None,
):
    """Stream progress from the GPU function and write its result to `output`.

    `with app.run()` stays in this (the job's) thread, so the worker is freed only
    once the ephemeral app has stopped (Modal runs one per process). The stream is
    read on a helper thread, so Cancel and shutdown take effect within a second
    even while the GPU sends nothing (scheduling, cold start, model copy, video
    download), and a GPU that never arrives gives way after start_seconds_limit().

    Only Modal and transport failures are GPUUnavailable (the CPU may take over):
    an engine crash is GPUEngineError, a footage problem ValueError, Modal's time
    limit GPUTimeout, and errors from progress() or from writing the result
    locally propagate unchanged. timeout_seconds (gpu_timeout_seconds) raises
    that limit above analyse's own 3 h for a long or detailed run.
    """
    base = public_base()
    if not base:
        raise GPUUnavailable("No public URL for the GPU to fetch the video from.")
    if not encrypted(base):
        # The grants (and the match video) would cross the internet in clear text.
        raise GPUUnavailable("VISION_PUBLIC_URL must be an https:// address for GPU runs.")
    try:
        from app.vision.modal_app import analyse, app
    except Exception as exc:
        raise GPUUnavailable(f"Modal client unavailable: {exc}") from exc
    ball_name = active_ball_name()
    names = run_model_names(profile, ball_search, ball_name)
    inner = None
    try:
        with app.run():
            # Grants last up to four hours, so that Modal re-running a preempted
            # generator input can fetch again; the worker revokes them as soon as
            # this run is no longer on the GPU (engines[job] == "gpu", server.gpu_grant).
            # `token` signs the grants and never leaves this worker.
            expires = time.time() + MAX_GRANT_SECONDS - 60
            args = (
                f"{base}/jobs/{job_id}/video",
                {name: model_grant(token, job_id, name, expires) for name in names},
                profile,
                int(sample_fps),
                max_seconds,
                start_seconds,
                ball_search,
                video_grant(token, job_id, expires),
                ball_name,
                reference_colours,
                reference_job,
            )
            # A Modal refusal here is GPUUnavailable, like entering the app.
            fn = analyse
            if timeout_seconds and timeout_seconds > MODAL_TIMEOUT:
                fn = analyse.with_options(timeout=int(timeout_seconds))
            try:
                payload = _receive(fn, args, progress, cancelled)
            except BaseException as exc:
                inner = exc
                raise
    except Exception as exc:
        if inner is None:
            # Entering or leaving the ephemeral app: auth, quota, image build.
            raise GPUUnavailable(_reason(exc)) from exc
        if exc is not inner:
            # Keep what stopped the run (a Cancel, the engine's error), not the exit's.
            logging.warning("Leaving the Modal app failed: %s", exc)
            raise inner
        raise
    try:
        data = gzip.decompress(payload)
    except (OSError, EOFError, zlib.error) as exc:
        raise GPUUnavailable(f"The GPU result could not be read: {_reason(exc)}") from exc
    output = Path(output)
    temp = output.with_suffix(".tmp")
    try:
        temp.write_bytes(data)
        temp.replace(output)
    except OSError:
        # A full volume here is not the GPU's fault: never re-run on the CPU for it.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _receive(analyse, args, progress, cancelled):
    """Read analyse.remote_gen(*args) on a daemon thread; return the gzip result.

    The job thread never touches the generator: leaving app.run() stops the app,
    which ends the remote call and so the helper thread.
    """
    items = queue.Queue()

    def pump():
        try:
            for item in analyse.remote_gen(*args):
                items.put(("item", item))
            items.put(("end", None))
        except BaseException as exc:  # handed to the job thread (if it is still listening)
            items.put(("error", exc))

    limit = start_seconds_limit()
    began = time.monotonic()
    threading.Thread(target=pump, name="gpu-stream", daemon=True).start()
    arrived = False
    while True:
        if cancelled():
            raise InterruptedError("Analysis cancelled")
        try:
            kind, value = items.get(timeout=0.5)
        except queue.Empty:
            if not arrived and time.monotonic() - began > limit:
                raise GPUUnavailable("No GPU became available in time. Your upload is saved; retry later.") from None
            continue
        if kind == "end":
            raise GPUUnavailable("The GPU run ended without a result.")
        if kind == "error":
            # Modal's FunctionTimeoutError (or another of its TimeoutErrors): checked
            # by name, since modal is imported only when a GPU run starts.
            kind_of = type(value)
            if kind_of.__module__.startswith("modal") and "Timeout" in kind_of.__name__:
                raise GPUTimeout(
                    "This video is too long for one GPU run at this detail level. "
                    "Choose 6 frames/second or analyse a section."
                ) from value
            # A Modal error or a cut stream (requests' ConnectionError and
            # ChunkedEncodingError are OSErrors too): the GPU's, not this volume's.
            raise GPUUnavailable(_reason(value)) from value
        arrived = True
        item = value
        if "error" in item:
            if item.get("user"):
                raise ValueError(item["error"])
            if item.get("engine"):
                raise GPUEngineError(item["error"])
            # Unknown ball model, weights the GPU could not copy: the CPU can still run.
            raise GPUUnavailable(str(item["error"])[:300])
        if "result_gz" in item:
            return item["result_gz"]
        stage = item.pop("stage", None)
        if stage:
            item["stage"] = f"GPU · {stage}"
        progress(**item)
