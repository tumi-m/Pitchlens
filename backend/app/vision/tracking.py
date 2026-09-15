"""Short-term identity association with explicit camera-motion compensation."""

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment


def camera_motion(previous, current, previous_boxes):
    """Estimate background pan/zoom while masking people from feature extraction."""
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    if previous is None:
        return identity, False
    height, width = current.shape[:2]
    scale = 320 / width
    a = cv2.resize(cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY), (320, round(height * scale)))
    b = cv2.resize(cv2.cvtColor(current, cv2.COLOR_BGR2GRAY), a.shape[::-1])
    mask = np.full_like(a, 255)
    for box in previous_boxes:
        x1, y1, x2, y2 = np.array(box) * scale
        cv2.rectangle(mask, (int(x1) - 3, int(y1) - 3), (int(x2) + 3, int(y2) + 3), 0, -1)
    points = cv2.goodFeaturesToTrack(a, maxCorners=200, qualityLevel=0.01, minDistance=5, mask=mask)
    if points is None or len(points) < 10:
        return identity, False
    moved, status, _ = cv2.calcOpticalFlowPyrLK(a, b, points, None, winSize=(21, 21), maxLevel=3)
    if moved is None:
        return identity, False
    valid = status.reshape(-1).astype(bool)
    if valid.sum() < 8:
        return identity, False
    matrix, inliers = cv2.estimateAffinePartial2D(
        points[valid], moved[valid], method=cv2.RANSAC, ransacReprojThreshold=2
    )
    if matrix is None or inliers.mean() < 0.5:
        return identity, False
    zoom = np.linalg.norm(matrix[0, :2])
    if not 0.85 < zoom < 1.18:
        return identity, False
    matrix[:, 2] /= scale
    return matrix.astype(np.float32), True


def warp_box(box, matrix):
    x1, y1, x2, y2 = box
    points = np.array([[x1, y1, 1], [x2, y1, 1], [x2, y2, 1], [x1, y2, 1]]) @ matrix.T
    return np.array(
        [points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()]
    )


def centre(box):
    return (np.array(box[:2]) + np.array(box[2:])) / 2


def overlap(a, b):
    left = np.maximum(a[:2], b[:2])
    right = np.minimum(a[2:], b[2:])
    inter = np.maximum(0, right - left).prod()
    return inter / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


@dataclass
class Track:
    id: int
    box: np.ndarray
    team: int
    seen: float
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))


class MotionTracker:
    """Never invent detections in gaps. Track IDs deliberately remain short-term."""

    def __init__(self):
        self.tracks = []
        self.next_id = 1
        self.last_time = None

    def update(self, observations, t, matrix, cut=False):
        if cut:
            self.tracks = []
        step = t - self.last_time if self.last_time is not None else 0
        self.last_time = t
        self.tracks = [p for p in self.tracks if t - p.seen <= 1.2]
        for p in self.tracks:
            p.box = warp_box(p.box, matrix)
            p.velocity = matrix[:, :2] @ p.velocity
            p.box = p.box + np.tile(p.velocity * step, 2)
        costs = np.full((len(self.tracks), len(observations)), 10000.0)
        for i, p in enumerate(self.tracks):
            predicted = p.box
            for j, d in enumerate(observations):
                if p.team >= 0 and d["team"] >= 0 and p.team != d["team"]:
                    continue
                height = max(12, (predicted[3] - predicted[1] + d["box"][3] - d["box"][1]) / 2)
                distance = np.linalg.norm(centre(predicted) - centre(d["box"])) / height
                if distance > 1.1:
                    continue
                costs[i, j] = 0.65 * distance + 0.35 * (1 - overlap(predicted, d["box"]))
        matched = {}
        if costs.size:
            rows, cols = linear_sum_assignment(costs)
            for i, j in zip(rows, cols):
                if costs[i, j] > 0.9:
                    continue
                p = self.tracks[i]
                d = observations[j]
                elapsed = max(0.05, step)
                residual = (centre(d["box"]) - centre(p.box)) / elapsed
                p.velocity = p.velocity + 0.5 * residual
                p.box = np.array(d["box"])
                p.seen = t
                if d["team"] >= 0:
                    p.team = d["team"]
                matched[j] = p.id
        for j, d in enumerate(observations):
            if j not in matched:
                # Weak detections may sustain a known track, but cannot create a new identity.
                if d.get("confidence", 1.0) < 0.5:
                    continue
                p = Track(self.next_id, np.array(d["box"]), d["team"], t)
                self.next_id += 1
                self.tracks.append(p)
                matched[j] = p.id
        return [{**d, "id": matched[j]} for j, d in enumerate(observations) if j in matched]
