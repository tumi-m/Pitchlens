"""Recover weak observations next to a strongly observed moving ball path."""

import math

import numpy as np


def recover_near_anchors(frames, matrices, diagonal, sample_fps):
    """Use nearby strong detections to resolve weak candidates, in both directions.

    Never extrapolate a reported ball position: every promotion must correspond
    to an existing neural or image-difference candidate. Motion candidates also
    need bracketing neural observations on the same path. Strong existing
    detections, scene cuts and unknown camera motion are not overwritten.
    """
    reach = max(1, math.ceil(0.6 * sample_fps))

    def carry(point, start, end):
        transform = np.eye(3)
        for k in range(min(start, end) + 1, max(start, end) + 1):
            if frames[k]["scene"] != frames[start]["scene"] or matrices[k] is None:
                return None
            m = np.asarray(matrices[k])
            if not np.isfinite(m).all():
                return None
            transform = np.vstack([m, [0, 0, 1]]) @ transform
        xy = np.array([point["x"], point["y"], 1.0])
        try:
            return (transform @ xy if start <= end else np.linalg.solve(transform, xy))[:2]
        except np.linalg.LinAlgError:
            return None

    def strong(ball):
        return (ball is not None and not ball.get("inferred") and not ball.get("recovered")
                and ball.get("confidence", 0) >= 0.6 and ball.get("trackId") is not None)

    proposals = {}
    for a in range(len(frames) - 1):
        first = frames[a].get("ball")
        if not strong(first):
            continue
        for b in range(a + 1, min(len(frames), a + 3)):
            last = frames[b].get("ball")
            dt = frames[b]["t"] - frames[a]["t"]
            if not strong(last) or first["trackId"] != last["trackId"] or not 0 < dt <= 0.4 + 1e-6:
                continue
            start_xy = carry(first, a, b)
            if start_xy is None:
                continue
            speed = np.linalg.norm(np.array([last["x"], last["y"]]) - start_xy) / dt
            if not diagonal * 0.02 <= speed <= diagonal:
                continue  # a still background spot cannot seed a recovery path
            for i in range(max(0, a - reach), min(len(frames), b + reach + 1)):
                away = max(frames[a]["t"] - frames[i]["t"], frames[i]["t"] - frames[b]["t"], 0)
                if away > 0.6 + 1e-6 or strong(frames[i].get("ball")):
                    continue
                x, y = carry(first, a, i), carry(last, b, i)
                if x is None or y is None:
                    continue
                predicted = x + (y - x) * ((frames[i]["t"] - frames[a]["t"]) / dt)
                gate = max(4., diagonal * (0.008 + 0.04 * away))
                old = frames[i].get("ball")
                if old and np.linalg.norm(predicted - [old["x"], old["y"]]) <= gate:
                    continue
                for candidate in frames[i].get("ballCandidates", []):
                    error = np.linalg.norm(predicted - [candidate["x"], candidate["y"]])
                    if error > gate:
                        continue
                    score = error / gate + 0.2 * (1 - candidate["confidence"]) + away
                    proposals.setdefault(i, []).append((score, candidate, first["trackId"], a, b))

    selected = {}
    for i, choices in proposals.items():
        choices.sort(key=lambda row: row[0])
        best = choices[0]
        # Different convincing paths disagree: leave the observation unresolved.
        if any(other[2] != best[2] and other[0] - best[0] < 0.2
               and math.hypot(other[1]["x"] - best[1]["x"], other[1]["y"] - best[1]["y"]) > 4
               for other in choices[1:]):
            continue
        selected[i] = best

    neural = {i: item for i, item in selected.items() if item[1].get("source") != "motion"}
    accepted = dict(neural)
    for i, item in selected.items():
        if item[1].get("source") != "motion":
            continue
        track = item[2]
        before, after = [], []
        for k in range(max(0, i - reach), min(len(frames), i + reach + 1)):
            ball = neural[k][1] if k in neural and neural[k][2] == track else frames[k].get("ball")
            ball_track = track if k in neural and neural[k][2] == track else (ball or {}).get("trackId")
            if ball and ball_track == track and ball.get("source") != "motion" and not ball.get("inferred"):
                if k < i: before.append((k, ball))
                if k > i: after.append((k, ball))
        if not before or not after:
            continue
        ka, ba = before[-1]
        kb, bb = after[0]
        x, y = carry(ba, ka, i), carry(bb, kb, i)
        if x is None or y is None:
            continue
        fraction = (frames[i]["t"] - frames[ka]["t"]) / (frames[kb]["t"] - frames[ka]["t"])
        if np.linalg.norm(x + fraction * (y - x) - [item[1]["x"], item[1]["y"]]) <= max(3, diagonal * 0.005):
            accepted[i] = item

    for i, (_, candidate, track, a, b) in accepted.items():
        frames[i]["ball"] = {**candidate, "trackId": track, "observed": True,
                             "recovered": True, "recoveryMethod": "strong-trajectory",
                             "anchorTimes": [frames[a]["t"], frames[b]["t"]]}
    return len(accepted)
