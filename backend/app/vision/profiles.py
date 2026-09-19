"""Explicit model choices: broadcast training does not establish indoor accuracy."""

import os
from pathlib import Path


def model_paths(profile="general"):
    root = Path(__file__).resolve().parents[2]

    def resolve(value):
        path = Path(value)
        return path if path.is_absolute() else root / path

    if profile == "general":
        return (
            resolve(os.getenv("VISION_MODEL_PATH", "models/yolo11s.pt")),
            resolve(os.getenv("VISION_BALL_MODEL_PATH", "models/football-ball.onnx")),
        )
    if profile == "broadcast":
        return (
            root / "models/roboflow-football-player.pt",
            root / "models/roboflow-football-ball.pt",
        )
    raise ValueError("Unknown footage profile")


def available_profiles():
    return [
        name for name in ("general", "broadcast") if all(p.is_file() for p in model_paths(name))
    ]
