"""Short-term identity association with explicit camera-motion compensation."""

import math
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
                elapsed = max(0.05, t - p.seen)
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


# ---------------------------------------------------------------------------
# ByteTrack/BoT-SORT-style tracker (default since pipeline 2.2).
#
# Why: the tracker above deletes a player after 1.2 s unseen and gives every
# confident detection a new identity at once. On a 3-minute clip 20% of IDs had
# two or fewer observations and 44% of track ends were followed within 2 s by a
# new track nearby. Following ByteTrack (Zhang et al. 2022) and BoT-SORT
# (Aharon et al. 2022): a constant-velocity Kalman filter with camera-motion
# compensation, a first association pass on confident detections and a second
# on weak ones, a lost-track buffer in seconds (3.5 s at our 5-6 samples/s),
# confirmation after two hits, and kit colour as a soft cost rather than a hard
# block (per-frame colour is unknown for ~16% of detections).


class _Kalman:
    """Constant velocity on (cx, cy, w, h); units: pixels and seconds."""

    def __init__(self, box):
        x1, y1, x2, y2 = box
        self.x = np.array([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, 0, 0, 0, 0], float)
        h = max(8.0, y2 - y1)
        self.P = np.diag([h, h, h, h, 4 * h, 4 * h, h, h]) ** 2 * 0.05

    def compensate(self, matrix):
        """Apply camera motion (affine mapping the previous frame into this one)."""
        A = np.asarray(matrix, float)[:, :2]
        t = np.asarray(matrix, float)[:, 2]
        self.x[:2] = A @ self.x[:2] + t
        self.x[4:6] = A @ self.x[4:6]
        scale = math.sqrt(abs(np.linalg.det(A)))
        self.x[2:4] *= scale
        self.x[6:8] *= scale
        R = np.eye(8)
        R[:2, :2] = A
        R[4:6, 4:6] = A
        self.P = R @ self.P @ R.T

    def predict(self, dt):
        F = np.eye(8)
        F[0, 4] = F[1, 5] = F[2, 6] = F[3, 7] = dt
        h = max(8.0, self.x[3])
        # Players accelerate hard; process noise grows with the gap.
        q = np.diag([0.05 * h, 0.05 * h, 0.02 * h, 0.02 * h, 1.5 * h, 1.5 * h, 0.1 * h, 0.1 * h]) ** 2 * max(dt, 0.05)
        self.x = F @ self.x
        self.x[2:4] = np.maximum(self.x[2:4], 2.0)
        self.P = F @ self.P @ F.T + q

    def update(self, box):
        x1, y1, x2, y2 = box
        z = np.array([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1], float)
        H = np.zeros((4, 8))
        H[0, 0] = H[1, 1] = H[2, 2] = H[3, 3] = 1
        h = max(8.0, z[3])
        R = np.diag([0.06 * h, 0.06 * h, 0.1 * h, 0.1 * h]) ** 2
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - H @ self.x)
        self.P = (np.eye(8) - K @ H) @ self.P

    def box(self):
        cx, cy, w, h = self.x[:4]
        return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])


@dataclass
class _Track:
    id: int
    kf: _Kalman
    team_votes: list
    seen: float
    hits: int = 1
    confirmed: bool = False
    born: float = 0.0
    # Observations seen before confirmation: (that frame's output list, observation).
    pending: list = field(default_factory=list)

    @property
    def team(self):
        a, b = self.team_votes
        if a + b < 2 or max(a, b) < 2 * min(a, b):
            return -1
        return 0 if a > b else 1


