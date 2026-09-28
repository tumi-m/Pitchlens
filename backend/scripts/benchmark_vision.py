"""Measure inference on a bounded section; never uploads the source video."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".vision/matplotlib"))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--profile", choices=["general", "small-ball", "broadcast"], default="general"
    )
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--fps", type=float, default=3)
    parser.add_argument("--search", choices=["exhaustive", "adaptive"], default="exhaustive")
    args = parser.parse_args()
    from app.vision.engine import run_video

    result = run_video(
        args.video,
        args.output,
        sample_fps=args.fps,
        max_seconds=args.seconds,
        start_seconds=args.start,
        profile=args.profile,
        ball_search=args.search,
    )
    print(
        json.dumps(
            {
                "performance": result["performance"],
                "metrics": result["metrics"],
                "ballTileCalls": result["ballTileCalls"],
            },
            indent=2,
        )
    )
