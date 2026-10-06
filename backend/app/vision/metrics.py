"""Conservative temporal measurements from observed detections, never filled-in scores."""

import math
from bisect import bisect_left, bisect_right


# Detection error in pixels: a player's foot point and a 3-pixel ball centre
# each wobble by a few pixels. On a 23-pixel player (a whole pitch at 360p)
# that error alone exceeds the 0.55-height control radius, so real control was
# rarely attributed. The slack is in pixels: negligible on large players.
POSSESSION_SLACK_PX = 14
AMBIGUITY_SLACK_PX = 3
# Same rules as the coach sheet: a pan is not running, a partial box is not a scale.
FOOT_NOISE_PX = 3
PLAYER_HEIGHT_M = 1.8
MAX_PLAYER_SPEED = 11
HEIGHT_LO = 0.55
HEIGHT_HI = 1.8


def _median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def apply_camera(x, y, cam):
    if not cam or len(cam) < 6:
        return None
    try:
        a, b, tx, c, d, ty = (float(v) for v in cam[:6])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (a, b, tx, c, d, ty)):
        return None
    return a * x + b * y + tx, c * x + d * y + ty


def compose_camera(outer, inner):
    """`outer` is applied after `inner`. Both are the six vision-file coefficients."""
    a1, b1, tx1, c1, d1, ty1 = inner
    a2, b2, tx2, c2, d2, ty2 = outer
    return (
        a2 * a1 + b2 * c1,
        a2 * b1 + b2 * d1,
        a2 * tx1 + b2 * ty1 + tx2,
        c2 * a1 + d2 * c1,
        c2 * b1 + d2 * d1,
        c2 * tx1 + d2 * ty1 + ty2,
    )


def camera_span(frames, start, end):
    """Affine that carries a point from frame `start` into frame `end`."""
    if end <= start:
        return None
    acc = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    for i in range(start + 1, end + 1):
        if frames[i].get("scene") != frames[i - 1].get("scene"):
            return None
        cam = frames[i].get("camera")
        if not cam or len(cam) < 6:
            return None
        acc = compose_camera(tuple(float(v) for v in cam[:6]), acc)
    return acc


def _team_of(votes):
    best, best_count, unknown, total = -1, 0, 0, 0
    for team, count in votes.items():
        total += count
        if team is None or team < 0:
            unknown += count
        elif count > best_count:
            best, best_count = team, count
    if best < 0:
        return -1
    if best_count >= unknown or best_count >= total * 0.4:
        return best
    return -1


def _step_metres(prev, nxt, frames, median, max_dt):
    t0, x0, y0, scale0, height0, scene0, index0 = prev
    t1, x1, y1, scale1, height1, scene1, index1 = nxt
    dt = t1 - t0
    if dt <= 0 or dt > max_dt or scene0 != scene1:
        return None
    if median <= 0 or not (HEIGHT_LO <= height0 / median <= HEIGHT_HI and HEIGHT_LO <= height1 / median <= HEIGHT_HI):
        return None
    warped = apply_camera(x0, y0, camera_span(frames, index0, index1))
    if warped is None:
        return None
    pixels = math.hypot(x1 - warped[0], y1 - warped[1])
    if pixels < FOOT_NOISE_PX:
        return None
    metres = pixels * (scale0 + scale1) / 2
    if metres / dt > MAX_PLAYER_SPEED:
        return None
    return metres


