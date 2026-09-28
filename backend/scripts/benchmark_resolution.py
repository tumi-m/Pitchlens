"""Reproducible ball-centre diagnostic; a tiny labelled set is NOT an accuracy benchmark."""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / ".ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".vision/matplotlib"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "labels", type=Path, help="JSON with frames: image, ball centre, tolerancePx"
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import cv2
    import torch

    from app.vision.ball import create_ball_detector
    from app.vision.profiles import model_paths

    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    labels = json.loads(args.labels.read_text())["frames"]
    measurements = []
    for profile in ("general", "broadcast"):
        _, path = model_paths(profile)
        if not path.is_file():
            continue
        detector = create_ball_detector(path)
        for height in (360, 240):
            for label in labels:
                if label.get("ball") is None:
                    continue
                frame = cv2.imread(label["image"])
                if frame is None:
                    raise ValueError(f"Missing labelled frame: {label['image']}")
                source_h, source_w = frame.shape[:2]
                width = round(source_w * height / source_h)
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                sx, sy = width / source_w, height / source_h
                # Scale the original centre tolerance; never make matching easier at 240p.
                truth = (label["ball"][0] * sx, label["ball"][1] * sy)
                tolerance = label.get("tolerancePx", 6) * min(sx, sy)
                started = time.monotonic()
                found = detector.detect(frame, threshold=0.15)
                seconds = time.monotonic() - started
                hit = any(math.dist(truth, (d["x"], d["y"])) <= tolerance for d in found)
                measurements.append(
                    {
                        "id": label["id"],
                        "profile": profile,
                        "height": height,
                        "hit": hit,
                        "falsePositives": len(found) - int(hit),
                        "seconds": round(seconds, 4),
                        "detections": found,
                    }
                )
    summary = []
    for profile in ("general", "broadcast"):
        for height in (360, 240):
            rows = [r for r in measurements if r["profile"] == profile and r["height"] == height]
            if rows:
                summary.append(
                    {
                        "profile": profile,
                        "height": height,
                        "frames": len(rows),
                        "hits": sum(r["hit"] for r in rows),
                        "falsePositives": sum(r["falsePositives"] for r in rows),
                        "seconds": round(sum(r["seconds"] for r in rows), 3),
                    }
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "caveat": (
                    "Diagnostic only. Downsampled stills are not native low-resolution "
                    "compressed video. Labels may overlap model training sources. "
                    "No claim of held-out accuracy."
                ),
                "summary": summary,
                "measurements": measurements,
            },
            indent=2,
        )
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
