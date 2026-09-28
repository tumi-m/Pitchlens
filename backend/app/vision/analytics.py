"""Match analytics from tracked detections: possession, events and team stats.

Everything here is post-processing of result.json (no model inference), so it
re-runs in seconds whenever the pitch calibration or a reviewer's decisions
change.

With a pitch calibration, positions are metres on the pitch and the rules use
physical distances, speeds and the goals. Without one, only image-space
possession and pass/turnover candidates are produced; shots, goals, heatmaps
and directions are reported as unavailable, never as zero.

Method (see docs/market-ready/DECISIONS.md for sources and trade-offs):
- Two steps, after Vidal-Codina et al. (2022, Sports Engineering, "Automatic
  event detection in football using tracking data"): decide who controls the
  ball in each frame, then read events from changes of control, the ball's
  movement and the pitch geometry. Possession zone ~1 m (their 0.5-1.0 m),
  widened by the calibration error because our positions come from one camera.
- Team possession runs from one team's won ball to the other team's, including
  the ball's flight between teammates, excluding dead-ball time. Link & Hoernig
  (2017, PLOS ONE) show player-on-ball control is only ~18 of ~56 minutes of
  team possession, so a control-time share would be biased and noisy.
- Shots are judged by what happens next (keeper collects = save, defender in
  front = block, beyond the line = goal candidate): ball height is not
  observable from one camera at 5-6 frames per second. Goals are never counted
  from geometry alone; a restart from the centre spot corroborates.
- Momentum weights control by closeness to the goal being attacked, in the
  spirit of Sofascore/Opta "attack momentum".
Thresholds are starting values for small-sided pitches at 5-6 sampled frames
per second; they are meant to be re-fitted from reviewed matches.
"""

import math
import random
from collections import Counter, defaultdict

import numpy as np

from app.vision import pitch as pitchlib
from app.vision.metrics import possession_owner as image_owner

PARAMS = {
    # Control (possession zone). Vidal-Codina: 0.5-1.0 m on elite tracking;
    # databallpy default 1.5 m. Ours: 1.1 m + 1.5 x calibration error, capped.
    "controlRadius": 1.1,
    "maxControlRadius": 2.0,
    # Duel zone: an opponent this much further out still contests the ball.
    "duelExtra": 0.5,
    # The ball must move with the player (gain validation, after Vidal-Codina).
    "maxRelativeSpeed": 4.0,  # m/s, plus 2 x calibration error
    "minControlSeconds": 0.3,
    # A control spell survives this many unseen samples (ball hidden at the feet).
    "bridgeSamples": 3,
    # Passes and turnovers.
    "maxTransferSmall": 4.0,  # s, pitches up to 45 m long (5-a-side)
    "maxTransferLarge": 5.0,  # s, larger pitches
    "minPassDistance": 2.0,  # m of ball travel
    "tackleMaxTravel": 2.0,  # m: shorter opponent gains are tackles, not interceptions
    "tackleMaxGap": 0.8,  # s
    "minPassesForAccuracy": 20,  # attempts per team before an accuracy % is shown
    # Shots.
    "minShotSpeed": 8.0,  # m/s; 6-8 m/s kept as low-confidence candidates
    "lowShotSpeed": 6.0,
    "maxShotOrigin": 0.6,  # fraction of pitch length from the target goal line
    "shotBand": 2.0,  # m beyond the posts for the extrapolated crossing
    "shotTimeToLine": 1.5,  # s
    "saveWindow": 1.5,  # s for a keeper-area gain after a shot
    "onTargetMargin": 0.3,  # m outside the posts (ball radius + noise)
    # Possession sequences and dead ball.
    "maxPossessionGap": 5.0,  # s of unseen/loose ball a possession survives
    "deadBallSpeed": 1.5,  # m/s mean player speed...
    "deadBallSeconds": 3.0,  # ...for at least this long
    "possessionMinCoverage": 0.6,  # share of in-play time before possession % is shown
    # Kick-off after a goal (centre restart).
    "kickoffCentreRadius": 1.5,
    "kickoffOwnHalfShare": 0.8,
    "kickoffWindow": (10.0, 90.0),
    # Momentum.
    "momentumGoalScale": 8.0,  # m, exp(-distance to attacked goal / scale)
    "momentumHalfLifeMinutes": 1.5,
    # Positions and tracks.
    "offPitchMargin": 2.0,
    "maxPlayerSpeed": 8.5,
    "maxStitchGap": 3.0,
    "directionBinSeconds": 60.0,
}


# --------------------------------------------------------------------- inputs


def foot_point(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, y2)


def ball_ground_point(ball):
    """Where the ball touches the ground in the image (bottom of its box)."""
    box = ball.get("box")
    if box:
        return ((box[0] + box[2]) / 2, box[3])
    return (ball["x"], ball["y"])


def calibration_by_frame(frames, calibration):
    """Per-frame image->pitch homography (or None) from a saved calibration."""
    if not calibration:
        return [None] * len(frames), {}
    per_frame = calibration.get("frames")
    if per_frame and len(per_frame) == len(frames):
        return [np.asarray(f["H"], float).reshape(3, 3) if f and f.get("H") else None for f in per_frame], calibration
    anchors = {int(k): np.asarray(v, float) for k, v in (calibration.get("anchors") or {}).items()}
    homographies, _ = pitchlib.propagate(frames, anchors, calibration.get("static"))
    return homographies, calibration


