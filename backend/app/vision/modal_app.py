"""Run the vision engine on a Modal GPU instead of the Railway CPU.

The Railway worker stays in charge of uploads, storage, status and playback.
For each job it starts this app (image builds are cached by Modal), and the GPU
function fetches the video straight from the worker with the service token,
streams progress back, and returns the gzip-compressed result JSON.

Enabled when MODAL_TOKEN_ID and MODAL_TOKEN_SECRET are set on the worker
(VISION_USE_MODAL=0 turns it off). GPU type: VISION_MODAL_GPU (default L4).
"""

import os
from pathlib import Path

import modal

BACKEND = Path(__file__).resolve().parents[2]
GPU = os.getenv("VISION_MODAL_GPU", "L4")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.14.0",
        "torchvision==0.29.0",
        "numpy==1.26.4",
        "scikit-learn==1.9.1",
        "threadpoolctl==3.6.0",
        "scipy==1.17.1",
        "ultralytics==8.4.152",
        "onnxruntime-gpu==1.30.0",
        "requests",
    )
    # The GUI OpenCV build needs libGL; the headless one does not.
    .run_commands(
        "pip uninstall -y opencv-python",
        "pip install --no-deps opencv-python-headless==4.10.0.84",
    )
    .add_local_file(
        BACKEND / "scripts" / "setup_vision.py", "/root/scripts/setup_vision.py", copy=True
    )
    .add_local_file(
        BACKEND / "scripts" / "setup_football.py", "/root/scripts/setup_football.py", copy=True
    )
    .run_commands(
        "python /root/scripts/setup_vision.py",
        # Football-trained player + ball models (optional: Drive may refuse).
        "python /root/scripts/setup_football.py || echo 'football models unavailable'",
    )
    .env(
        {
            "YOLO_CONFIG_DIR": "/tmp/ultralytics",
            "MPLCONFIGDIR": "/tmp/matplotlib",
            "YOLO_OFFLINE": "1",
            "VISION_DEVICE": "cuda",
        }
    )
    .add_local_python_source("app")
)

app = modal.App("pitchlens-vision", image=image)
# Weights copied from the Railway worker persist here between runs.
models_volume = modal.Volume.from_name("pitchlens-models", create_if_missing=True)


@app.function(
    gpu=GPU, timeout=3 * 3600, max_containers=2, volumes={"/cache": models_volume}
)
def analyse(video_url: str, token: str, profile: str, sample_fps: int):
    """Generator: yields progress dicts, then {"result_gz": bytes}."""
    import gzip
    import queue
    import threading

    import requests

    from app.vision.engine import run_video

    from app.vision.profiles import model_paths

    missing = [p for p in model_paths(profile) if not p.is_file()]
    if missing:
        yield {"stage": "GPU: preparing football models", "progress": 1}
        base = video_url.split("/jobs/")[0]
        try:
            for path in missing:
                ensure_model(path, base, token)
        except Exception as exc:
            yield {"error": f"Model unavailable on GPU: {exc}", "user": False}
            return

    workdir = Path("/tmp/job")
    workdir.mkdir(exist_ok=True)
    video = workdir / "video"
    yield {"stage": "GPU: fetching the video", "progress": 1}
    with requests.get(
        video_url, headers={"Authorization": f"Bearer {token}"}, stream=True, timeout=120
    ) as response:
        response.raise_for_status()
        with video.open("wb") as f:
            for chunk in response.iter_content(4 * 1024 * 1024):
                f.write(chunk)

    updates = queue.Queue()
    outcome = {}

    def run():
        try:
            run_video(
                video,
                workdir / "result.json",
                progress=lambda **kw: updates.put(kw),
                profile=profile,
                sample_fps=sample_fps,
            )
        except ValueError as exc:  # footage problem, shown to the user as-is
            outcome["error"] = str(exc)
            outcome["user"] = True
        except Exception as exc:  # reported to the worker, which marks the job failed
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            updates.put(None)

    threading.Thread(target=run, daemon=True).start()
    while (update := updates.get()) is not None:
        yield update
    if "error" in outcome:
        yield {"error": outcome["error"], "user": outcome.get("user", False)}
        return
    yield {"result_gz": gzip.compress((workdir / "result.json").read_bytes())}


def ensure_model(path, base, token):
    """Copy a weight file from the volume cache, or from the worker, verifying its hash."""
    import hashlib
    import shutil

    import requests

    from app.vision.profiles import KNOWN_SHA256

    def digest(p):
        with p.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    expected = KNOWN_SHA256.get(path.name)
    cached = Path("/cache") / path.name
    if not (cached.is_file() and (expected is None or digest(cached) == expected)):
        temporary = cached.with_suffix(".download")
        with requests.get(
            f"{base}/models/{path.name}",
            headers={"Authorization": f"Bearer {token}"},
            stream=True,
            timeout=300,
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as f:
                for chunk in response.iter_content(8 * 1024 * 1024):
                    f.write(chunk)
        if expected and digest(temporary) != expected:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"checksum mismatch for {path.name}")
        temporary.replace(cached)
        try:
            models_volume.commit()
        except Exception:  # cache persistence is an optimisation only
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, path)
