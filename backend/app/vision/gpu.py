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

from app.vision import gpucost
from app.vision.access import MAX_GRANT_SECONDS, model_grant, video_grant


class GPUUnavailable(Exception):
    """Modal could not run the job (auth, quota, image build, no GPU in time, a cut
    connection): the CPU may take over."""


class GPUEngineError(RuntimeError):
    """The engine itself failed on the GPU: the CPU would fail the same way."""


class GPUTimeout(ValueError):
    """Modal stopped a full match at its time limit. Not GPUUnavailable: the GPU time
    was billed, a retry would hit the same limit and the CPU would take many hours."""


# modal_app.analyse's own timeout, and Modal's ceiling for one function call.
MODAL_TIMEOUT = 3 * 3600
MODAL_MAX_TIMEOUT = 24 * 3600


def gpu_timeout_seconds(video, sample_fps, ball_search, max_seconds=None, start_seconds=0, file_size=None):
    """Time limit for one GPU run: 1.5x its expected L4 cost plus 15 minutes for
    scheduling and the video download, between MODAL_TIMEOUT and MODAL_MAX_TIMEOUT.
    None without the upload's metadata (analyse's own 3 h then applies).

    A section (max_seconds) gets at least 30 minutes instead of 3 h, plus a second
    per 2 MB of file_size on top: every GPU run downloads the whole original, and
    Modal's limit includes the container's start. A full match keeps the 3 h
    floor, since the per-frame cost is an L4's and a slower VISION_MODAL_GPU
    takes longer.

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
    if max_seconds != math.inf:
        download = int(file_size) // 2_000_000 if isinstance(file_size, int) and file_size > 0 else 0
        return min(MODAL_MAX_TIMEOUT, max(1800, int(1.5 * frames * per_frame + 900)) + download)
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
    on_run=None,
    tick=None,
):
    """Stream progress from the GPU function and write its result to `output`.

    `with app.run()` stays in this (the job's) thread, so the worker is freed only
    once the ephemeral app has stopped (Modal runs one per process). The stream is
    read on a helper thread, so Cancel and shutdown take effect within a second
    even while the GPU sends nothing (scheduling, cold start, model copy, video
    download), and a GPU that never arrives gives way after start_seconds_limit().

    Only Modal and transport failures are GPUUnavailable (the CPU may take over):
    an engine crash is GPUEngineError, a footage problem ValueError, Modal's time
    limit GPUTimeout (GPUUnavailable for a section, which the CPU can still
    run), and errors from progress() or from writing the result locally
    propagate unchanged. timeout_seconds (gpu_timeout_seconds) replaces
    analyse's own 3 h: longer for a long or detailed run, shorter for a section.

    on_run(record) is called once, however the run ends, with what it cost once
    the GPU was asked (gpu_record); tick() every 30 s while the GPU is asked.
    Both are best-effort, and the record never reaches result.json (the owner
    can download that).
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
    clock = {}  # monotonic: called, first, last (items), exit (app stopped)
    timings = {}
    recorder = _recorded(clock, timings, on_run, os.getenv("VISION_MODAL_GPU", "L4"))
    try:
        with recorder, app.run():
            # Tagged so Modal's billing report (`modal billing report --tag-names
            # pitchlens-job`) gives the invoiced dollars of this very run (docs/COST.md).
            recorder.app_id = getattr(app, "app_id", None)
            try:
                app.set_tags({"pitchlens-job": job_id, "pitchlens-kind": "analysis"})
            except Exception as exc:  # reporting only: never stops a run
                logging.info("Could not tag the Modal app: %s", _reason(exc))
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
            if timeout_seconds and int(timeout_seconds) != MODAL_TIMEOUT:
                fn = analyse.with_options(timeout=int(timeout_seconds))
            try:
                payload = _receive(fn, args, progress, cancelled, clock, timings, tick, section=bool(max_seconds))
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


class _recorded:
    """Times the GPU run (from the Modal call to the app's exit) and hands on_run its record."""

    def __init__(self, clock, timings, on_run, gpu):
        self.clock, self.timings, self.on_run, self.gpu = clock, timings, on_run, gpu
        self.record = None
        self.app_id = None

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        if "called" not in self.clock:
            return False
        try:
            self.clock["exit"] = time.monotonic()
            self.record = gpu_record(self.gpu, self.clock, self.timings, exc)
            if self.app_id:
                self.record["modalAppId"] = self.app_id
            if self.on_run:
                self.on_run(self.record)
        except Exception:  # reporting only: never changes how the run ends
            logging.exception("Could not record the GPU run's cost")
        return False


def gpu_record(gpu, clock, timings, exc=None):
    """What one GPU run cost: worker-side times, the GPU's own phases, an estimate.

    Billed seconds run from the container's start to the app's exit: the time to
    the first item less the GPU's own time before that item is queueing, which
    Modal does not bill. Without the GPU's report the whole wait is counted.
    """
    called = clock["called"]
    end = clock.get("exit", time.monotonic())
    first, last = clock.get("first"), clock.get("last")
    queue_seconds = 0.0
    if first is not None and timings.get("firstYieldSeconds") is not None:
        before_first = float(timings.get("containerUptimeSeconds") or 0) + float(timings["firstYieldSeconds"])
        queue_seconds = max(0.0, first - called - before_first)
    billed = max(0.0, end - called - queue_seconds)
    usd, known = gpucost.estimate(
        gpu, billed, timings.get("cpuSeconds"), timings.get("maxRssBytes"),
        timings.get("downloadBytes") or 0,
    )
    outcome = "completed" if exc is None else type(exc).__name__
    record = {
        "gpu": gpu, "gpuName": timings.get("gpuName"), "outcome": outcome,
        "wallSeconds": round((last if last is not None else end) - called, 1),
        "appExitSeconds": round(end - called, 1),
        "firstItemSeconds": None if first is None else round(first - called, 1),
        "queueSeconds": round(queue_seconds, 1), "seconds": round(billed, 1),
        "usd": usd, "priceKnown": known, "estimated": True,
    }
    phases = {k: timings[k] for k in (
        "containerUptimeSeconds", "importSeconds", "modelCopySeconds", "downloadSeconds", "downloadBytes",
        "engineSeconds", "compressSeconds", "cpuSeconds", "maxRssBytes", "functionSeconds",
    ) if k in timings}
    if phases:
        record["phases"] = phases
    return record


def _receive(analyse, args, progress, cancelled, clock=None, timings=None, tick=None, section=False):
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

    clock = {} if clock is None else clock
    limit = start_seconds_limit()
    began = clock["called"] = time.monotonic()
    ticked = began
    threading.Thread(target=pump, name="gpu-stream", daemon=True).start()
    arrived = False
    while True:
        if tick and time.monotonic() - ticked >= gpucost.HEARTBEAT_SECONDS:
            ticked = time.monotonic()
            try:
                tick()
            except (OSError, ValueError) as exc:  # the spend ledger: never stops a run
                logging.warning("Could not record the GPU run's heartbeat: %s", _reason(exc))
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
                if section:
                    # Its limit is minutes, not the full match's hours: the CPU can run it.
                    raise GPUUnavailable("The GPU run for this section hit its time limit.") from value
                raise GPUTimeout(
                    "This video is too long for one GPU run at this detail level. "
                    "Choose 6 frames/second or analyse a section."
                ) from value
            # A Modal error or a cut stream (requests' ConnectionError and
            # ChunkedEncodingError are OSErrors too): the GPU's, not this volume's.
            raise GPUUnavailable(_reason(value)) from value
        arrived = True
        item = value
        clock.setdefault("first", time.monotonic())
        clock["last"] = time.monotonic()
        if isinstance(item.get("timings"), dict) and timings is not None:
            timings.update(item.pop("timings"))
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
