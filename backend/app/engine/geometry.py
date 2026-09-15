from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass
class Homography:
    matrix: np.ndarray
    residual_px: float
    pitch_length_m: float
    pitch_width_m: float
    valid: bool

    def image_to_pitch(self, x: float, y: float) -> tuple[float, float]:
        if not self.valid or cv2 is None:
            return x, y
        pt = np.array([[[x, y]]], dtype=np.float32)
        out = cv2.perspectiveTransform(pt, self.matrix)[0][0]
        return float(out[0]), float(out[1])


def estimate_homography(
    image_points: Sequence[tuple[float, float]],
    pitch_points: Sequence[tuple[float, float]],
    pitch_length_m: float,
    pitch_width_m: float,
) -> Homography:
    if cv2 is None:
        raise RuntimeError("OpenCV is required for pitch calibration")
    if len(image_points) < 4 or len(image_points) != len(pitch_points):
        return Homography(
            matrix=np.eye(3, dtype=np.float32),
            residual_px=float("inf"),
            pitch_length_m=pitch_length_m,
            pitch_width_m=pitch_width_m,
            valid=False,
        )
    src = np.array(image_points, dtype=np.float32)
    dst = np.array(pitch_points, dtype=np.float32)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None:
        return Homography(
            matrix=np.eye(3, dtype=np.float32),
            residual_px=float("inf"),
            pitch_length_m=pitch_length_m,
            pitch_width_m=pitch_width_m,
            valid=False,
        )
    H_inv = np.linalg.inv(H)
    reprojected = cv2.perspectiveTransform(dst.reshape(-1, 1, 2), H_inv).reshape(-1, 2)
    residual = float(np.mean(np.linalg.norm(reprojected - src, axis=1)))
    inliers = int(mask.sum()) if mask is not None else 0
    return Homography(
        matrix=H.astype(np.float32),
        residual_px=residual,
        pitch_length_m=pitch_length_m,
        pitch_width_m=pitch_width_m,
        valid=inliers >= 4 and residual < 40,
    )


def foot_point(xyxy: Iterable[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = xyxy
    return (float(x1) + float(x2)) / 2.0, float(y2)


def clip_pitch(x: float, y: float, length: float, width: float) -> tuple[float, float]:
    return max(0.0, min(length, x)), max(0.0, min(width, y))


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def toward_goal(
    start: tuple[float, float],
    end: tuple[float, float],
    goal: tuple[float, float],
) -> bool:
    return distance(end, goal) + 0.15 < distance(start, goal)
