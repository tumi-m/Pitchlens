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


@app.function(gpu=GPU, timeout=3 * 3600, max_containers=2)
def analyse(video_url: str, token: str, profile: str, sample_fps: int):
    """Generator: yields progress dicts, then {"result_gz": bytes}."""
    import gzip
    import queue
    import threading

    import requests

    from app.vision.engine import run_video

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