class ByteTracker:
    """Two-pass association with a Kalman filter; identities are still short-term."""

    HIGH = 0.4  # confident detections: may start a track
    LOW = 0.1
    LOST_SECONDS = 3.5
    CONFIRM_HITS = 2
    TENTATIVE_SECONDS = 0.8  # a new track must be re-seen within this time
    WEAK_SUSTAIN_SECONDS = 1.5

    def __init__(self):
        self.tracks = []
        self.next_id = 1
        self.last_time = None
        self.interval = None

    def _cost(self, track, obs, t, gate_scale):
        predicted = track.kf.box()
        height = max(10.0, (predicted[3] - predicted[1] + obs["box"][3] - obs["box"][1]) / 2)
        distance = np.linalg.norm(centre(predicted) - centre(obs["box"])) / height
        gap = t - track.seen
        gate = min(3.0, (1.0 + 0.9 * gap) * gate_scale)
        if distance > gate:
            return None
        cost = 0.65 * distance / gate + 0.35 * (1 - overlap(predicted, np.array(obs["box"])))
        team = track.team
        if team >= 0 and obs["team"] >= 0 and team != obs["team"]:
            a, b = track.team_votes
            if a + b >= 3 and max(a, b) >= 2 * min(a, b):
                return None  # an established kit never jumps to the other team's player
            cost += 0.35  # soft while the kit is uncertain: one misread frame should not break a track
        # Prefer tracks seen recently when two compete for one detection.
        return cost + 0.05 * min(gap, 3.0)

    def _associate(self, tracks, observations, t, gate_scale, threshold):
        if not tracks or not observations:
            return {}, list(range(len(tracks))), list(range(len(observations)))
        costs = np.full((len(tracks), len(observations)), 1e6)
        for i, tr in enumerate(tracks):
            for j, obs in enumerate(observations):
                c = self._cost(tr, obs, t, gate_scale)
                if c is not None:
                    costs[i, j] = c
        rows, cols = linear_sum_assignment(costs)
        matched = {}
        for i, j in zip(rows, cols):
            if costs[i, j] <= threshold:
                matched[i] = j
        unmatched_tracks = [i for i in range(len(tracks)) if i not in matched]
        used = set(matched.values())
        unmatched_obs = [j for j in range(len(observations)) if j not in used]
        return matched, unmatched_tracks, unmatched_obs

    def update(self, observations, t, matrix, cut=False):
        if cut:
            self.tracks = []
        dt = t - self.last_time if self.last_time is not None else 0.0
        self.last_time = t
        if dt > 0:
            self.interval = dt if self.interval is None else 0.8 * self.interval + 0.2 * dt
        # In samples, not only seconds: at one frame per second a new track must
        # still get the chance to be seen a second time.
        tentative = max(self.TENTATIVE_SECONDS, 2.5 * (self.interval or 0.0))
        lost = max(self.LOST_SECONDS, 3.5 * (self.interval or 0.0))
        self.tracks = [
            tr
            for tr in self.tracks
            if (tr.confirmed and t - tr.seen <= lost) or (not tr.confirmed and t - tr.born <= tentative)
        ]
        for tr in self.tracks:
            tr.kf.compensate(matrix)
            tr.kf.predict(dt)
        high = [j for j, o in enumerate(observations) if o.get("confidence", 1.0) >= self.HIGH]
        low = [j for j, o in enumerate(observations) if self.LOW <= o.get("confidence", 1.0) < self.HIGH]
        output = {}
        frame_list = []  # this frame's output; earlier frames' lists get late additions

        def accept(tr, obs_index):
            obs = observations[obs_index]
            tr.kf.update(obs["box"])
            tr.seen = t
            tr.hits += 1
            if obs["team"] in (0, 1):
                tr.team_votes[obs["team"]] += 1
            if not tr.confirmed and tr.hits >= self.CONFIRM_HITS:
                tr.confirmed = True
                # The track is real: restore its earlier observations in place.
                for earlier, item in tr.pending:
                    earlier.append({**item, "id": tr.id})
                tr.pending = []
            if tr.confirmed:
                output[obs_index] = tr.id
            else:
                tr.pending.append((frame_list, obs))

        # Pass 1: confident detections against every live track (confirmed first).
        pool = sorted(self.tracks, key=lambda tr: (not tr.confirmed, t - tr.seen))
        matched, left_tracks, left_high = self._associate(pool, [observations[j] for j in high], t, 1.0, 0.9)
        for i, k in matched.items():
            accept(pool[i], high[k])
        # Pass 2: weak detections only sustain existing tracks (never start one).
        remaining = [pool[i] for i in left_tracks if t - pool[i].seen <= self.WEAK_SUSTAIN_SECONDS]
        matched2, _, _ = self._associate(remaining, [observations[j] for j in low], t, 1.0, 0.9)
        for i, k in matched2.items():
            accept(remaining[i], low[k])
        # New tentative tracks from unmatched confident detections.
        for k in left_high:
            obs = observations[high[k]]
            votes = [0, 0]
            if obs["team"] in (0, 1):
                votes[obs["team"]] += 1
            track = _Track(self.next_id, _Kalman(obs["box"]), votes, t, born=t)
            track.pending.append((frame_list, obs))
            self.tracks.append(track)
            self.next_id += 1
        frame_list.extend({**d, "id": output[j]} for j, d in enumerate(observations) if j in output)
        # The same list object is returned and stored by the caller, so late
        # additions (a track confirmed on its next sighting) land in that frame.
        return frame_list
