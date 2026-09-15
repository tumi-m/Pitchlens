from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

import numpy as np

from app.engine.config import EngineConfig

ClassName = Literal["player", "goalkeeper", "referee", "ball"]


@dataclass
class RawDetection:
    xyxy: tuple[float, float, float, float]
    confidence: float
    name: ClassName
    class_id: int


class Detector:
    def __init__(self, config: EngineConfig):
        self.config = config
        self.backend = "none"
        self._model: Any = None
        self._rf = None
        self._load()

    def _load(self) -> None:
        try:
            from ultralytics import YOLO

            self._model = YOLO(self.config.yolo_weights)
            self.backend = "ultralytics"
            return
        except Exception:
            self._model = None

        import os

        key = os.getenv("ROBOFLOW_API_KEY", "")
        if key:
            from inference_sdk import InferenceHTTPClient

            self._rf = InferenceHTTPClient(
                api_url="https://detect.roboflow.com", api_key=key
            )
            self.backend = "roboflow"
            return
        raise RuntimeError(
            "No detector available. Install ultralytics or set ROBOFLOW_API_KEY."
        )

    def infer(self, frame: np.ndarray) -> list[RawDetection]:
        if self.backend == "ultralytics":
            return self._infer_yolo(frame)
        return self._infer_roboflow(frame)

    def _infer_yolo(self, frame: np.ndarray) -> list[RawDetection]:
        results = self._model.predict(
            frame,
            verbose=False,
            conf=min(self.config.conf_player, self.config.conf_ball),
            imgsz=self.config.imgsz,
            device=self.config.device,
        )
        out: list[RawDetection] = []
        if not results:
            return out
        r0 = results[0]
        names = r0.names or {}
        boxes = getattr(r0, "boxes", None)
        if boxes is None:
            return out
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        for box, conf, cid in zip(xyxy, confs, clss):
            label = str(names.get(int(cid), cid)).lower()
            mapped = _map_label(label)
            if mapped is None:
                continue
            min_conf = self.config.conf_ball if mapped == "ball" else self.config.conf_player
            if float(conf) < min_conf:
                continue
            x1, y1, x2, y2 = map(float, box)
            out.append(RawDetection((x1, y1, x2, y2), float(conf), mapped, int(cid)))
        return out

    def _infer_roboflow(self, frame: np.ndarray) -> list[RawDetection]:
        import os

        project = os.getenv("ROBOFLOW_PROJECT", "football-players-detection-3zvbc")
        version = os.getenv("ROBOFLOW_VERSION", "9")
        result = self._rf.infer(frame, model_id=f"{project}/{version}")
        preds = result.get("predictions", []) if isinstance(result, dict) else []
        out: list[RawDetection] = []
        for p in preds:
            mapped = _map_label(str(p.get("class", "")).lower())
            if mapped is None:
                continue
            w = float(p["width"]); h = float(p["height"])
            cx = float(p["x"]); cy = float(p["y"])
            out.append(RawDetection(
                (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                float(p.get("confidence", 0)),
                mapped,
                int(p.get("class_id", 0)),
            ))
        return out


def _map_label(label: str) -> Optional[ClassName]:
    if label in {"player", "person"}:
        return "player"
    if label in {"goalkeeper", "goalie", "gk"}:
        return "goalkeeper"
    if label in {"referee", "ref"}:
        return "referee"
    if label in {"ball", "sports ball", "soccer ball", "football"}:
        return "ball"
    return None