def project(frames, calibration):
    """Pitch positions (metres) for players and ball in every frame."""
    homographies, cal = calibration_by_frame(frames, calibration)
    template = cal.get("template") if cal else None
    L = template["length"] if template else None
    W = template["width"] if template else None
    margin = PARAMS["offPitchMargin"]
    out = []
    for f, H in zip(frames, homographies):
        players = []
        for p in f["players"]:
            xy = None
            if H is not None:
                point = pitchlib.image_to_pitch(cal, [foot_point(p["box"])], H)[0]
                if np.isfinite(point).all() and -margin <= point[0] <= L + margin and -margin <= point[1] <= W + margin:
                    xy = (float(point[0]), float(point[1]))
            players.append(
                {
                    "id": p["id"],
                    "team": p.get("team", -1),
                    "role": p.get("role", "player"),
                    "xy": xy,
                    "height": p["box"][3] - p["box"][1],
                    "box": p["box"],
                }
            )
        ball = None
        if f.get("ball"):
            b = f["ball"]
            xy = None
            onPitch = None
            if H is not None:
                point = pitchlib.image_to_pitch(cal, [ball_ground_point(b)], H)[0]
                if np.isfinite(point).all():
                    onPitch = bool(-margin <= point[0] <= L + margin and -margin <= point[1] <= W + margin)
                    # Beyond the margin the ground-plane assumption has failed
                    # (ball in the air, or a false detection): keep it out of
                    # distance rules but remember where it projected.
                    xy = (float(point[0]), float(point[1]))
            ball = {
                "xy": xy,
                "onPitch": onPitch,
                "inferred": bool(b.get("inferred")),
                "confidence": b.get("confidence", 0),
                "image": (b["x"], b["y"]),
                "raw": b,
            }
        out.append({"t": f["t"], "scene": f["scene"], "players": players, "ball": ball, "calibrated": H is not None})
    return out, template


# --------------------------------------------------------------------- tracks


def stitch_tracks(projected, sample_fps):
    """Join short track fragments into longer player tracks.

    The online tracker drops an identity after 1.2 s unseen, so one player
    becomes dozens of fragments. Offline we can look both ways: a fragment that
    ends is continued by one that starts shortly after, nearby (within a
    running speed), with the same kit, and never overlapping in time.
    Returns {fragment id: player id}.
    """
    fragments = {}
    for index, f in enumerate(projected):
        for p in f["players"]:
            item = fragments.setdefault(p["id"], {"id": p["id"], "frames": [], "teams": Counter(), "scene": f["scene"]})
            item["frames"].append((index, f["t"], p["xy"], foot_point(p["box"]), p["height"]))
            if p["team"] in (0, 1):
                item["teams"][p["team"]] += 1
    for item in fragments.values():
        item["team"] = item["teams"].most_common(1)[0][0] if item["teams"] else -1
        item["start"], item["end"] = item["frames"][0][1], item["frames"][-1][1]
    order = sorted(fragments.values(), key=lambda x: x["start"])
    player_of = {}
    tails = []  # open player tracks: dict(player, last fragment)
    next_player = 1
    vmax, gap_max = PARAMS["maxPlayerSpeed"], PARAMS["maxStitchGap"]
    for frag in order:
        best, best_cost = None, None
        first = frag["frames"][0]
        for tail in tails:
            last = tail["last"]
            if last["scene"] != frag["scene"]:
                continue
            gap = frag["start"] - last["end"]
            if gap <= 0 or gap > gap_max:
                continue
            if frag["team"] in (0, 1) and last["team"] in (0, 1) and frag["team"] != last["team"]:
                continue
            end = last["frames"][-1]
            if end[2] is not None and first[2] is not None:
                distance = math.dist(end[2], first[2])
                limit = vmax * gap + 1.5
            else:
                height = max(10.0, (end[4] + first[4]) / 2)
                distance = math.dist(end[3], first[3]) / height
                limit = 1.2 * gap + 0.8  # body heights
            if distance > limit:
                continue
            cost = distance / limit + gap / gap_max
            if best_cost is None or cost < best_cost:
                best, best_cost = tail, cost
        if best is None:
            tails.append({"player": next_player, "last": frag})
            player_of[frag["id"]] = next_player
            next_player += 1
        else:
            best["last"] = frag
            player_of[frag["id"]] = best["player"]
    return player_of


# --------------------------------------------------------------------- direction


