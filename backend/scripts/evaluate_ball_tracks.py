"""Evaluate exported ball tracks against timestamped, manually labelled centres."""

import argparse
import json
import math
from pathlib import Path


def evaluate(result, annotation):
    frames = result["frames"]
    rows = []
    for label in annotation["labels"]:
        frame = min(frames, key=lambda f: abs(f["t"] - label["t"]), default=None)
        if frame is None or abs(frame["t"] - label["t"]) > 0.025:
            raise ValueError(f"No matching sampled frame at {label['t']}")
        ball = frame.get("ball")
        error = math.hypot(ball["x"] - label["x"], ball["y"] - label["y"]) if ball else None
        rows.append(
            {
                "t": label["t"],
                "errorPixels": round(error, 3) if error is not None else None,
                "matched": error is not None and error <= annotation["tolerancePixels"],
                "inferred": bool(ball and ball.get("inferred")),
                "source": ball.get("source", "detector") if ball else None,
            }
        )
    return {
        "matched": sum(r["matched"] for r in rows),
        "labelled": len(rows),
        "missing": sum(r["errorPixels"] is None for r in rows),
        "wrongLocations": sum(r["errorPixels"] is not None and not r["matched"] for r in rows),
        "note": annotation.get("note"),
        "frames": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()
    annotation = json.loads(args.labels.read_text())
    print(
        json.dumps(
            {str(p): evaluate(json.loads(p.read_text()), annotation) for p in args.results},
            indent=2,
        )
    )
