from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.engine.detect import RawDetection
from app.engine.geometry import foot_point


@dataclass
class TrackedObject:
    track_id: int
    xyxy: tuple[float, float, float, float]
    confidence: float
    name: str
    foot: tuple[float, float]


@dataclass
class FrameTracks:
    frame_index: int
    timestamp: float
    players: list[TrackedObject] = field(default_factory=list)
    ball: Optional[TrackedObject] = None


class PlayerTracker:
    def __init__(self, frame_rate: float):
        self.frame_rate = max(1, int(round(frame_rate)))
        self._sv = None
        try:
            import supervision as sv

            self._sv = sv.ByteTrack(frame_rate=self.frame_rate)
        except Exception:
            self._next_id = 1
            self._prev: list[TrackedObject] = []

    def update(self, detections: list[RawDetection], timestamp: float, frame_index: int) -> FrameTracks:
        people = [d for d in detections if d.name != "ball"]
        balls = [d for d in detections if d.name == "ball"]
        players = self._update_people(people)
        ball = None
        if balls:
            best = max(balls, key=lambda d: d.confidence)
            ball = TrackedObject(
                track_id=0,
                xyxy=best.xyxy,
                confidence=best.confidence,
                name="ball",
                foot=((best.xyxy[0] + best.xyxy[2]) / 2, (best.xyxy[1] + best.xyxy[3]) / 2),
            )
        return FrameTracks(frame_index=frame_index, timestamp=timestamp, players=players, ball=ball)

    def _update_people(self, people: list[RawDetection]) -> list[TrackedObject]:
        if self._sv is not None:
            return self._update_bytetrack(people)
        return self._update_iou(people)

    def _update_bytetrack(self, people: list[RawDetection]) -> list[TrackedObject]:
        import supervision as sv

        if not people:
            self._sv.update_with_detections(sv.Detections.empty())
            return []
        xyxy = np.array([d.xyxy for d in people], dtype=np.float32)
        conf = np.array([d.confidence for d in people], dtype=np.float32)
        cls = np.array([0 if d.name == "player" else 1 for d in people], dtype=int)
        tracked = self._sv.update_with_detections(sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls))
        out: list[TrackedObject] = []
        if tracked.tracker_id is None:
            return out
        for box, conf_i, tid, cls_i in zip(tracked.xyxy, tracked.confidence, tracked.tracker_id, tracked.class_id):
            xyxy_t = tuple(map(float, box))
            out.append(TrackedObject(int(tid), xyxy_t, float(conf_i), "goalkeeper" if int(cls_i) == 1 else "player", foot_point(xyxy_t)))
        return out

    def _update_iou(self, people: list[RawDetection]) -> list[TrackedObject]:
        current: list[TrackedObject] = []
        used_prev: set[int] = set()
        for det in people:
            best_iou, best_prev = 0.0, None
            for prev in self._prev:
                if prev.track_id in used_prev:
                    continue
                iou = _iou(det.xyxy, prev.xyxy)
                if iou > best_iou:
                    best_iou, best_prev = iou, prev
            if best_prev and best_iou >= 0.3:
                tid = best_prev.track_id
                used_prev.add(tid)
            else:
                tid = self._next_id
                self._next_id += 1
            current.append(TrackedObject(tid, det.xyxy, det.confidence, det.name, foot_point(det.xyxy)))
        self._prev = current
        return current


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0