def attacking_directions(projected, template, player_of=None):
    """Which goal each team attacks over time.

    Per minute, the team whose players stand deeper on average defends that
    side. At most one switch (half time) is allowed: the switch point that best
    explains the per-minute evidence is chosen. Returns a dict with segments
    and a confidence; team 0 "attacks right" means towards x = length.
    """
    if not template:
        return None
    L = template["length"]
    bin_seconds = PARAMS["directionBinSeconds"]
    bins = defaultdict(lambda: [[], []])
    for f in projected:
        if not f["calibrated"]:
            continue
        b = int(f["t"] // bin_seconds)
        for p in f["players"]:
            if p["team"] in (0, 1) and p["xy"] and p["role"] in ("player", "person"):
                bins[b][p["team"]].append(p["xy"][0])
    votes = []
    for b in sorted(bins):
        a, c = bins[b]
        if len(a) >= 5 and len(c) >= 5:
            diff = (np.mean(a) - np.mean(c)) / L
            # team 0 deeper on the left (smaller x) -> team 0 attacks right
            votes.append((b, 1 if diff < 0 else -1, abs(diff)))
    if not votes:
        return {"segments": [], "confidence": 0.0, "source": "insufficient"}
    best = None
    for split in range(len(votes) + 1):
        for first in (1, -1):
            score = sum(w for i, (_, v, w) in enumerate(votes) if v == (first if i < split else -first))
            # A switch must be earned: without one, the whole match is one direction.
            if split not in (0, len(votes)):
                score -= 0.02
            if best is None or score > best[0]:
                best = (score, split, first)
    _, split, first = best
    total = sum(w for _, _, w in votes) or 1
    agree = sum(w for i, (_, v, w) in enumerate(votes) if v == (first if i < split else -first))
    segments = []
    if split > 0:
        segments.append({"start": 0.0, "end": votes[split - 1][0] * bin_seconds + bin_seconds, "team0Attacks": "right" if first == 1 else "left"})
    if split < len(votes):
        start = votes[split][0] * bin_seconds if split else 0.0
        direction = -first  # votes from `split` on are explained by -first
        segments.append({"start": start, "end": math.inf, "team0Attacks": "right" if direction == 1 else "left"})
    if len(segments) == 2:
        segments[0]["end"] = segments[1]["start"]
    return {"segments": segments, "confidence": round(agree / total, 2), "source": "team-depth"}


def attack_sign(directions, team, t):
    """+1 when `team` attacks towards x = length at time t, -1 towards 0."""
    if not directions or not directions.get("segments"):
        return None
    for s in directions["segments"]:
        if s["start"] <= t < s["end"]:
            sign = 1 if s["team0Attacks"] == "right" else -1
            return sign if team == 0 else -sign
    return None


# --------------------------------------------------------------------- control


def _central_difference(projected, positions):
    """Velocity (m/s) per frame from a {frame index: (x, y)} track, central difference."""
    out = {}
    for i, xy in positions.items():
        a, b = positions.get(i - 1), positions.get(i + 1)
        if a is None or b is None:
            continue
        if projected[i - 1]["scene"] != projected[i + 1]["scene"]:
            continue
        dt = projected[i + 1]["t"] - projected[i - 1]["t"]
        if dt > 0:
            out[i] = ((b[0] - a[0]) / dt, (b[1] - a[1]) / dt)
    return out


def ball_positions(projected):
    """Grounded, on-pitch ball positions by frame (metres)."""
    return {
        i: f["ball"]["xy"]
        for i, f in enumerate(projected)
        if f["ball"] and f["ball"]["xy"] is not None and f["ball"]["onPitch"]
    }


def fragment_positions(projected):
    tracks = defaultdict(dict)
    for i, f in enumerate(projected):
        for p in f["players"]:
            if p["xy"]:
                tracks[p["id"]][i] = p["xy"]
    return tracks


def control_states(projected, player_of, calibration_error=0.0):
    """Per-frame ball state: control (by whom), contested, loose or unknown.

    Calibrated frames use metres: the nearest player within the possession
    zone controls the ball if it moves with them and no opponent is inside the
    duel zone. Uncalibrated frames fall back to the image-space rule.
    """
    balls = ball_positions(projected)
    ball_velocity = _central_difference(projected, balls)
    tracks = fragment_positions(projected)
    velocities = {pid: _central_difference(projected, pos) for pid, pos in tracks.items()}
    radius = min(PARAMS["maxControlRadius"], PARAMS["controlRadius"] + 1.5 * calibration_error)
    duel = radius + PARAMS["duelExtra"]
    relative_limit = PARAMS["maxRelativeSpeed"] + 2 * calibration_error
    states = []
    for i, f in enumerate(projected):
        b = f["ball"]
        if b is None:
            states.append({"state": "unknown"})
            continue
        if f["calibrated"]:
            if i not in balls:
                # Projected off the pitch: in the air or a false detection.
                states.append({"state": "loose", "airborne": True})
                continue
            bxy = balls[i]
            near = sorted(
                (
                    (math.dist(p["xy"], bxy), k, p)
                    for k, p in enumerate(f["players"])
                    if p["team"] in (0, 1) and p["xy"] and math.dist(p["xy"], bxy) <= duel
                ),
                key=lambda x: (x[0], x[1]),
            )
            if not near or near[0][0] > radius:
                states.append({"state": "loose", "ball": bxy})
                continue
            d0, _, p0 = near[0]
            rivals = [x for x in near[1:] if x[2]["team"] != p0["team"]]
            if rivals:
                states.append({"state": "contested", "ball": bxy, "teams": [p0["team"], rivals[0][2]["team"]]})
                continue
            bv = ball_velocity.get(i)
            pv = velocities.get(p0["id"], {}).get(i)
            if bv is not None and pv is not None and math.dist(bv, pv) > relative_limit:
                states.append({"state": "loose", "ball": bxy, "passing": True})
                continue
            states.append(
                {
                    "state": "control",
                    "ball": bxy,
                    "player": player_of.get(p0["id"], p0["id"]),
                    "fragment": p0["id"],
                    "team": p0["team"],
                    "distance": round(d0, 2),
                }
            )
        else:
            owner = image_owner([{"id": p["id"], "team": p["team"], "box": p["box"]} for p in f["players"]], b["raw"])
            if owner is None:
                states.append({"state": "loose", "image": True})
            else:
                states.append(
                    {"state": "control", "player": player_of.get(owner["id"], owner["id"]), "fragment": owner["id"], "team": owner["team"], "image": True}
                )
    return states, ball_velocity, velocities


def control_spells(projected, states, sample_fps):
    """Runs of control by one player, bridging a few frames where the ball is hidden."""
    minimum = max(2, math.ceil(PARAMS["minControlSeconds"] * sample_fps))
    bridge = PARAMS["bridgeSamples"] + 1
    spells, current = [], None
    for i, (f, s) in enumerate(zip(projected, states)):
        if s["state"] != "control":
            # Another state that shows the ball elsewhere ends the spell.
            if current and s["state"] in ("loose", "contested") and not s.get("airborne"):
                spells.append(current)
                current = None
            continue
        key = (s.get("player"), s.get("team"), f["scene"])
        if current and current["key"] == key and i - current["last"] <= bridge:
            current["last"] = i
            current["frames"].append(i)
            continue
        if current:
            spells.append(current)
        current = {"key": key, "first": i, "last": i, "frames": [i]}
    if current:
        spells.append(current)
    out = []
    for s in spells:
        if len(s["frames"]) < minimum:
            continue
        player, team, scene = s["key"]
        out.append(
            {
                "player": player,
                "team": team,
                "scene": scene,
                "first": s["first"],
                "last": s["last"],
                "start": projected[s["first"]]["t"],
                "end": projected[s["last"]]["t"],
                "startBall": states[s["first"]].get("ball"),
                "endBall": states[s["last"]].get("ball"),
            }
        )
    return out


# --------------------------------------------------------------------- in play


def dead_ball(projected, velocities, ball_velocity, sample_fps):
    """Frames when play is stopped: everyone slow and the ball still or unseen.

    Needs pitch positions; without them every frame counts as in play.
    Returns (in_play list, dead intervals [(first, last)]).
    """
    n = len(projected)
    if not any(f["calibrated"] for f in projected):
        return [True] * n, []
    speeds = [None] * n
    for i in range(n):
        values = [math.hypot(*v[i]) for v in velocities.values() if i in v]
        if len(values) >= 4:
            speeds[i] = float(np.mean(values))
    window = max(1, int(round(sample_fps)))
    smooth = [None] * n
    for i in range(n):
        chunk = [s for s in speeds[max(0, i - window // 2) : i + window // 2 + 1] if s is not None]
        smooth[i] = float(np.mean(chunk)) if chunk else None
    candidate = []
    for i, f in enumerate(projected):
        bv = ball_velocity.get(i)
        still = f["ball"] is None or bv is None or math.hypot(*bv) < 1.0
        candidate.append(smooth[i] is not None and smooth[i] < PARAMS["deadBallSpeed"] and still)
    minimum = int(math.ceil(PARAMS["deadBallSeconds"] * sample_fps))
    in_play = [True] * n
    intervals = []
    i = 0
    while i < n:
        if not candidate[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and candidate[j + 1] and projected[j + 1]["scene"] == projected[i]["scene"]:
            j += 1
        if j - i + 1 >= minimum:
            intervals.append((i, j))
            for k in range(i, j + 1):
                in_play[k] = False
        i = j + 1
    return in_play, intervals


# --------------------------------------------------------------------- possession


def possession_sequences(projected, spells, in_play, sample_fps):
    """Team possession: from one team's won ball until the other team wins it.

    The ball's flight between teammates belongs to the team that played it;
    a possession ends at the next opponent control, a cut, dead ball, or after
    maxPossessionGap seconds without any control (then the time is unknown).
    """
    n = len(projected)
    owner = [None] * n
    for k, s in enumerate(spells):
        for i in range(s["first"], s["last"] + 1):
            if in_play[i]:
                owner[i] = s["team"]
        nxt = spells[k + 1] if k + 1 < len(spells) else None
        if nxt and nxt["scene"] == s["scene"] and nxt["start"] - s["end"] <= PARAMS["maxPossessionGap"]:
            between = range(s["last"] + 1, nxt["first"])
            if all(in_play[i] for i in between):
                for i in between:
                    owner[i] = s["team"]
    dt = 1 / sample_fps
    seconds = [0.0, 0.0]
    sequences = []
    current = None
    for i, team in enumerate(owner):
        if team is not None:
            seconds[team] += dt
        if current and team == current["team"] and projected[i]["scene"] == current["scene"]:
            current["last"] = i
            continue
        if current:
            sequences.append(current)
            current = None
        if team is not None:
            current = {"team": team, "first": i, "last": i, "scene": projected[i]["scene"]}
    if current:
        sequences.append(current)
    for s in sequences:
        s["start"] = round(projected[s["first"]]["t"], 2)
        s["end"] = round(projected[s["last"]]["t"] + dt, 2)
        s["seconds"] = round((s["last"] - s["first"] + 1) * dt, 2)
        del s["scene"]
    in_play_seconds = sum(in_play) * dt
    return owner, sequences, seconds, in_play_seconds


def share_interval(sequences, iterations=400, seed=7):
    """95% bootstrap interval of team 0's possession share, resampling possessions."""
    if len(sequences) < 4:
        return None
    rng = random.Random(seed)
    shares = []
    for _ in range(iterations):
        sample = [rng.choice(sequences) for _ in sequences]
        a = sum(s["seconds"] for s in sample if s["team"] == 0)
        b = sum(s["seconds"] for s in sample if s["team"] == 1)
        if a + b:
            shares.append(a / (a + b) * 100)
    if not shares:
        return None
    shares.sort()
    return [round(shares[int(0.025 * len(shares))], 1), round(shares[int(0.975 * len(shares)) - 1], 1)]


# --------------------------------------------------------------------- events


def _ball_path(projected, first, last):
    path = []
    for i in range(first, last + 1):
        b = projected[i]["ball"]
        if b and b["xy"]:
            path.append((projected[i]["t"], b["xy"], b["onPitch"], b["inferred"]))
    return path


def _visible_fraction(projected, first, last):
    n = last - first + 1
    if n <= 0:
        return 0.0
    seen = sum(1 for i in range(first, last + 1) if projected[i]["ball"] and not projected[i]["ball"]["inferred"])
    return seen / n


def in_keeper_zone(xy, goal_x, template):
    """Inside (or at the edge of) the goal area of the goal at goal_x."""
    if xy is None:
        return False
    c = template["width"] / 2
    if template.get("areaRadius"):
        return math.dist(xy, (goal_x, c)) <= template["areaRadius"] + 1.0
    if template.get("areaDepth") and template.get("areaWidth"):
        return abs(xy[0] - goal_x) <= template["areaDepth"] + 1.0 and abs(xy[1] - c) <= template["areaWidth"] / 2 + 1.0
    return math.dist(xy, (goal_x, c)) <= max(4.0, 0.15 * template["length"])


def detect_shot(projected, template, directions, spell, sample_fps):
    """Release towards the attacked goal: fast, from within range, heading between the posts (+ band)."""
    if not template or spell["endBall"] is None:
        return None
    sign = attack_sign(directions, spell["team"], spell["end"])
    if sign is None:
        return None
    L, W, g = template["length"], template["width"], template["goalWidth"]
    goal_x = L if sign == 1 else 0.0
    origin = spell["endBall"]
    distance_to_goal = abs(goal_x - origin[0])
    if distance_to_goal > PARAMS["maxShotOrigin"] * L:
        return None
    horizon = PARAMS["shotTimeToLine"] + 0.5
    end = min(len(projected) - 1, spell["last"] + int(math.ceil(horizon * sample_fps)) + 1)
    path = [p for p in _ball_path(projected, spell["last"] + 1, end) if p[0] - spell["end"] <= horizon]
    # Velocity from the samples after the ball has left the foot (the release
    # frame often still shows it there): at least two moving samples within
    # ~0.8 s, fitted with a straight line.
    moving = [(t, xy) for t, xy, _, inferred in path if not inferred and math.dist(xy, origin) > 0.5 and t - spell["end"] <= 0.8 + 1e-6]
    if len(moving) < 2:
        return None
    ts = np.array([m[0] for m in moving])
    if np.ptp(ts) <= 0:
        return None
    vx = float(np.polyfit(ts, [m[1][0] for m in moving], 1)[0])
    vy = float(np.polyfit(ts, [m[1][1] for m in moving], 1)[0])
    speed = math.hypot(vx, vy)
    if speed < PARAMS["lowShotSpeed"] or vx * sign <= 0:
        return None
    first_t, first_xy = moving[0]
    time_to_line = (goal_x - first_xy[0]) / vx
    if time_to_line <= 0 or time_to_line > PARAMS["shotTimeToLine"]:
        return None
    cross_y = first_xy[1] + vy * time_to_line
    centre = W / 2
    if abs(cross_y - centre) > g / 2 + PARAMS["shotBand"]:
        return None
    beyond = None
    for t, xy, _, inferred in path:
        if (xy[0] - goal_x) * sign >= 0.2 and not inferred:
            beyond = (t, xy)
            break
    return {
        "speed": round(speed, 1),
        "distance": round(distance_to_goal, 1),
        "crossY": round(cross_y, 2),
        "goalX": goal_x,
        "sign": sign,
        "beyond": beyond,
        "lowSpeed": speed < PARAMS["minShotSpeed"],
    }


def shot_outcome(shot, spell, nxt, template):
    """What happened next decides the outcome; ball height is not observable."""
    W, g = template["width"], template["goalWidth"]
    centre = W / 2
    between_posts = abs(shot["crossY"] - centre) <= g / 2 + PARAMS["onTargetMargin"]
    if shot["beyond"] is not None:
        if abs(shot["beyond"][1][1] - centre) <= g / 2 + 0.1:
            return "goal-candidate", True
        return "off-target", False
    if nxt and nxt["team"] != spell["team"] and nxt["start"] - spell["end"] <= PARAMS["saveWindow"]:
        where = nxt["startBall"]
        if in_keeper_zone(where, shot["goalX"], template):
            return ("saved", True) if between_posts else ("off-target", False)
        if where is not None and abs(where[0] - shot["goalX"]) >= 1.5:
            return "blocked", False
    return "unresolved", None


def detect_kickoffs(projected, dead_intervals, directions, template, sample_fps):
    """Centre restarts: play stopped, both teams in their own half, a player on the centre spot."""
    if not template or not directions or not directions.get("segments"):
        return []
    L, W = template["length"], template["width"]
    centre = (L / 2, W / 2)
    found = []
    need = max(2, int(round(sample_fps)))  # ~1 s on the spot
    for first, last in dead_intervals:
        window = range(max(first, last - 3 * need), min(len(projected), last + need + 1))
        on_spot = Counter()
        own_half_ok = 0
        checked = 0
        for i in window:
            f = projected[i]
            if not f["calibrated"]:
                continue
            by_team = {0: [], 1: []}
            for p in f["players"]:
                if p["team"] in (0, 1) and p["xy"]:
                    by_team[p["team"]].append(p)
                    if math.dist(p["xy"], centre) <= PARAMS["kickoffCentreRadius"]:
                        on_spot[p["team"]] += 1
            ok = True
            for team, players in by_team.items():
                if len(players) < 2:
                    ok = False
                    break
                sign = attack_sign(directions, team, f["t"])
                if sign is None:
                    ok = False
                    break
                own = sum(1 for p in players if (p["xy"][0] - L / 2) * sign <= 0.5)
                if own / len(players) < PARAMS["kickoffOwnHalfShare"]:
                    ok = False
            checked += 1
            own_half_ok += ok
        if not checked or own_half_ok / checked < 0.6 or not on_spot:
            continue
        team, samples = on_spot.most_common(1)[0]
        if samples < need:
            continue
        found.append({"t": round(projected[last]["t"], 2), "team": team, "frame": last})
    return found


def detect_events(projected, states, spells, template, directions, sample_fps, in_play, dead_intervals):
    events = []

    def add(kind, t, team, confidence, **extra):
        events.append(
            {
                "id": "",
                "type": kind,
                "t": round(float(t), 2),
                "team": int(team) if team is not None else None,
                "confidence": round(float(max(0.05, min(0.95, confidence))), 2),
                "status": "proposed",
                **extra,
            }
        )

    small = template is not None and template["length"] <= 45
    max_transfer = PARAMS["maxTransferSmall"] if small else PARAMS["maxTransferLarge"]
    shots = []
    for k, spell in enumerate(spells):
        nxt = spells[k + 1] if k + 1 < len(spells) else None
        shot = detect_shot(projected, template, directions, spell, sample_fps)
        if shot:
            outcome, on_target = shot_outcome(shot, spell, nxt, template)
            confidence = 0.45 + (0.0 if shot["lowSpeed"] else 0.15) + (0.15 if outcome in ("saved", "goal-candidate", "off-target") else 0)
            item = {
                "player": spell["player"],
                "x": round(spell["endBall"][0], 2),
                "y": round(spell["endBall"][1], 2),
                "outcome": outcome,
                "onTarget": on_target,
                "speed": shot["speed"],
                "distance": shot["distance"],
                "needsReview": True,
            }
            add("shot", spell["end"], spell["team"], confidence, **item)
            shots.append(events[-1])
            if outcome == "goal-candidate":
                add("goal-candidate", spell["end"], spell["team"], 0.35, player=spell["player"], x=item["x"], y=item["y"], evidence=["ball-over-line"], needsReview=True,
                    note="Ball seen beyond the goal line between the posts. Only a reviewer can confirm a goal.")
            continue
        if not nxt or nxt["scene"] != spell["scene"] or nxt["player"] == spell["player"]:
            continue
        gap = nxt["start"] - spell["end"]
        if gap <= 0 or gap > max_transfer:
            continue
        if not all(in_play[i] for i in range(spell["last"], nxt["first"] + 1)):
            continue  # a stoppage in between: a restart, not a pass
        seen = _visible_fraction(projected, spell["last"], nxt["first"])
        travel = None
        if spell["endBall"] is not None and nxt["startBall"] is not None:
            travel = math.dist(spell["endBall"], nxt["startBall"])
        confidence = 0.35 + 0.4 * seen + (0.1 if travel is not None else 0)
        common = {
            "from": spell["player"],
            "to": nxt["player"],
            "x": round(spell["endBall"][0], 2) if spell["endBall"] else None,
            "y": round(spell["endBall"][1], 2) if spell["endBall"] else None,
            "endX": round(nxt["startBall"][0], 2) if nxt["startBall"] else None,
            "endY": round(nxt["startBall"][1], 2) if nxt["startBall"] else None,
            "ballSeen": round(seen, 2),
            "tEnd": round(nxt["start"], 2),
        }
        if nxt["team"] == spell["team"]:
            if travel is not None and travel < PARAMS["minPassDistance"]:
                continue
            add("pass", spell["end"], spell["team"], confidence, outcome="complete", length=round(travel, 1) if travel is not None else None, **common)
        else:
            tackle = travel is not None and travel < PARAMS["tackleMaxTravel"] and gap < PARAMS["tackleMaxGap"]
            if tackle:
                add("tackle", nxt["start"], nxt["team"], confidence - 0.05, lostBy=spell["team"], **common)
            else:
                add("pass", spell["end"], spell["team"], confidence - 0.05, outcome="intercepted", length=round(travel, 1) if travel is not None else None, **common)
                add("interception", nxt["start"], nxt["team"], confidence - 0.05, lostBy=spell["team"], **common)
    # Ball out of play (lined pitches only; a walled cage keeps it in).
    if template and not template.get("walls"):
        L, W = template["length"], template["width"]
        run = 0
        for i, f in enumerate(projected):
            b = f["ball"]
            outside = bool(
                b and b["xy"] is not None and not b["inferred"] and b["onPitch"]
                and (b["xy"][0] < -0.5 or b["xy"][0] > L + 0.5 or b["xy"][1] < -0.5 or b["xy"][1] > W + 0.5)
            )
            run = run + 1 if outside else 0
            if run == 2:
                last_team = next((s["team"] for s in reversed(spells) if s["last"] < i), None)
                add("out", projected[i - 1]["t"], last_team, 0.4, x=round(b["xy"][0], 2), y=round(b["xy"][1], 2), needsReview=False)
    # Restarts from the centre spot after a goal corroborate (or reveal) goals.
    kickoffs = detect_kickoffs(projected, dead_intervals, directions, template, sample_fps)
    start_t = projected[0]["t"] if projected else 0
    lo, hi = PARAMS["kickoffWindow"]
    for ko in kickoffs:
        if ko["t"] - start_t < 20:
            continue  # the match's own kick-off
        scorer = 1 - ko["team"]
        prior = [s for s in shots if s["team"] == scorer and lo <= ko["t"] - s["t"] <= hi]
        if prior:
            shot = prior[-1]
            existing = next((e for e in events if e["type"] == "goal-candidate" and abs(e["t"] - shot["t"]) < 0.01), None)
            if existing:
                existing["evidence"].append("centre-restart")
                existing["confidence"] = round(min(0.8, existing["confidence"] + 0.3), 2)
            else:
                add("goal-candidate", shot["t"], scorer, 0.45, x=shot.get("x"), y=shot.get("y"), evidence=["centre-restart"], restartAt=ko["t"], needsReview=True,
                    note="Play restarted from the centre spot after this shot, as it does after a goal. Only a reviewer can confirm a goal.")
        else:
            add("goal-candidate", max(start_t, ko["t"] - lo), scorer, 0.25, evidence=["centre-restart"], restartAt=ko["t"], needsReview=True,
                note="Play restarted from the centre spot, as it does after a goal; the shot itself was not seen. Only a reviewer can confirm a goal.")
    events.sort(key=lambda e: e["t"])
    for i, e in enumerate(events):
        e["id"] = f"ev-{i}"
    return events, kickoffs


# --------------------------------------------------------------------- review


ON_TARGET = {"on-target", "saved", "goal-candidate", "goal"}


def apply_review(events, review):
    """Apply a reviewer's append-only decisions. Later decisions win."""
    if not review:
        return events, {}
    by_id = {e["id"]: dict(e) for e in events}
    added = []
    overrides = {}
    for d in review.get("decisions", []):
        action = d.get("action")
        if action == "add":
            item = {
                "id": d.get("id") or f"added-{len(added)}",
                "type": d.get("type", "shot"),
                "t": float(d.get("t", 0)),
                "team": d.get("team"),
                "confidence": 1.0,
                "status": "confirmed",
                "source": "reviewer",
                **({"outcome": d["outcome"]} if d.get("outcome") else {}),
                **({"x": d["x"], "y": d["y"]} if d.get("x") is not None else {}),
            }
            if item["type"] == "shot":
                item["onTarget"] = item.get("outcome", "on-target") in ON_TARGET
            added.append(item)
            continue
        if action == "direction":
            overrides["direction"] = d.get("value")
            continue
        target = by_id.get(d.get("eventId"))
        if target is None:
            target = next((a for a in added if a["id"] == d.get("eventId")), None)
        if target is None:
            continue
        if action == "accept":
            target["status"] = "confirmed"
        elif action == "reject":
            target["status"] = "rejected"
        elif action == "reset":
            target["status"] = "proposed"
        elif action == "team" and d.get("value") in (0, 1):
            target["team"] = d["value"]
            target["status"] = "confirmed"
        elif action == "type" and d.get("value"):
            target["type"] = d["value"]
            target["status"] = "confirmed"
        elif action == "outcome" and d.get("value"):
            target["outcome"] = d["value"]
            target["status"] = "confirmed"
            if target["type"] == "shot":
                target["onTarget"] = d["value"] in ON_TARGET
    merged = [e for e in by_id.values()] + [a for a in added if a["id"] not in by_id]
    merged.sort(key=lambda e: e["t"])
    return merged, overrides


# --------------------------------------------------------------------- stats


def _grid(points, template, nx=12, ny=8):
    L, W = template["length"], template["width"]
    grid = np.zeros((ny, nx))
    for x, y in points:
        i = min(nx - 1, max(0, int(x / L * nx)))
        j = min(ny - 1, max(0, int(y / W * ny)))
        grid[j, i] += 1
    total = grid.sum()
    return (grid / total).round(4).tolist() if total else None


def summarise(projected, states, spells, events, template, directions, player_of, sample_fps, duration, start, in_play, owner, sequences, possession_seconds, in_play_seconds):
    dt = 1 / sample_fps
    control = [0.0, 0.0]
    contested = loose = unknown = 0.0
    tilt = [0.0, 0.0]  # control time in the attacking third
    for f, s in zip(projected, states):
        if s["state"] == "control":
            control[s["team"]] += dt
            if template and s.get("ball") is not None:
                sign = attack_sign(directions, s["team"], f["t"])
                goal_x = template["length"] if sign == 1 else 0.0
                if sign is not None and abs(s["ball"][0] - goal_x) <= template["length"] / 3:
                    tilt[s["team"]] += dt
        elif s["state"] == "contested":
            contested += dt
        elif s["state"] == "loose":
            loose += dt
        else:
            unknown += dt
    live = [e for e in events if e["status"] != "rejected"]

    def count(kind, team, predicate=None):
        items = [e for e in live if e["type"] == kind and e.get("team") == team and (predicate is None or predicate(e))]
        return {
            "value": len(items),
            "confirmed": sum(1 for e in items if e["status"] == "confirmed"),
            "pending": sum(1 for e in items if e["status"] == "proposed"),
        }

    total_possession = sum(possession_seconds)
    coverage = total_possession / in_play_seconds if in_play_seconds else 0.0
    shown = coverage >= PARAMS["possessionMinCoverage"] and total_possession > 0
    interval = share_interval(sequences) if shown else None
    teams = []
    for team in (0, 1):
        passes = count("pass", team)
        complete = count("pass", team, lambda e: e.get("outcome") == "complete")
        reliable = [e for e in live if e["type"] == "pass" and e.get("team") == team and (e["status"] == "confirmed" or e["confidence"] >= 0.5)]
        accuracy = None
        if len(reliable) >= PARAMS["minPassesForAccuracy"]:
            accuracy = round(sum(1 for e in reliable if e.get("outcome") == "complete") / len(reliable) * 100, 1)
        goals_confirmed = sum(
            1
            for e in live
            if e.get("team") == team
            and e["status"] == "confirmed"
            and (e["type"] in ("goal", "goal-candidate") or (e["type"] == "shot" and e.get("outcome") in ("goal", "goal-candidate") and e.get("source") == "reviewer"))
        )
        own = [s for s in sequences if s["team"] == team]
        teams.append(
            {
                "controlSeconds": round(control[team], 1),
                "possessionSeconds": round(possession_seconds[team], 1),
                "possession": round(possession_seconds[team] / total_possession * 100, 1) if shown else None,
                "possessions": len(own),
                "averagePossession": round(sum(s["seconds"] for s in own) / len(own), 1) if own else None,
                "passes": passes,
                "passesComplete": complete,
                "passAccuracy": accuracy,
                "shots": count("shot", team) if template else None,
                "shotsOnTarget": count("shot", team, lambda e: e.get("onTarget") is True) if template else None,
                "goals": {"value": goals_confirmed, "candidates": count("goal-candidate", team)["value"]} if template else None,
                "interceptions": count("interception", team),
                "tackles": count("tackle", team),
                "fieldTilt": round(tilt[team] / sum(tilt) * 100, 1) if template and sum(tilt) > 0 else None,
            }
        )
    coverage_block = {
        "possessionPercent": round(coverage * 100, 1),
        "possessionShown": shown,
        "possessionInterval": interval,
        "controlPercent": round(sum(control) / duration * 100, 1) if duration else 0,
        "ballStatePercent": round((sum(control) + contested + loose) / duration * 100, 1) if duration else 0,
        "calibratedPercent": round(sum(1 for f in projected if f["calibrated"]) / max(1, len(projected)) * 100, 1),
        "inPlaySeconds": round(in_play_seconds, 1),
        "deadBallSeconds": round(max(0.0, len(projected) * dt - in_play_seconds), 1),
        "contestedSeconds": round(contested, 1),
        "looseSeconds": round(loose, 1),
        "unknownSeconds": round(unknown, 1),
    }
    minutes = max(1, math.ceil(duration / 60))
    per_minute = [[0.0, 0.0] for _ in range(minutes)]
    for f, s in zip(projected, states):
        if s["state"] != "control":
            continue
        m = min(minutes - 1, max(0, int((f["t"] - start) // 60)))
        weight = 1.0
        if template and s.get("ball") is not None:
            sign = attack_sign(directions, s["team"], f["t"])
            if sign is not None:
                goal_x = template["length"] if sign == 1 else 0.0
                distance = math.dist(s["ball"], (goal_x, template["width"] / 2))
                weight = math.exp(-distance / PARAMS["momentumGoalScale"])
        per_minute[m][s["team"]] += weight * dt
    raw = [(a - b) / max(a + b, 1e-9) if a + b > 0 else None for a, b in per_minute]
    # Exponentially weighted smoothing (half-life in minutes), leaving gaps as gaps.
    alpha = 1 - 0.5 ** (1 / PARAMS["momentumHalfLifeMinutes"])
    momentum, level = [], None
    for v in raw:
        if v is None:
            momentum.append(None)
            continue
        level = v if level is None else level + alpha * (v - level)
        momentum.append(round(level, 2))
    heatmaps = average_positions = shot_map = None
    if template:
        normalised = {0: [], 1: []}
        per_player = defaultdict(list)
        for f in projected:
            for p in f["players"]:
                if p["team"] not in (0, 1) or not p["xy"]:
                    continue
                sign = attack_sign(directions, p["team"], f["t"]) or 1
                x, y = p["xy"]
                nx_, ny_ = (x, y) if sign == 1 else (template["length"] - x, template["width"] - y)
                normalised[p["team"]].append((nx_, ny_))
                per_player[(player_of.get(p["id"], p["id"]), p["team"])].append((nx_, ny_))
        heatmaps = {str(t): _grid(normalised[t], template) for t in (0, 1)}
        min_samples = int(20 * sample_fps)
        average_positions = [
            {"player": pid, "team": team, "x": round(float(np.mean([p[0] for p in pts])), 1), "y": round(float(np.mean([p[1] for p in pts])), 1), "seconds": round(len(pts) / sample_fps, 1)}
            for (pid, team), pts in per_player.items()
            if len(pts) >= min_samples
        ]
        average_positions.sort(key=lambda a: -a["seconds"])
        shot_map = []
        for e in live:
            if e["type"] == "shot" and e.get("x") is not None:
                sign = attack_sign(directions, e["team"], e["t"]) or 1
                x, y = (e["x"], e["y"]) if sign == 1 else (template["length"] - e["x"], template["width"] - e["y"])
                shot_map.append({"id": e["id"], "team": e["team"], "x": round(x, 1), "y": round(y, 1), "outcome": e.get("outcome"), "status": e["status"], "t": e["t"]})
    return {
        "teams": teams,
        "coverage": coverage_block,
        "momentum": momentum,
        "heatmaps": heatmaps,
        "averagePositions": average_positions,
        "shotMap": shot_map,
        "possessionSequences": sequences[-400:],
        "tracks": {"fragments": len(player_of), "players": len(set(player_of.values()))},
    }


# --------------------------------------------------------------------- entry


def analyse(result, calibration=None, review=None):
    """Full analytics for one analysed match. Pure function of its inputs."""
    frames = result["frames"]
    sample_fps = result.get("sampleFps") or 5
    start = result.get("analysedStart", 0) or 0
    duration = result.get("analysedDuration") or (frames[-1]["t"] - start if frames else 0)
    projected, template = project(frames, calibration)
    player_of = stitch_tracks(projected, sample_fps)
    directions = attacking_directions(projected, template, player_of)
    overrides = {}
    if review:
        _, overrides = apply_review([], review)
    if overrides.get("direction") in ("left", "right") and template:
        # Reviewer says which way team 0 attacks first; keep the detected switch time, if any.
        switch = directions["segments"][1]["start"] if directions and len(directions.get("segments", [])) == 2 else None
        first = overrides["direction"]
        other = "left" if first == "right" else "right"
        segments = [{"start": 0.0, "end": switch if switch is not None else math.inf, "team0Attacks": first}]
        if switch is not None:
            segments.append({"start": switch, "end": math.inf, "team0Attacks": other})
        directions = {"segments": segments, "confidence": 1.0, "source": "reviewer"}
    calibration_error = float(calibration.get("rms") or 0) if calibration else 0.0
    states, ball_velocity, velocities = control_states(projected, player_of, calibration_error)
    spells = control_spells(projected, states, sample_fps)
    in_play, dead_intervals = dead_ball(projected, velocities, ball_velocity, sample_fps)
    owner, sequences, possession_seconds, in_play_seconds = possession_sequences(projected, spells, in_play, sample_fps)
    events, kickoffs = detect_events(projected, states, spells, template, directions, sample_fps, in_play, dead_intervals)
    events, _ = apply_review(events, review)
    stats = summarise(projected, states, spells, events, template, directions, player_of, sample_fps, duration, start, in_play, owner, sequences, possession_seconds, in_play_seconds)
    serial_directions = None
    if directions:
        serial_directions = {
            **directions,
            "confidence": float(directions.get("confidence", 0)),
            "segments": [{**s, "end": None if s["end"] == math.inf else s["end"]} for s in directions["segments"]],
        }
    return {
        "schemaVersion": 1,
        "calibrated": template is not None,
        "template": template,
        "directions": serial_directions,
        "params": {k: list(v) if isinstance(v, tuple) else v for k, v in PARAMS.items()},
        "events": events,
        "kickoffs": kickoffs,
        "stats": stats,
        "review": {
            # How often the automatic events were right, from this match's decisions.
            "byType": _review_accuracy(events, review),
            "decisions": len(review.get("decisions", [])) if review else 0,
            "confirmed": sum(1 for e in events if e["status"] == "confirmed"),
            "rejected": sum(1 for e in events if e["status"] == "rejected"),
            "pending": sum(1 for e in events if e["status"] == "proposed" and e.get("needsReview", True)),
        },
        "positions": compact_positions(projected, player_of) if template else None,
    }


def _review_accuracy(events, review):
    from app.evaluation.metrics import review_metrics

    if not review:
        return {}
    return review_metrics({"events": events}, review)


def compact_positions(projected, player_of):
    """Per-frame pitch positions for the mini-pitch replay: [t, [[player, team, x, y]...], [bx, by] | None]."""
    out = []
    for f in projected:
        if not f["calibrated"]:
            out.append([f["t"], None, None])
            continue
        players = [
            [player_of.get(p["id"], p["id"]), p["team"], round(p["xy"][0], 1), round(p["xy"][1], 1)]
            for p in f["players"]
            if p["xy"]
        ]
        b = f["ball"]
        ball = [round(b["xy"][0], 1), round(b["xy"][1], 1), 1 if b["inferred"] else 0] if b and b["xy"] and b["onPitch"] else None
        out.append([f["t"], players, ball])
    return out
