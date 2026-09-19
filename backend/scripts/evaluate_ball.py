"""Reproducible centre-localisation diagnostic using independently annotated images.

Labels: {"frames": [{"id": "...", "image": "/path/image.jpg",
"labelStatus": "visible", "ball": [x, y], "tolerancePx": 6}]}.
Uncertain labels are excluded; one target ball per image. This is not COCO mAP.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(root / ".ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(root / ".vision" / "matplotlib"))
sys.path.insert(0, str(root))


def evaluate(labels, predictions):
    totals = {"labelledFrames": 0, "truePositives": 0, "falsePositives": 0, "falseNegatives": 0}
    for label in labels:
        if label.get("labelStatus") != "visible":
            continue
        found = predictions[label["id"]]
        target = label["ball"]
        hit = any(
            math.hypot(d["x"] - target[0], d["y"] - target[1]) <= label["tolerancePx"]
            for d in found
        )
        totals["labelledFrames"] += 1
        totals["truePositives"] += int(hit)
        totals["falsePositives"] += len(found) - int(hit)
        totals["falseNegatives"] += int(not hit)
    tp, fp, fn = (totals[k] for k in ("truePositives", "falsePositives", "falseNegatives"))
    return {
        **totals,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def main():
    import cv2
    import torch
    from ultralytics import settings

    from app.vision.ball import create_ball_detector

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confidence", type=float, default=0.15)
    args = parser.parse_args()
    if not 0 < args.confidence < 1:
        parser.error("confidence must be between zero and one")
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    settings.update({"sync": False})
    labels = json.loads(args.labels.read_text())["frames"]
    detector = create_ball_detector(args.model)
    predictions = {}
    started = time.monotonic()
    for label in labels:
        frame = cv2.imread(label["image"])
        if frame is None:
            raise ValueError(f"Cannot read image for {label['id']}")
        predictions[label["id"]] = detector.detect(frame, args.confidence)
        print(label["id"], flush=True)
    result = {
        "metrics": evaluate(labels, predictions),
        "predictions": predictions,
        "elapsedSeconds": time.monotonic() - started,
        "confidence": args.confidence,
        "modelSha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "labelsSha256": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
        "method": (
            "One visible ball centre per labelled frame; duplicates count as false positives. "
            "Uncertain frames excluded. Not mAP or full-match validation."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
