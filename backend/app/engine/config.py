from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Optional


PitchKind = Literal["five-a-side", "eleven"]


@dataclass
class PitchSpec:
    kind: PitchKind = "five-a-side"
    length_m: float = 42.0
    width_m: float = 25.0

    @classmethod
    def eleven(cls) -> "PitchSpec":
        return cls(kind="eleven", length_m=105.0, width_m=68.0)

    @classmethod
    def from_env(cls) -> "PitchSpec":
        kind = os.getenv("PITCH_KIND", "five-a-side")
        if kind == "eleven":
            return cls.eleven()
        return cls()


@dataclass
class EngineConfig:
    """Runtime knobs. Prefer env vars so a coach can tune without editing code."""

    frame_stride: int = int(os.getenv("FRAME_SUBSAMPLE", "3"))
    max_seconds: float = float(os.getenv("MAX_ANALYZE_SECONDS", "900"))
    conf_player: float = float(os.getenv("DET_CONF_PLAYER", "0.35"))
    conf_ball: float = float(os.getenv("DET_CONF_BALL", "0.15"))
    possession_radius_m: float = float(os.getenv("POSSESSION_RADIUS_M", "2.0"))
    possession_radius_px: float = float(os.getenv("POSSESSION_RADIUS_PX", "80"))
    shot_cooldown_s: float = 2.0
    pass_min_travel_m: float = 2.0
    pass_min_travel_px: float = 40.0
    yolo_weights: str = os.getenv("YOLO_WEIGHTS", "yolov8n.pt")
    device: str = os.getenv("CV_DEVICE", "cpu")
    imgsz: int = int(os.getenv("YOLO_IMGSZ", "640"))
    pitch: PitchSpec = field(default_factory=PitchSpec.from_env)
    write_annotated: bool = os.getenv("WRITE_ANNOTATED", "false").lower() == "true"
    sample_crops: int = 400

    def processed_fps(self, source_fps: float) -> float:
        return max(1.0, source_fps / max(1, self.frame_stride))
