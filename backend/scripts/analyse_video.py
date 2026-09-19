"""Run the exact same engine as the web app against a local video."""

import argparse
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(root / ".ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(root / ".vision" / "matplotlib"))
sys.path.insert(0, str(root))
from app.vision.engine import run_video  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("video")
p.add_argument("--output", required=True)
p.add_argument("--fps", type=float, default=3)
p.add_argument("--seconds", type=float)
p.add_argument("--profile", choices=["general", "broadcast"], default="general")
a = p.parse_args()
run_video(
    a.video,
    a.output,
    progress=lambda **kw: print(json.dumps(kw), flush=True),
    sample_fps=a.fps,
    max_seconds=a.seconds,
    profile=a.profile,
)