def player_load(frames):
    """Metres each kit ran. The camera is removed. The ball is not used.

    A step is dropped when the camera could not be estimated, the box is far
    from that track's own height, the foot moved under 3 pixels, or the
    implied speed is over 11 m/s. Track ids are not player names.
    """
    heights = {}
    votes = {}
    for frame in frames:
        seen = set()
        for player in frame.get("players") or []:
            box = player.get("box")
            pid = player.get("id")
            if pid is None or pid in seen or not box or len(box) < 4:
                continue
            seen.add(pid)
            height = float(box[3]) - float(box[1])
            heights.setdefault(pid, []).append(height)
            team = player.get("team", -1)
            votes.setdefault(pid, {})
            votes[pid][team] = votes[pid].get(team, 0) + 1
    medians = {pid: _median(values) for pid, values in heights.items()}
    metres = [0.0, 0.0]
    max_speed = 0.0
    kept = 0
    previous = {}
    for index, frame in enumerate(frames):
        seen = set()
        for player in frame.get("players") or []:
            pid = player.get("id")
            box = player.get("box")
            if pid is None or pid in seen or not box or len(box) < 4:
                continue
            seen.add(pid)
            height = max(float(box[3]) - float(box[1]), 1.0)
            here = (
                float(frame.get("t", index)),
                (float(box[0]) + float(box[2])) / 2,
                float(box[3]),
                PLAYER_HEIGHT_M / height,
                height,
                frame.get("scene", 0),
                index,
            )
            earlier = previous.get(pid)
            if earlier is not None:
                moved = _step_metres(earlier, here, frames, medians.get(pid, 0), 0.45)
                team = _team_of(votes.get(pid, {}))
                if moved is not None and team in (0, 1):
                    metres[team] += moved
                    kept += 1
                    speed = moved / max(here[0] - earlier[0], 1e-6)
                    if speed > max_speed:
                        max_speed = speed
            previous[pid] = here
    return {
        "metres": [round(value, 1) for value in metres],
        "maxSpeed": round(max_speed, 2),
        "steps": kept,
        "method": "player-height-camera-compensated",
        "playerHeightM": PLAYER_HEIGHT_M,
        "footNoisePx": FOOT_NOISE_PX,
    }


def ball_shift(frames):
    """How far the ball moved, in the last frame's pixels.

    When every step has a camera estimate, the first position is carried
    forward so a pan is not mistaken for a pass. Otherwise the raw image
    displacement is used.
    """
    first, last = frames[0]["ball"], frames[-1]["ball"]
    x, y = first["x"], first["y"]
    for frame in frames[1:]:
        cam = frame.get("camera")
        if not cam or len(cam) < 6:
            return math.hypot(last["x"] - first["x"], last["y"] - first["y"])
        a, b, tx, c, d, ty = (float(v) for v in cam[:6])
        x, y = a * x + b * y + tx, c * x + d * y + ty
    return math.hypot(last["x"] - x, last["y"] - y)


def possession_owner(players, ball):
    if not ball:
        return None
    candidates = []
    for p in players:
        if p["team"] not in (0, 1):
            continue
        x1, y1, x2, y2 = p["box"]
        # Perspective-aware image-space proximity; not a metre measurement.
        scale = max(y2 - y1, 1)
        distance = (
            min(math.hypot(ball["x"] - x, ball["y"] - y2) for x in (x1, (x1 + x2) / 2, x2)) / scale
        )
        if distance <= 0.55 + POSSESSION_SLACK_PX / scale:
            candidates.append((distance, p, scale))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return None
    if len(candidates) > 1:
        margin = 0.12 + AMBIGUITY_SLACK_PX / candidates[0][2]
        if candidates[1][0] - candidates[0][0] < margin:
            return None
    return candidates[0][1]


