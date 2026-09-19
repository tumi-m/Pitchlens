"""Associate observed ball candidates through camera movement, without filling gaps."""

import math

import numpy as np
from scipy.optimize import linear_sum_assignment


class BallTracker:
    def __init__(self, max_gap=0.7):
        self.max_gap = max_gap
        self.tracks = []
        self.next_id = 1
        self.time = None

    def update(self, candidates, t, matrix, shape, cut=False):
        if cut:
            self.tracks = []
        step = max(0, t - self.time) if self.time is not None else 0
        self.time = t
        diagonal = math.hypot(*shape[:2])
        self.tracks = [p for p in self.tracks if t - p["seen"] <= self.max_gap]
        for p in self.tracks:
            p["velocity"] = matrix[:, :2] @ p["velocity"]
            p["xy"] = matrix @ np.r_[p["xy"], 1] + p["velocity"] * step
        costs = np.full((len(self.tracks), len(candidates)), 1e6)
        for i, p in enumerate(self.tracks):
            # Image-space uncertainty grows with elapsed time. This is not speed in metres.
            gate = diagonal * (0.025 + 0.3 * (t - p["seen"]))
            for j, candidate in enumerate(candidates):
                distance = np.linalg.norm(p["xy"] - [candidate["x"], candidate["y"]])
                if distance <= gate:
                    costs[i, j] = distance / gate
        matched = {}
        if costs.size:
            rows, cols = linear_sum_assignment(costs)
            for i, j in zip(rows, cols):
                if costs[i, j] > 1:
                    continue
                p = self.tracks[i]
                xy = np.array([candidates[j]["x"], candidates[j]["y"]])
                # Do not amplify a residual accumulated over a gap into one-frame velocity.
                elapsed = max(0.01, t - p["seen"])
                p["velocity"] += 0.5 * (xy - p["xy"]) / elapsed
                speed = np.linalg.norm(p["velocity"])
                if speed > diagonal:
                    p["velocity"] *= diagonal / speed
                p.update(xy=xy, seen=t, hits=p["hits"] + 1)
                matched[j] = (p, float(costs[i, j]))
        for j, candidate in enumerate(candidates):
            if j not in matched:
                p = {
                    "id": self.next_id,
                    "xy": np.array([candidate["x"], candidate["y"]]),
                    "velocity": np.zeros(2),
                    "seen": t,
                    "hits": 1,
                }
                self.next_id += 1
                self.tracks.append(p)
                matched[j] = (p, 0.0)
        ranked = []
        for j, (p, residual) in matched.items():
            c = candidates[j]
            if p["hits"] < 2 and c["confidence"] < 0.6:
                continue
            score = c["confidence"] + 0.08 * min(5, p["hits"] - 1) - 0.2 * residual
            ranked.append((score, j, p["id"]))
        ranked.sort(reverse=True)
        if not ranked:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.12:
            # Competing balls remain unknown instead of switching arbitrarily.
            return None
        _, j, track_id = ranked[0]
        return {**candidates[j], "trackId": track_id, "observed": True}
