"""Faint-object ball recovery: track-before-detect for small, low-resolution balls.

A ball at 360p is a handful of pixels that seldom passes a detector's confidence
bar in any single frame, yet it traces a smooth path across frames. Astronomers
find faint moving objects the same way ("shift-and-stack", track-before-detect):
keep weak evidence and confirm it by consistency over time.

Two candidate sources feed the confirmation step:
  1. the ball detector run at a low threshold (weak neural evidence), and
  2. camera-compensated frame differencing (difference imaging), which finds
     small fast-moving blobs the detector missed.
A chain of candidates on a near-constant-velocity path over several frames is
promoted to observed ball positions. Isolated blips stay unconfirmed.
Gaps of at most `max_bridge` seconds inside a confirmed chain are bridged with
positions marked `inferred: True`; they are never reported as observed.
"""

import math

import cv2
import numpy as np


def ball_size_prior(players, frame_height):
    """Expected ball diameter in pixels: about 1/8 of a player's height."""
    heights = [p["box"][3] - p["box"][1] for p in players] if players else []
    if heights:
        return max(3.0, float(np.median(heights)) / 8)
    return max(3.0, frame_height / 70)


def difference_candidates(previous, current, matrix, players, diameter, limit=8, mask=None):
    """Small bright blobs that moved against the camera-compensated background.

    `mask` is the detected playing surface; candidates outside a generously
    dilated version of it (spectators, boards, walls) are discarded.
    """
    if previous is None or matrix is None:
        return []
    h, w = current.shape[:2]
    warped = cv2.warpAffine(previous, matrix, (w, h), flags=cv2.INTER_LINEAR)
    grey_now = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    grey_prev = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    # Signed difference: where the picture got brighter. A light ball arriving
    # lights up its new position; with an absolute difference the spot it left
    # behind lit up too and the ghost trailed every promoted position by a frame.
    diff = cv2.subtract(grey_now, grey_prev)
    # Border pixels the warp could not fill look like motion.
    diff[:3, :] = 0
    diff[-3:, :] = 0
    diff[:, :3] = 0
    diff[:, -3:] = 0
    # Players move too: blank their boxes so limbs are not mistaken for balls.
    for p in players:
        x1, y1, x2, y2 = [int(v) for v in p["box"]]
        cv2.rectangle(diff, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), 0, -1)
    if mask is not None and mask.any():
        k = max(3, int(h * 0.08))
        region = cv2.dilate(mask, np.ones((k, k), np.uint8))
        diff[region == 0] = 0
    threshold = max(24, int(np.percentile(diff, 99.5)))
    _, binary = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    k = max(1, int(diameter / 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    found = []
    lo, hi = diameter * 0.4, diameter * 2.2
    for i in range(1, count):
        bw, bh, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        size = max(bw, bh)
        if not lo <= size <= hi or area < 0.35 * bw * bh:
            continue
        # Roundness: a ball's blob is compact; a limb or line is elongated.
        if min(bw, bh) / max(bw, bh) < 0.4:
            continue
        cx, cy = centroids[i]
        energy = float(diff[stats[i, cv2.CC_STAT_TOP] : stats[i, cv2.CC_STAT_TOP] + bh,
                            stats[i, cv2.CC_STAT_LEFT] : stats[i, cv2.CC_STAT_LEFT] + bw].mean())
        found.append(
            {
                "x": round(float(cx), 1),
                "y": round(float(cy), 1),
                "box": [round(float(cx - bw / 2), 1), round(float(cy - bh / 2), 1),
                        round(float(cx + bw / 2), 1), round(float(cy + bh / 2), 1)],
                # Deliberately weak: only a consistent trajectory can promote it,
                # and it never outranks a detector response the tracker accepts.
                "confidence": round(min(0.15, 0.05 + energy / 510), 3),
                "source": "motion",
            }
        )
    found.sort(key=lambda c: -c["confidence"])
    return found[:limit]


def strong_candidates(candidates, threshold=0.15):
    """Candidates allowed to drive the online ball tracker frame by frame.

    Motion blobs are kept only for the batch confirmation step: fed straight to
    the tracker they made every sock and shadow an 'observed' ball.
    """
    return [
        c
        for c in candidates
        if c["confidence"] >= threshold and c.get("source", "detector") != "motion"
    ]


def _warp_point(xy, matrix):
    return matrix @ np.array([xy[0], xy[1], 1.0])


def confirm_chains(frames, matrices, diagonal, sample_fps=6.0, window=6, min_length=3, min_evidence=0.55):
    """Track-before-detect over all sampled frames.

    `matrices[i]` maps frame i-1 coordinates into frame i coordinates. A chain is
    a sequence of candidates in consecutive-ish frames whose camera-compensated
    positions follow a near-constant velocity. Chains with at least `min_length`
    members and summed evidence >= `min_evidence` are confirmed.
    Returns a list of {frame index -> candidate} promotions.

    Every comparison happens in the *current* frame's pixels: earlier candidates
    are carried forward through at most `window` camera transforms. A global
    reference frame would inherit every zoom the camera ever made, which turns
    pixel gates into nonsense after a few minutes of broadcast footage.
    """
    n = len(frames)
    identity = np.eye(3)
    local = []
    for i in range(n):
        m = matrices[i] if i > 0 and matrices[i] is not None else None
        local.append(np.vstack([m, [0, 0, 1]]) if m is not None else identity)
    points, conf = [], []
    for f in frames:
        c = f.get("ballCandidates", [])
        points.append(np.array([[k["x"], k["y"], 1.0] for k in c]) if c else np.zeros((0, 3)))
        conf.append(np.array([k["confidence"] for k in c]) if c else np.zeros(0))

    # Physical plausibility in image space. A hard pass crosses a small pitch in
    # about a second, i.e. up to ~0.15 of the frame diagonal per sampled frame
    # at 6 fps; anything faster is a jump between unrelated blobs.
    # Sparser sampling means more movement between samples: scale the gates.
    per_sample = max(1.0, 6.0 / max(sample_fps, 0.5))
    max_step = diagonal * 0.15 * per_sample
    gate = diagonal * 0.03 * per_sample  # prediction tolerance per frame of separation
    # (frame, candidate) -> (score, length, previous key, velocity in own frame's pixels)
    best = {}
    order = []
    # Per-frame arrays for the dynamic programme (a 40-minute match has ~300k
    # candidates; a Python inner loop took minutes, this takes seconds).
    scores = [None] * n
    lengths = [None] * n
    velocities = [None] * n
    for i in range(n):
        m = len(points[i])
        if not m:
            continue
        # transform[back] maps frame i-back coordinates into frame i coordinates.
        transform = [identity]
        for back in range(1, window + 1):
            k = i - back
            if k < 0 or frames[k]["scene"] != frames[i]["scene"]:
                break
            transform.append(transform[-1] @ local[k + 1])
        here = points[i][:, :2]
        score = conf[i].copy()
        length = np.ones(m, int)
        prev = [None] * m
        velocity = np.full((m, 2), np.nan)
        for back in range(1, len(transform)):
            k = i - back
            if scores[k] is None:
                continue
            T = transform[back]
            there = (points[k] @ T.T)[:, :2]
            v0 = velocities[k]
            has_v = ~np.isnan(v0[:, 0])
            # Velocity is a direction: rotate/scale it, never translate it.
            carried = np.where(has_v[:, None], v0 @ T[:2, :2].T, 0.0)
            predicted = there + carried * back
            tolerance = np.where(has_v, gate * back, gate * back + max_step * 0.6)
            step = here[:, None, :] - there[None, :, :]
            ok = np.linalg.norm(step, axis=2) <= max_step * back
            ok &= np.linalg.norm(here[:, None, :] - predicted[None], axis=2) <= tolerance[None, :]
            candidate = np.where(ok, scores[k][None, :] + conf[i][:, None] - 0.02 * back, -np.inf)
            for j in range(m):
                l = int(np.argmax(candidate[j]))
                if candidate[j, l] > score[j]:
                    score[j] = candidate[j, l]
                    length[j] = lengths[k][l] + 1
                    prev[j] = (k, l)
                    velocity[j] = step[j, l] / back
        scores[i], lengths[i], velocities[i] = score, length, velocity
        for j in range(m):
            best[(i, j)] = (float(score[j]), int(length[j]), prev[j])
            order.append((i, j))
    # Walk chains from their strongest end, longest first, without reuse.
    promoted = {}
    used = set()
    for key in sorted(order, key=lambda k: -best[k][0]):
        if key in used or best[key][1] < min_length or best[key][0] < min_evidence:
            continue
        chain = []
        cursor = key
        while cursor is not None and cursor not in used:
            chain.append(cursor)
            cursor = best[cursor][2]
        if len(chain) < min_length:
            continue
        # A stronger chain already owns one of these frames: a parallel, weaker
        # path (a sock, a second ball) must not replace it.
        if any(i in promoted for i, _ in chain):
            continue
        members = [frames[i]["ballCandidates"][j] for i, j in chain]
        neural = [m for m in members if m.get("source", "detector") != "motion"]
        # A path made only of motion blobs needs to be long to count: feet,
        # socks and hands also make small bright moving blobs.
        if not neural and len(chain) < 2 * min_length:
            continue
        if neural and max(m["confidence"] for m in neural) < 0.1 and len(chain) < min_length + 2:
            continue
        # Evidence of the chain actually walked (it may have stopped at a used key).
        span = chain[0][0] - chain[-1][0]
        evidence = sum(m["confidence"] for m in members) - 0.02 * (span - (len(chain) - 1))
        if evidence < min_evidence:
            continue
        mean_conf = sum(m["confidence"] for m in members) / len(members)
        chain_conf = round(min(0.6 if neural else 0.4, mean_conf + 0.04 * len(chain)), 3)
        for i, j in chain:
            used.add((i, j))
            candidate = dict(frames[i]["ballCandidates"][j])
            candidate["confidence"] = max(candidate["confidence"], chain_conf)
            candidate["chainEvidence"] = round(float(evidence), 3)
            candidate["chainLength"] = len(chain)
            promoted[i] = candidate
    return promoted


def _attended(frame, ball):
    """A team player stands over the ball (a set piece), so it is no marking."""
    for p in frame.get("players", []):
        if p.get("team") not in (0, 1):
            continue
        x1, y1, x2, y2 = p["box"]
        scale = max(y2 - y1, 1)
        if min(math.hypot(ball["x"] - x, ball["y"] - y2) for x in (x1, (x1 + x2) / 2, x2)) <= scale:
            return True
    return False


def drop_static_balls(frames, matrices, diagonal, sample_fps, seconds=3.0):
    """A weakly evidenced 'ball' that has not moved for several seconds (after
    cancelling camera motion) is a pitch marking, a logo or a stray object.

    Only chain-recovered or sub-threshold positions are eligible: a confident
    detector observation of a ball at rest (kick-off, corner, penalty) is kept,
    and so is any still ball with a player standing over it. Drift is measured
    from where the run started, so a slowly rolling ball is not "static".
    """
    limit = max(2, int(round(seconds * sample_fps)))
    tolerance = diagonal * 0.004
    run = []  # indices of consecutive frames with a near-stationary weak ball
    dropped = 0

    def flush():
        nonlocal dropped
        if len(run) >= limit and sum(_attended(frames[i], frames[i]["ball"]) for i in run) * 2 < len(run):
            for i in run:
                frames[i]["ball"] = None
                dropped += 1
        run.clear()

    anchor = None  # start of the run, carried through each frame's camera transform
    for i, f in enumerate(frames):
        b = f["ball"]
        weak = b is not None and not b.get("inferred") and (b.get("recovered") or b["confidence"] < 0.15)
        if not weak:
            flush()
            anchor = None
            continue
        here = np.array([b["x"], b["y"]])
        if anchor is not None and matrices[i] is not None and frames[i - 1]["scene"] == f["scene"]:
            anchor = _warp_point(anchor, matrices[i])
            if np.linalg.norm(here - anchor) <= tolerance:
                if not run:
                    run.append(i - 1)
                run.append(i)
                continue
        flush()
        anchor = here
    flush()
    return dropped


def recover_ball(frames, matrices, diagonal, sample_fps, max_bridge=0.5):
    """Fill missing ball observations from confirmed chains; bridge short gaps.

    Returns counts: {"recovered": n, "inferred": n, "droppedStatic": n}.
    """
    promoted = confirm_chains(frames, matrices, diagonal, sample_fps)
    recovered = 0
    for i, candidate in promoted.items():
        if frames[i]["ball"] is None:
            frames[i]["ball"] = {**candidate, "trackId": -1, "observed": True, "recovered": True}
            recovered += 1
    # Bridge gaps between observed positions that are close in time and space.
    inferred = 0
    # `max_bridge` is the unobserved time allowed inside a chain.
    max_gap = max(1, int(round(max_bridge * sample_fps))) + 1
    last = None
    for i, f in enumerate(frames):
        if f["ball"] is not None and not f["ball"].get("inferred"):
            if last is not None and 1 < i - last <= max_gap and frames[last]["scene"] == f["scene"]:
                a, b = frames[last]["ball"], f["ball"]
                # The online tracker's own gate: never join what it refused to join.
                reach = diagonal * (0.025 + 0.3 * (i - last) / sample_fps)
                if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= reach:
                    for k in range(last + 1, i):
                        s = (k - last) / (i - last)
                        frames[k]["ball"] = {
                            "x": round(a["x"] + (b["x"] - a["x"]) * s, 1),
                            "y": round(a["y"] + (b["y"] - a["y"]) * s, 1),
                            "box": None,
                            "confidence": round(min(a["confidence"], b["confidence"]) * 0.8, 3),
                            "trackId": a.get("trackId", -1),
                            "observed": False,
                            "inferred": True,
                        }
                        inferred += 1
            last = i
    dropped = drop_static_balls(frames, matrices, diagonal, sample_fps)
    return {"recovered": recovered, "inferred": inferred, "droppedStatic": dropped}
