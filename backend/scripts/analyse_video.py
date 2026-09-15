"""Run the exact same engine as the web app against a local video."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.vision.engine import run_video

p = argparse.ArgumentParser()
p.add_argument("video")
p.add_argument("--output", required=True)
p.add_argument("--fps", type=float, default=3)
p.add_argument("--seconds", type=float)
a = p.parse_args()
run_video(
    a.video,
    a.output,
    progress=lambda **kw: print(json.dumps(kw), flush=True),
    sample_fps=a.fps,
    max_seconds=a.seconds,
)
