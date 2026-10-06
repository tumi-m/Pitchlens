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
        bw, bh, area = (
            stats[i, cv2.CC_STAT_WIDTH],
            stats[i, cv2.CC_STAT_HEIGHT],
            stats[i, cv2.CC_STAT_AREA],
        )
        size = max(bw, bh)
        if not lo <= size <= hi or area < 0.35 * bw * bh:
            continue
        # Roundness: a ball's blob is compact; a limb or line is elongated.
        if min(bw, bh) / max(bw, bh) < 0.4:
            continue
        cx, cy = centroids[i]
        energy = float(
            diff[
                stats[i, cv2.CC_STAT_TOP] : stats[i, cv2.CC_STAT_TOP] + bh,
                stats[i, cv2.CC_STAT_LEFT] : stats[i, cv2.CC_STAT_LEFT] + bw,
            ].mean()
        )
        found.append(
            {
                "x": round(float(cx), 1),
                "y": round(float(cy), 1),
                "box": [
                    round(float(cx - bw / 2), 1),
                    round(float(cy - bh / 2), 1),
                    round(float(cx + bw / 2), 1),
                    round(float(cy + bh / 2), 1),
                ],
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


def confirm_chains(
    frames, matrices, diagonal, sample_fps=6.0, window=6, min_length=3, min_evidence=0.55
):
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
                previous_index = int(np.argmax(candidate[j]))
                if candidate[j, previous_index] > score[j]:
                    score[j] = candidate[j, previous_index]
                    length[j] = lengths[k][previous_index] + 1
                    prev[j] = (k, previous_index)
                    velocity[j] = step[j, previous_index] / back
        scores[i], lengths[i], velocities[i] = score, length, velocity
        for j in range(m):
            best[(i, j)] = (float(score[j]), int(length[j]), prev[j])
            order.append((i, j))
    # Walk chains from their strongest end, longest first, without reuse.
    promoted = {}
    used = set()
    chain_id = -2
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
        # Motion alone cannot identify a ball: feet, socks and hands also
        # make small bright moving blobs.
        if not neural:
            continue
        if neural and max(m["confidence"] for m in neural) < 0.1 and len(chain) < min_length + 2:
            continue
        # Evidence of the chain actually walked (it may have stopped at a used key).
        span = chain[0][0] - chain[-1][0]
        evidence = sum(m["confidence"] for m in members) - 0.02 * (span - (len(chain) - 1))
        if evidence < min_evidence:
            continue
        neural_keys = [
            (k, j)
            for k, j in chain
            if frames[k]["ballCandidates"][j].get("source", "detector") != "motion"
            and frames[k]["ballCandidates"][j]["confidence"] >= 0.1
        ]

        def carry(point, source, target):
            transform = np.eye(3)
            lo, hi = sorted((source, target))
            for k in range(lo + 1, hi + 1):
                if matrices[k] is None or not np.isfinite(matrices[k]).all():
                    return None
                transform = np.vstack([matrices[k], [0, 0, 1]]) @ transform
            vector = np.array([point["x"], point["y"], 1.0])
            try:
                return (
                    transform @ vector if source <= target else np.linalg.solve(transform, vector)
                )[:2]
            except np.linalg.LinAlgError:
                return None

        def corroborated_motion(index, point):
            # A neural hit at the start of a 20-second chain cannot authenticate
            # every subsequent moving sock. Require local, bracketing detections.
            reach = max(1, math.ceil(sample_fps * 0.5))
            before = [(k, j) for k, j in neural_keys if 0 < index - k <= reach]
            after = [(k, j) for k, j in neural_keys if 0 < k - index <= reach]
            if not before or not after:
                return False
            ka, ja = max(before)
            kb, jb = min(after)
            a = carry(frames[ka]["ballCandidates"][ja], ka, index)
            b = carry(frames[kb]["ballCandidates"][jb], kb, index)
            if a is None or b is None:
                return False
            fraction = (index - ka) / (kb - ka)
            predicted = a + fraction * (b - a)
            return np.linalg.norm(predicted - [point["x"], point["y"]]) <= max(3, diagonal * 0.005)

        chain_id -= 1
        for i, j in chain:
            candidate = dict(frames[i]["ballCandidates"][j])
            if candidate.get("source") == "motion" and not corroborated_motion(i, candidate):
                continue
            used.add((i, j))
            candidate["recoveryChainId"] = chain_id
            candidate["chainEvidence"] = round(float(evidence), 3)
            candidate["chainLength"] = len(chain)
            promoted[i] = candidate
    return promoted


def _attended(frame, ball):
    """A player stands over the ball, including keepers and uncertain kit labels."""
    for p in frame.get("players", []):
        if p.get("role") not in (None, "person", "player", "goalkeeper"):
            continue
        x1, y1, x2, y2 = p["box"]
        scale = max(y2 - y1, 1)
        if min(math.hypot(ball["x"] - x, ball["y"] - y2) for x in (x1, (x1 + x2) / 2, x2)) <= scale:
            return True
    return False


def _above_heads(frames, points):
    """True when the mark sits above every visible player's head on most frames.

    A scoreboard graphic does this. A dead ball on the grass does not. Frames
    with nobody in them do not count, so a short clip of an empty set piece is
    left alone.
    """
    above = comparable = 0
    for i, _x, y in points:
        heads = [
            p["box"][1]
            for p in frames[i].get("players", [])
            if p.get("box") and len(p["box"]) == 4
        ]
        if not heads:
            continue
        comparable += 1
        if y < min(heads) - 4:
            above += 1
    return comparable >= 3 and above * 2 >= comparable


def reject_static_candidates(frames, matrices, diagonal, sample_fps):
    """Remove persistent unattended distractors before choosing a ball path.

    Test both camera-compensated and image-fixed positions. Optical-flow drift
    previously protected fixed bright specks/overlays from static rejection.
    Short, sparse or attended hypotheses are preserved. This removes neural
    observations, never manufactures a replacement ball or a missing position.
    """
    active, rejected = [], set()
    tolerance = diagonal * 0.004

    def finish(run):
        keys = run["keys"]
        first, last = keys[0][0], keys[-1][0]
        span = frames[last]["t"] - frames[first]["t"] + 1 / sample_fps
        candidates = [frames[i]["ballCandidates"][j] for i, j in keys]
        # One score spike must not protect an otherwise weak, stationary logo.
        weak = np.median([c["confidence"] for c in candidates]) < 0.3
        outside = sum(c.get("outsidePitch", False) for c in candidates) >= 0.6 * len(candidates)
        points = [(i, frames[i]["ballCandidates"][j]["x"], frames[i]["ballCandidates"][j]["y"]) for i, j in keys]
        # A confident mark glued above every head is a graphic, not a dead ball.
        minimum = 3.0 if weak or outside or _above_heads(frames, points) else 20.0
        if span + 1e-6 < minimum or len(keys) / (span * sample_fps) < 0.6:
            return
        attended = sum(_attended(frames[i], frames[i]["ballCandidates"][j]) for i, j in keys)
        if attended * 2 < len(keys):
            rejected.update(keys)

    for i, frame in enumerate(frames):
        matrix = matrices[i]
        same_scene = i > 0 and frame["scene"] == frames[i - 1]["scene"]
        motion_known = matrix is not None and np.isfinite(matrix).all()
        retained = []
        for run in active:
            last_seen = frames[run["keys"][-1][0]]["t"]
            if not same_scene or frame["t"] - last_seen > 0.5 + 1 / sample_fps:
                finish(run)
            else:
                if motion_known:
                    run["anchor"] = _warp_point(run["anchor"], matrix)
                else:
                    run["cameraStatic"] = False
                retained.append(run)
        active = retained
        choices = []
        for k, run in enumerate(active):
            for j, candidate in enumerate(frame.get("ballCandidates", [])):
                if candidate.get("source") == "motion":
                    continue
                xy = np.array([candidate["x"], candidate["y"]])
                camera_error = np.linalg.norm(run["anchor"] - xy) if run["cameraStatic"] else math.inf
                screen_error = np.linalg.norm(run["screen"] - xy) if run["screenStatic"] else math.inf
                distance = min(camera_error, screen_error)
                if distance <= tolerance:
                    choices.append((distance, k, j, camera_error, screen_error))
        used_runs, used_candidates = set(), set()
        for _, k, j, camera_error, screen_error in sorted(choices):
            if k in used_runs or j in used_candidates:
                continue
            run = active[k]
            run["keys"].append((i, j))
            run["cameraStatic"] &= camera_error <= tolerance
            run["screenStatic"] &= screen_error <= tolerance
            used_runs.add(k)
            used_candidates.add(j)
        for j, candidate in enumerate(frame.get("ballCandidates", [])):
            if candidate.get("source") != "motion" and j not in used_candidates:
                xy = np.array([candidate["x"], candidate["y"]])
                active.append({
                    "anchor": xy, "screen": xy, "keys": [(i, j)],
                    "cameraStatic": True, "screenStatic": True,
                })
    for run in active:
        finish(run)
    for i, frame in enumerate(frames):
        frame["ballCandidates"] = [
            c for j, c in enumerate(frame.get("ballCandidates", [])) if (i, j) not in rejected
        ]
    return len(rejected)


def drop_static_balls(frames, matrices, diagonal, sample_fps, seconds=3.0, confident_seconds=20.0):
    """A 'ball' that has not moved for a long time (after cancelling camera
    motion) with nobody standing over it is a pitch marking, a logo or a stray
    object, not the match ball.

    Weak evidence (chain-recovered or detector confidence below 0.3) needs only
    `seconds` of stillness. So does a confident mark that sits above every
    player's head: that is a scoreboard graphic, and a 20-second test is too
    short for the old half-minute rule to catch it. A confident ball on the
    grass needs `confident_seconds`, long enough that a dead ball at a kick-off,
    corner or penalty is kept. A still ball with a team player within one body
    height is always kept. Drift is measured from where the run started, so a
    slowly rolling ball is not "static".
    """
    tolerance = diagonal * 0.004
    run = []  # indices of consecutive frames with a near-stationary ball
    dropped = 0

    def flush():
        nonlocal dropped
        if run:
            # "Confident" means the detector was fairly sure, not merely above
            # the tracker's 0.15 entry bar: a 0.17 blob still for 14 s is a marking.
            weak = all(
                frames[i]["ball"].get("recovered") or frames[i]["ball"]["confidence"] < 0.3
                for i in run
            )
            points = [(i, frames[i]["ball"]["x"], frames[i]["ball"]["y"]) for i in run]
            short = weak or _above_heads(frames, points)
            limit = max(2, int(round((seconds if short else confident_seconds) * sample_fps)))
            attended = sum(_attended(frames[i], frames[i]["ball"]) for i in run)
            if len(run) >= limit and attended * 2 < len(run):
                for i in run:
                    frames[i]["ball"] = None
                    dropped += 1
        run.clear()

    anchor = None  # run start, carried through each frame's camera transform
    screen = None  # run start in image pixels, never warped
    for i, f in enumerate(frames):
        b = f["ball"]
        if b is None or b.get("inferred"):
            flush()
            anchor = screen = None
            continue
        here = np.array([float(b["x"]), float(b["y"])])
        same = anchor is not None and i > 0 and frames[i - 1]["scene"] == f["scene"]
        if same:
            screen_still = np.linalg.norm(here - screen) <= tolerance
            camera_still = False
            matrix = matrices[i]
            if matrix is not None and np.isfinite(np.asarray(matrix, dtype=float)).all():
                anchor = np.asarray(_warp_point(anchor, matrix), dtype=float)
                camera_still = float(np.linalg.norm(here - anchor)) <= tolerance
            # Either frame is enough. A pan used to break the screen-fixed test
            # and a missing camera estimate used to break the pitch-fixed test,
            # so a graphic never reached the time limit.
            if camera_still or screen_still:
                if not run:
                    run.append(i - 1)
                run.append(i)
                continue
        flush()
        anchor = here.copy()
        screen = here.copy()
    flush()
    return dropped


def recover_ball(frames, matrices, diagonal, sample_fps, max_bridge=0.5):
    """Fill missing ball observations from confirmed chains; bridge short gaps.

    Returns counts: {"recovered": n, "inferred": n, "droppedStatic": n}.
    """
    promoted = confirm_chains(frames, matrices, diagonal, sample_fps)
    # An offline hypothesis has its own identity. Reuse an online identity only
    # when a single existing track corroborates that chain; never label all
    # recovered paths -1 and accidentally join different objects.
    anchors = {}
    for i, candidate in promoted.items():
        ball = frames[i]["ball"]
        if ball is not None and ball.get("trackId", -1) >= 0:
            if (
                math.hypot(ball["x"] - candidate["x"], ball["y"] - candidate["y"])
                <= diagonal * 0.01
            ):
                anchors.setdefault(candidate["recoveryChainId"], set()).add(ball["trackId"])
    recovered = 0
    for i, candidate in promoted.items():
        if frames[i]["ball"] is None:
            chain = candidate["recoveryChainId"]
            ids = anchors.get(chain, set())
            track_id = next(iter(ids)) if len(ids) == 1 else chain
            frames[i]["ball"] = {
                **candidate,
                "trackId": track_id,
                "observed": True,
                "recovered": True,
            }
            recovered += 1
    # Reject static hypotheses before interpolation, so guessed positions cannot
    # break a static run or leave ghost bridges after its endpoints are removed.
    dropped = drop_static_balls(frames, matrices, diagonal, sample_fps)
    inferred = 0
    max_gap = max(1, int(round(max_bridge * sample_fps))) + 1
    last = None
    for i, f in enumerate(frames):
        if f["ball"] is None or f["ball"].get("inferred"):
            continue
        if last is not None and 1 < i - last <= max_gap and frames[last]["scene"] == f["scene"]:
            a, b = frames[last]["ball"], f["ball"]
            track_id = a.get("trackId", -1)
            elapsed = f["t"] - frames[last]["t"]
            same_track = track_id != -1 and track_id == b.get("trackId", -1)
            transforms = [np.eye(3)]
            for k in range(last + 1, i + 1):
                matrix = matrices[k]
                if matrix is None or not np.isfinite(matrix).all():
                    break
                transforms.append(np.vstack([matrix, [0, 0, 1]]) @ transforms[-1])
            if (
                same_track
                and 0 < elapsed <= max_bridge + 1 / sample_fps + 1e-6
                and len(transforms) == i - last + 1
            ):
                origin = np.array([a["x"], a["y"], 1.0])
                destination = np.array([b["x"], b["y"], 1.0])
                reach = diagonal * (0.025 + 0.3 * elapsed)
                if np.linalg.norm((transforms[-1] @ origin - destination)[:2]) <= reach:
                    try:
                        endpoint = np.linalg.solve(transforms[-1], destination)
                    except np.linalg.LinAlgError:
                        last = i
                        continue
                    for k in range(last + 1, i):
                        fraction = (frames[k]["t"] - frames[last]["t"]) / elapsed
                        xy = transforms[k - last] @ (origin + fraction * (endpoint - origin))
                        frames[k]["ball"] = {
                            "x": round(float(xy[0]), 1),
                            "y": round(float(xy[1]), 1),
                            "box": None,
                            "confidence": round(min(a["confidence"], b["confidence"]) * 0.8, 3),
                            "trackId": track_id,
                            "observed": False,
                            "inferred": True,
                        }
                        inferred += 1
        last = i
    return {"recovered": recovered, "inferred": inferred, "droppedStatic": dropped}
