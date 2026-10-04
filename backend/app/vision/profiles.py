"""Explicit model choices: broadcast training does not establish indoor accuracy."""

import json
import os
import re
from pathlib import Path

# Pinned SHA-256 of downloadable weights; a GPU copying weights verifies these.
KNOWN_SHA256 = {
    "yolo11s.pt": "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5",
    "football-ball.onnx": "9fd2031e5bced9dff47a48bae8c6809dd56493124ef8924ae28a3ce26a17a441",
    "roboflow-football-ball.pt": "678fbad05134f19c5094cb8d273812ec9c6691228180d46832551ecf99ed2912",
    "roboflow-football-player.pt": (
        "75b09c377fbf9d0791d23f6cfb689f5aed6eaa43a6818bd1fb884cf7507fffaf"
    ),
}


CUSTOM_BALL = re.compile(r"ball-([a-f0-9]{16})\.pt")


def expected_digest(name):
    """Full SHA-256 for downloadable weights, or the hash prefix a fine-tuned ball model is named by."""
    if name in KNOWN_SHA256:
        return KNOWN_SHA256[name]
    match = CUSTOM_BALL.fullmatch(name)
    return match.group(1) if match else None


def models_dir():
    """Fine-tuned weights live with the match data (a persistent volume), not in the image."""
    return Path(os.getenv("VISION_DATA_DIR", ".vision")).resolve() / "models"


def active_ball_weights():
    """The fine-tuned ball model in use, if one passed its validation (see balltrain)."""
    explicit = os.getenv("VISION_BALL_WEIGHTS")
    if explicit:
        # Set by the GPU job before it copies the file in (missing until then).
        return Path(explicit)
    try:
        registry = json.loads((models_dir() / "ball-model.json").read_text())
    except (OSError, ValueError):
        return None
    name = registry.get("active")
    if not isinstance(name, str) or not CUSTOM_BALL.fullmatch(name):
        return None
    path = models_dir() / name
    return path if path.is_file() else None


def model_paths(profile="general"):
    root = Path(__file__).resolve().parents[2]
    custom = active_ball_weights()

    def resolve(value):
        path = Path(value)
        return path if path.is_absolute() else root / path

    if profile == "general":
        return (
            resolve(os.getenv("VISION_MODEL_PATH", "models/yolo11s.pt")),
            resolve(os.getenv("VISION_BALL_MODEL_PATH", "models/football-ball.onnx")),
        )
    if profile == "small-ball":
        return (
            resolve(os.getenv("VISION_MODEL_PATH", "models/yolo11s.pt")),
            custom or root / "models/roboflow-football-ball.pt",
        )
    if profile == "broadcast":
        return (
            root / "models/roboflow-football-player.pt",
            custom or root / "models/roboflow-football-ball.pt",
        )
    raise ValueError("Unknown footage profile")


def available_profiles():
    return [
        name
        for name in ("general", "broadcast", "small-ball")
        if all(p.is_file() for p in model_paths(name))
    ]
