"""The real Modal app modules: they import, their local files exist, the GPU function
runs without the worker-only packages, and every place that pins the vision stack
agrees. Skipped where Modal is not installed (the Poetry CI job); the vision smoke
job installs it."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("modal")

BACKEND = Path(__file__).resolve().parents[1]


def test_the_modal_apps_import_with_their_gpu_functions():
    from app.vision import modal_app, modal_train

    assert callable(modal_app.analyse.local) and callable(modal_train.train_ball.local)
    assert modal_train.image is modal_app.image


def test_every_file_copied_into_the_gpu_image_exists():
    from app.vision.modal_app import LOCAL_FILES

    assert LOCAL_FILES
    for source, target in LOCAL_FILES:
        assert Path(source).is_file(), source
        assert target.startswith("/root/")


# Run in a fresh interpreter: the GPU image installs the engine's packages only,
# so the engine must never import what only the worker has. The function fetches
# the fixture over HTTP and runs the real player model on it (CPU here).
GPU_RUN = r"""
import gzip, http.server, importlib.abc, json, os, sys, threading
from pathlib import Path

BLOCKED = {"fastapi", "starlette", "httpx", "uvicorn", "imageio_ffmpeg", "yt_dlp"}
DETECTING = "Detecting players and tracking kits"

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is not installed in the GPU image")
        return None

sys.meta_path.insert(0, Block())
video = Path(sys.argv[1]).read_bytes()

class Serve(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(video)))
        self.end_headers()
        self.wfile.write(video)

    def log_message(self, *args):
        pass

httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Serve)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
from app.vision import modal_app

url = f"http://127.0.0.1:{httpd.server_port}/jobs/{'a' * 32}/video"
stages = []
for item in modal_app.analyse.local(url, "test-token", "general", 3, max_seconds=2, ball_search="none"):
    stages.append(item.get("stage"))
    if "result_gz" in item:
        result = json.loads(gzip.decompress(item["result_gz"]))
        print("result", len(result["frames"]), "frames")
        sys.exit(0)
    if "error" in item:
        # The short fixture has nobody on a pitch: an honest end once the
        # engine has run the player model on its frames (as smoke_player_load).
        if item.get("user") and "No on-pitch players detected" in item["error"] and DETECTING in stages:
            print("detected", stages.count(DETECTING), "progress items")
            sys.exit(0)
        sys.exit(f"GPU function failed: {item} after {stages}")
sys.exit(f"no result after {stages}")
"""


def test_the_gpu_function_runs_without_worker_only_packages(tmp_path):
    if not (BACKEND / "models" / "yolo11s.pt").is_file():
        pytest.skip("backend/models/yolo11s.pt is not installed")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(BACKEND),
        "VISION_GPU_WORKDIR": str(tmp_path / "job"),
        "VISION_DATA_DIR": str(tmp_path / "data"),
        "VISION_DEVICE": "cpu",
        "YOLO_OFFLINE": "1",
        "YOLO_CONFIG_DIR": str(tmp_path / "ultralytics"),
        "MPLCONFIGDIR": str(tmp_path / "matplotlib"),
    }
    done = subprocess.run(
        [sys.executable, "-c", GPU_RUN, str(BACKEND / "tests" / "fixtures" / "vfr-30-15.mp4")],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-4000:]
    assert done.stdout.startswith(("result ", "detected "))


PINNED = ("torch", "torchvision", "opencv", "onnxruntime", "ultralytics", "numpy", "scipy", "scikit-learn")


def _pins(text):
    pins = {}
    for name, version in re.findall(r"([A-Za-z][A-Za-z0-9_.\-]*)==([0-9][0-9A-Za-z.+]*)", text):
        name = name.lower()
        if name in ("onnxruntime-gpu",):
            name = "onnxruntime"
        if name in ("opencv-python", "opencv-python-headless"):
            name = "opencv"
        pins.setdefault(name, set()).add(version)
    return pins


def test_the_vision_stack_is_pinned_the_same_everywhere():
    sources = {
        "requirements-vision.txt": BACKEND / "requirements-vision.txt",
        "vision.Dockerfile": BACKEND / "vision.Dockerfile",
        "modal_app.py": BACKEND / "app" / "vision" / "modal_app.py",
        "vision-smoke.yml": BACKEND.parent / ".github" / "workflows" / "vision-smoke.yml",
    }
    found = {label: _pins(path.read_text()) for label, path in sources.items() if path.is_file()}
    # The two complete lists: the worker image's and the GPU image's.
    for label in ("requirements-vision.txt", "modal_app.py"):
        assert set(PINNED) <= set(found[label]), (label, set(PINNED) - set(found[label]))
    assert "opencv" in found["vision.Dockerfile"] and "torch" in found["vision.Dockerfile"]
    for name in PINNED:
        versions = {label: pins[name] for label, pins in found.items() if name in pins}
        assert len(set().union(*versions.values())) == 1, (name, versions)
