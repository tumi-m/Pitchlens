"""Explicit model choices: broadcast training does not establish indoor accuracy."""

import os
from pathlib import Path


# Pinned SHA-256 of downloadable weights; a GPU copying weights verifies these.
KNOWN_SHA256 = {
    "yolo11s.pt": "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5",
    "football-ball.onnx": "9fd2031e5bced9dff47a48bae8c6809dd56493124ef8924ae28a3ce26a17a441",
    "roboflow-football-ball.pt": "678fbad05134f19c5094cb8d273812ec9c6691228180d46832551ecf99ed2912",
    "roboflow-football-player.pt": "75b09c377fbf9d0791d23f6cfb689f5aed6eaa43a6818bd1fb884cf7507fffaf",
}


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
