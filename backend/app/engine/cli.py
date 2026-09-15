"""Analyze a local match video."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.engine.config import EngineConfig, PitchSpec
from app.engine.pipeline import LocalCVPipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pitchlens local computer-vision engine")
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, default=Path("outputs/analysis.json"))
    parser.add_argument("--stride", type=int, default=None)
    parser.add_argument("--weights", type=str, default=None)
    parser.add_argument("--device", type=str, default=None)
    parser.add_argument("--eleven", action="store_true")
    parser.add_argument("--landmarks", type=Path)
    parser.add_argument("--home-defends-right", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = EngineConfig()
    if args.stride:
        cfg.frame_stride = args.stride
    if args.weights:
        cfg.yolo_weights = args.weights
    if args.device:
        cfg.device = args.device
    if args.eleven:
        cfg.pitch = PitchSpec.eleven()
    landmarks = json.loads(args.landmarks.read_text()) if args.landmarks else None

    def progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}", flush=True)

    result = LocalCVPipeline(cfg, progress=progress).run(
        args.video,
        landmarks=landmarks,
        home_defends_left=not args.home_defends_right,
        write_json=args.out,
    )
    print(json.dumps({
        "out": str(args.out),
        "space": result["space"],
        "frames": result["video"]["framesProcessed"],
        "tracks": len(result["tracks"]),
        "events": len(result["events"]),
        "possession": result["possession"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