def derive_metrics(frames, sample_fps, duration, start_seconds=0):
    dt = 1 / sample_fps
    # Possession runs may include positions bridged for up to 0.5 s inside a
    # confirmed ball path (`inferred`); pass windows below require observed frames.
    runs = []
    for f in frames:
        owner = possession_owner(f["players"], f["ball"])
        key = (owner["id"], owner["team"]) if owner else None
        if runs and runs[-1]["key"] == key and runs[-1]["scene"] == f["scene"]:
            runs[-1]["end"] = f["t"]
            runs[-1]["samples"] += 1
        else:
            runs.append(
                {"key": key, "start": f["t"], "end": f["t"], "samples": 1, "scene": f["scene"]}
            )
    minimum_samples = max(2, math.ceil(0.25 * sample_fps))
    stable = [r for r in runs if r["key"] is not None and r["samples"] >= minimum_samples]
    seconds = [0.0, 0.0]
    timestamps = [f["t"] for f in frames]
    events = []
    for i, r in enumerate(stable):
        length = min(start_seconds + duration - r["start"], r["samples"] * dt)
        seconds[r["key"][1]] += max(0, length)
        if i == 0:
            continue
        previous = stable[i - 1]
        gap = r["start"] - previous["end"]
        if previous["scene"] != r["scene"] or gap > 1.2 or previous["key"][0] == r["key"][0]:
            continue
        observed = frames[
            bisect_left(timestamps, previous["end"]) : bisect_right(timestamps, r["start"])
        ]
        # A pass candidate needs a visible ball throughout the transfer.
        if not observed or any(f["ball"] is None or f["ball"].get("inferred") for f in observed):
            continue
        if len({f["ball"]["trackId"] for f in observed if "trackId" in f["ball"]}) > 1:
            continue
        same_team = r["key"][1] == previous["key"][1]
        if same_team:
            # A same-location ID switch is not a pass. Require the receiver to
            # already exist before the transfer and meaningful ball displacement.
            before = {p["id"]: p for p in observed[0]["players"]}
            if r["key"][0] not in before:
                continue
            sender = before.get(previous["key"][0])
            if sender is None:
                continue
            height = sender["box"][3] - sender["box"][1]
            if ball_shift(observed) < height * 0.5:
                continue
        events.append(
            {
                "id": f"cv-{len(events)}",
                "type": "pass-candidate" if same_team else "turnover-candidate",
                "t": r["start"],
                "from": previous["key"][0],
                "to": r["key"][0],
                "team": r["key"][1],
                "confidence": min(f["ball"]["confidence"] for f in observed),
                "source": "computer-vision",
                "status": "unreviewed",
            }
        )
    coverage = sum(seconds)
    chains = possession_chains(stable, events, dt, start_seconds + duration)
    return {
        "sampledFrames": len(frames),
        "playerFrames": sum(bool(f["players"]) for f in frames),
        "ballFrames": sum(f["ball"] is not None and not f["ball"].get("inferred") for f in frames),
        "ballFramesInferred": sum(bool(f["ball"] and f["ball"].get("inferred")) for f in frames),
        "teamSeconds": [round(x, 2) for x in seconds],
        "unknownSeconds": round(max(0, duration - coverage), 2),
        "possessionShare": [round(x / coverage * 100, 1) if coverage else None for x in seconds],
        "possessionCoverage": round(coverage / duration * 100, 1) if duration else 0,
        "events": events,
        "trackCount": len({p["id"] for f in frames for p in f["players"]}),
        "possessions": chains,
        "playerLoad": player_load(frames),
    }


# A team keeps the ball across brief unseen moments; longer gaps, a scene cut
# or opponent control end the possession (StatsBomb's possession-sequence idea,
# adapted to what the camera can observe).
POSSESSION_GAP = 3.0


def possession_chains(stable, events, dt, duration):
    chains = []
    for r in stable:
        team = r["key"][1]
        start, end = r["start"], min(duration, r["end"] + dt)
        last = chains[-1] if chains else None
        if (
            last
            and last["team"] == team
            and last["scene"] == r["scene"]
            and start - last["end"] <= POSSESSION_GAP
        ):
            last["end"] = end
            last["controlSeconds"] += r["samples"] * dt
        else:
            chains.append(
                {
                    "team": team,
                    "start": start,
                    "end": end,
                    "scene": r["scene"],
                    "controlSeconds": r["samples"] * dt,
                }
            )
    for chain in chains:
        chain["passes"] = sum(
            1
            for e in events
            if e["type"] == "pass-candidate"
            and e["team"] == chain["team"]
            and chain["start"] <= e["t"] <= chain["end"]
        )
        chain["start"] = round(chain["start"], 2)
        chain["end"] = round(chain["end"], 2)
        chain["controlSeconds"] = round(chain["controlSeconds"], 2)
        del chain["scene"]
    summary = []
    for team in (0, 1):
        own = [c for c in chains if c["team"] == team]
        lengths = [c["end"] - c["start"] for c in own]
        regains = sum(1 for e in events if e["type"] == "turnover-candidate" and e["team"] == team)
        opponent_passes = sum(
            1 for e in events if e["type"] == "pass-candidate" and e["team"] != team
        )
        summary.append(
            {
                "count": len(own),
                "averageSeconds": round(sum(lengths) / len(own), 1) if own else None,
                "longestSeconds": round(max(lengths), 1) if own else None,
                "passesPerPossession": (
                    round(sum(c["passes"] for c in own) / len(own), 2) if own else None
                ),
                # Pressing intensity in the spirit of PPDA: opponent passes allowed
                # per ball won. Lower = more aggressive. Needs regains to mean anything.
                "passesAllowedPerRegain": (
                    round(opponent_passes / regains, 1) if regains else None
                ),
            }
        )
    return {"teams": summary, "chains": chains}
