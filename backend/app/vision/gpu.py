"""Send a job's heavy inference to a Modal GPU; the worker keeps everything else."""

import gzip
import logging
import os
import queue
import threading
import time
import zlib
from pathlib import Path

from app.vision.access import video_grant


class GPUUnavailable(Exception):
    """Modal could not run the job (auth, quota, image build, no GPU in time, a cut
    connection): the CPU may take over."""


class GPUEngineError(RuntimeError):
    """The engine itself failed on the GPU: the CPU would fail the same way."""


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
):
    """Stream progress from the GPU function and write its result to `output`.

    `with app.run()` stays in this (the job's) thread, so the worker is freed only
    once the ephemeral app has stopped (Modal runs one per process). The stream is
    read on a helper thread, so Cancel and shutdown take effect within a second
    even while the GPU sends nothing (scheduling, cold start, model copy, video
    download), and a GPU that never arrives gives way after start_seconds_limit().

    Only Modal and transport failures are GPUUnavailable (the CPU may take over):
    an engine crash is GPUEngineError, a footage problem ValueError, and errors
    from progress() or from writing the result locally propagate unchanged.
    """
    base = public_base()
    if not base:
        raise GPUUnavailable("No public URL for the GPU to fetch the video from.")
    try:
        from app.vision.modal_app import analyse, app
    except Exception as exc:
        raise GPUUnavailable(f"Modal client unavailable: {exc}") from exc
    args = (
        f"{base}/jobs/{job_id}/video",
        token,
        profile,
        int(sample_fps),
        max_seconds,
        start_seconds,
        ball_search,
        video_grant(token, job_id),
        active_ball_name(),
        reference_colours,
        reference_job,
    )
    inner = None
    try:
        with app.run():
            try:
                payload = _receive(analyse, args, progress, cancelled)
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
