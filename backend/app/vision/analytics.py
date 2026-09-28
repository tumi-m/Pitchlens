"""Match analytics from tracked detections: possession, events and team stats.

Everything here is post-processing of result.json (no model inference), so it
re-runs in seconds whenever the pitch calibration or a reviewer's decisions
change.

With a pitch calibration, positions are metres on the pitch and the rules use
physical distances, speeds and the goals. Without one, only image-space
possession and pass/turnover candidates are produced; shots, goals, heatmaps
and directions are reported as unavailable, never as zero.

The event logic follows the two-step design of Vidal-Codina et al. (2022,
"Automatic event detection in football using tracking data"): first decide who
controls the ball in each frame, then read events from changes of control
combined with the ball's movement and the pitch geometry. Control uses a
distance-plus-relative-speed test in the spirit of Link & Hoernig (2017,
"Individual ball possession in soccer"). Thresholds are adapted for small-sided
pitches, 5-6 sampled frames per second and image-derived positions with
measurement error; see PARAMS.
"""

import math
from collections import Counter, defaultdict

import numpy as np

from app.vision import pitch as pitchlib
from app.vision.metrics import possession_owner as image_owner

PARAMS = {
    # Control: the ball within reach of a player's feet...
    "controlRadius": 1.2,  # m, before adding calibration error
    # ...moving with the player rather than past them.
    "maxRelativeSpeed": 6.0,  # m/s
    # A second opponent this close as well makes it a contest, not control.
    "contestMargin": 0.6,  # m
    "minControlSeconds": 0.3,
    # Passes and turnovers.
    "maxTransferSeconds": 5.0,
    "minPassDistance": 2.0,  # m of ball travel between two players
    # Shots.
    "minShotSpeed": 7.0,  # m/s of the ball just after release
    "shotHorizon": 2.0,  # s to reach the goal line
    "goalMargin": 1.0,  # m either side of the posts still counted as on target band
    "maxShotDistance": 0.75,  # fraction of pitch length from the goal
    # Positions and tracks.
    "offPitchMargin": 2.0,  # m: projected positions further out are not trusted
    "maxPlayerSpeed": 8.5,  # m/s for linking track fragments
    "maxStitchGap": 3.0,  # s
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


def _velocities(projected, key):
    """Finite-difference ball velocity (m/s) per frame, None where unknown."""
    out = [None] * len(projected)
    for i in range(1, len(projected) - 1):
        a, b = projected[i - 1], projected[i + 1]
        if a["scene"] != b["scene"]:
            continue
        pa, pb = key(a), key(b)
        if pa is None or pb is None:
            continue
        dt = b["t"] - a["t"]
        if dt > 0:
            out[i] = ((pb[0] - pa[0]) / dt, (pb[1] - pa[1]) / dt)
    return out


def control_states(projected, player_of, calibration_error=0.0):
    """Per-frame ball state: control (by whom), contested, loose or unknown."""

    def ball_xy(f):
        b = f["ball"]
        return b["xy"] if b and b["xy"] and b["onPitch"] else None

    ball_velocity = _velocities(projected, ball_xy)
    player_tracks = defaultdict(dict)
    for i, f in enumerate(projected):
        for p in f["players"]:
            if p["xy"]:
                player_tracks[p["id"]][i] = p["xy"]
    radius = PARAMS["controlRadius"] + 1.5 * calibration_error
    states = []
    for i, f in enumerate(projected):
        b = f["ball"]
        if b is None:
            states.append({"state": "unknown"})
            continue
        if f["calibrated"] and ball_xy(f) is not None:
            bxy = ball_xy(f)
            near = []
            for p in f["players"]:
                if p["team"] not in (0, 1) or not p["xy"]:
                    continue
                d = math.dist(p["xy"], bxy)
                if d <= radius + PARAMS["contestMargin"]:
                    near.append((d, p))
            near.sort(key=lambda x: x[0])
            if not near or near[0][0] > radius:
                states.append({"state": "loose", "ball": bxy})
                continue
            d0, p0 = near[0]
            # Ball moving past the player rather than with them is not control.
            bv = ball_velocity[i]
            track = player_tracks[p0["id"]]
            pv = None
            if (i - 1) in track and (i + 1) in track and projected[i + 1]["t"] > projected[i - 1]["t"]:
                dt = projected[i + 1]["t"] - projected[i - 1]["t"]
                pv = ((track[i + 1][0] - track[i - 1][0]) / dt, (track[i + 1][1] - track[i - 1][1]) / dt)
            if bv is not None and pv is not None and math.dist(bv, pv) > PARAMS["maxRelativeSpeed"]:
                states.append({"state": "loose", "ball": bxy, "passing": True})
                continue
            rivals = [x for x in near[1:] if x[1]["team"] != p0["team"] and x[0] - d0 < PARAMS["contestMargin"]]
            if rivals:
                states.append({"state": "contested", "ball": bxy, "teams": [p0["team"], rivals[0][1]["team"]]})
                continue
            states.append({"state": "control", "ball": bxy, "player": player_of.get(p0["id"], p0["id"]), "fragment": p0["id"], "team": p0["team"], "distance": round(d0, 2)})
        else:
            # Uncalibrated: the image-space proximity rule used by metrics.py.
            owner = image_owner(
                [{"id": p["id"], "team": p["team"], "box": p["box"]} for p in f["players"]],
                b["raw"],
            )
            if owner is None:
                states.append({"state": "loose", "image": True})
            else:
                states.append({"state": "control", "player": player_of.get(owner["id"], owner["id"]), "fragment": owner["id"], "team": owner["team"], "image": True})
    return states, ball_velocity


def control_spells(projected, states, sample_fps):
    """Consecutive frames of control by one player, ignoring single-frame flicker."""
    spells = []
    minimum = max(2, math.ceil(PARAMS["minControlSeconds"] * sample_fps))
    current = None
    for i, (f, s) in enumerate(zip(projected, states)):
        key = (s.get("player"), s.get("team"), f["scene"]) if s["state"] == "control" else None
        if key and current and current["key"] == key and i - current["last"] <= 2:
            current["last"] = i
            current["frames"].append(i)
            continue
        if key:
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


def detect_shot(projected, template, directions, spell, next_first, ball_velocity, sample_fps=6):
    """A shot: right after release the ball heads fast towards the goal the team attacks."""
    if not template or spell["endBall"] is None:
        return None
    sign = attack_sign(directions, spell["team"], spell["end"])
    if sign is None:
        return None
    L, W, g = template["length"], template["width"], template["goalWidth"]
    goal_x = L if sign == 1 else 0.0
    origin = spell["endBall"]
    distance_to_goal = abs(goal_x - origin[0])
    if distance_to_goal > PARAMS["maxShotDistance"] * L:
        return None
    horizon_frames = int(math.ceil(PARAMS["shotHorizon"] * sample_fps)) + 1
    end = min(len(projected) - 1, spell["last"] + horizon_frames)
    if next_first is not None:
        end = min(end, next_first)
    path = [p for p in _ball_path(projected, spell["last"] + 1, end) if p[0] - spell["end"] <= PARAMS["shotHorizon"]]
    if not path:
        return None
    # Velocity from a straight-line fit over the first second after release:
    # the release frame itself often still shows the ball at the foot, so a
    # two-point difference would read a hard shot as a stationary ball.
    early = [(spell["end"], origin)] + [(t, xy) for t, xy, _, _ in path if t - spell["end"] <= 1.0]
    if len(early) < 3:
        return None
    ts = np.array([e[0] for e in early]) - spell["end"]
    xs = np.array([e[1][0] for e in early])
    ys = np.array([e[1][1] for e in early])
    if np.ptp(ts) <= 0:
        return None
    vx = float(np.polyfit(ts, xs, 1)[0])
    vy = float(np.polyfit(ts, ys, 1)[0])
    speed = math.hypot(vx, vy)
    if speed < PARAMS["minShotSpeed"] or vx * sign <= 0:
        return None
    # Where would the ball cross the goal line?
    time_to_line = (goal_x - origin[0]) / vx
    if time_to_line <= 0 or time_to_line > PARAMS["shotHorizon"] * 1.5:
        return None
    cross_y = origin[1] + vy * time_to_line
    centre = W / 2
    on_target = abs(cross_y - centre) <= g / 2
    in_band = abs(cross_y - centre) <= g / 2 + PARAMS["goalMargin"] * 3
    if not in_band:
        return None
    # Did the ball actually cross the line between the posts (goal candidate)?
    crossed = None
    for t, xy, onPitch, inferred in path:
        beyond = (xy[0] - goal_x) * sign
        if beyond >= -0.3 and abs(xy[1] - centre) <= g / 2 + 0.3:
            crossed = (t, xy)
            break
    return {
        "speed": round(speed, 1),
        "distance": round(distance_to_goal, 1),
        "onTarget": bool(on_target),
        "crossY": round(cross_y, 2),
        "goalCandidate": crossed is not None,
        "goalX": goal_x,
        "sign": sign,
    }


def detect_events(projected, states, spells, template, directions, sample_fps, ball_velocity):
    events = []

    def add(kind, t, team, confidence, **extra):
        events.append(
            {
                "id": f"ev-{len(events)}",
                "type": kind,
                "t": round(float(t), 2),
                "team": int(team) if team is not None else None,
                "confidence": round(float(confidence), 2),
                "status": "proposed",
                **extra,
            }
        )

    for k, spell in enumerate(spells):
        nxt = spells[k + 1] if k + 1 < len(spells) else None
        shot = detect_shot(projected, template, directions, spell, nxt["first"] if nxt else None, ball_velocity, sample_fps)
        if shot:
            outcome = "goal-candidate" if shot["goalCandidate"] else "on-target" if shot["onTarget"] else "off-target"
            if nxt and not shot["goalCandidate"] and nxt["team"] != spell["team"] and nxt["start"] - spell["end"] <= PARAMS["shotHorizon"] + 0.5:
                # Opponent collected it: saved (if on target) or blocked.
                outcome = "saved" if shot["onTarget"] else "blocked"
            add(
                "shot",
                spell["end"],
                spell["team"],
                min(0.9, 0.35 + 0.05 * shot["speed"] / 2),
                player=spell["player"],
                x=round(spell["endBall"][0], 2),
                y=round(spell["endBall"][1], 2),
                outcome=outcome,
                onTarget=shot["onTarget"] or shot["goalCandidate"],
                speed=shot["speed"],
                distance=shot["distance"],
                needsReview=True,
            )
            if shot["goalCandidate"]:
                add(
                    "goal-candidate",
                    spell["end"],
                    spell["team"],
                    0.3,
                    player=spell["player"],
                    x=round(spell["endBall"][0], 2),
                    y=round(spell["endBall"][1], 2),
                    needsReview=True,
                    note="Ball projected across the goal line between the posts. Only a reviewer can confirm a goal.",
                )
            continue
        if not nxt or nxt["scene"] != spell["scene"]:
            continue
        gap = nxt["start"] - spell["end"]
        if gap <= 0 or gap > PARAMS["maxTransferSeconds"]:
            continue
        if nxt["player"] == spell["player"]:
            continue
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
            add("pass", spell["end"], spell["team"], min(0.9, confidence), outcome="complete", length=round(travel, 1) if travel is not None else None, **common)
        else:
            short = travel is not None and travel < PARAMS["minPassDistance"]
            if short:
                add("tackle", nxt["start"], nxt["team"], min(0.8, confidence - 0.05), lostBy=spell["team"], **common)
            else:
                add("pass", spell["end"], spell["team"], min(0.85, confidence), outcome="intercepted", length=round(travel, 1) if travel is not None else None, **common)
                add("interception", nxt["start"], nxt["team"], min(0.85, confidence), lostBy=spell["team"], **common)
    # Ball leaving the pitch (not for walled cages).
    if template and not template.get("walls"):
        L, W = template["length"], template["width"]
        out_run = 0
        for i, f in enumerate(projected):
            b = f["ball"]
            outside = bool(b and b["xy"] and not b["inferred"] and (b["xy"][0] < -0.75 or b["xy"][0] > L + 0.75 or b["xy"][1] < -0.75 or b["xy"][1] > W + 0.75))
            out_run = out_run + 1 if outside else 0
            if out_run == 2:
                add("out", projected[i - 1]["t"], None, 0.4, x=round(b["xy"][0], 2), y=round(b["xy"][1], 2), needsReview=False)
    events.sort(key=lambda e: e["t"])
    for i, e in enumerate(events):
        e["id"] = f"ev-{i}"
    return events


# --------------------------------------------------------------------- review


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
            added.append(
                {
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
            )
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


def summarise(projected, states, spells, events, template, directions, player_of, sample_fps, duration, start):
    dt = 1 / sample_fps
    control = [0.0, 0.0]
    contested = 0.0
    loose = 0.0
    unknown = 0.0
    territory = [0.0, 0.0]  # control seconds in the opponent's half
    for f, s in zip(projected, states):
        if s["state"] == "control":
            control[s["team"]] += dt
            if template and s.get("ball") is not None:
                sign = attack_sign(directions, s["team"], f["t"])
                if sign is not None and (s["ball"][0] - template["length"] / 2) * sign > 0:
                    territory[s["team"]] += dt
        elif s["state"] == "contested":
            contested += dt
        elif s["state"] == "loose":
            loose += dt
        else:
            unknown += dt
    total_control = sum(control)
    live = [e for e in events if e["status"] != "rejected"]

    def count(kind, team, **match):
        items = [e for e in live if e["type"] == kind and e.get("team") == team and all(e.get(k) == v for k, v in match.items())]
        return {
            "value": len(items),
            "confirmed": sum(1 for e in items if e["status"] == "confirmed"),
            "pending": sum(1 for e in items if e["status"] == "proposed"),
        }

    teams = []
    for team in (0, 1):
        passes = count("pass", team)
        complete = count("pass", team, outcome="complete")
        shots = count("shot", team)
        on_target = count("shot", team, onTarget=True)
        goals_confirmed = sum(1 for e in live if e["type"] == "goal" and e.get("team") == team and e["status"] == "confirmed") + sum(
            1 for e in live if e["type"] == "goal-candidate" and e.get("team") == team and e["status"] == "confirmed"
        )
        teams.append(
            {
                "controlSeconds": round(control[team], 1),
                "possession": round(control[team] / total_control * 100, 1) if total_control else None,
                "passes": passes,
                "passesComplete": complete,
                "passAccuracy": round(complete["value"] / passes["value"] * 100, 1) if passes["value"] else None,
                "shots": shots if template else None,
                "shotsOnTarget": on_target if template else None,
                "goals": {"value": goals_confirmed, "candidates": count("goal-candidate", team)["value"]} if template else None,
                "interceptions": count("interception", team),
                "tackles": count("tackle", team),
                "territory": round(territory[team] / control[team] * 100, 1) if template and control[team] else None,
            }
        )
    covered = total_control + contested + loose
    coverage = {
        "controlPercent": round(total_control / duration * 100, 1) if duration else 0,
        "ballStatePercent": round(covered / duration * 100, 1) if duration else 0,
        "calibratedPercent": round(sum(1 for f in projected if f["calibrated"]) / max(1, len(projected)) * 100, 1),
        "contestedSeconds": round(contested, 1),
        "looseSeconds": round(loose, 1),
        "unknownSeconds": round(unknown, 1),
    }
    # Momentum: per-minute control difference, with control in the opponent's half counting double.
    minutes = max(1, math.ceil(duration / 60))
    per_minute = [[0.0, 0.0] for _ in range(minutes)]
    for f, s in zip(projected, states):
        if s["state"] != "control":
            continue
        m = min(minutes - 1, max(0, int((f["t"] - start) // 60)))
        weight = 1.0
        if template and s.get("ball") is not None:
            sign = attack_sign(directions, s["team"], f["t"])
            if sign is not None and (s["ball"][0] - template["length"] / 2) * sign > 0:
                weight = 2.0
        per_minute[m][s["team"]] += weight
    momentum = [round((a - b) / (a + b), 2) if a + b >= 2 else None for a, b in per_minute]
    heatmaps = None
    average_positions = None
    shot_map = None
    if template:
        normalised = {0: [], 1: []}
        per_player = defaultdict(list)
        for f in projected:
            for p in f["players"]:
                if p["team"] not in (0, 1) or not p["xy"]:
                    continue
                sign = attack_sign(directions, p["team"], f["t"]) or 1
                x, y = p["xy"]
                # Everyone attacks to the right in the normalised view.
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
        "coverage": coverage,
        "momentum": momentum,
        "heatmaps": heatmaps,
        "averagePositions": average_positions,
        "shotMap": shot_map,
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
        # Reviewer says which way team 0 attacks in the first half; keep the detected switch time if any.
        switch = directions["segments"][1]["start"] if directions and len(directions.get("segments", [])) == 2 else None
        first = overrides["direction"]
        other = "left" if first == "right" else "right"
        segments = [{"start": 0.0, "end": switch if switch is not None else math.inf, "team0Attacks": first}]
        if switch is not None:
            segments.append({"start": switch, "end": math.inf, "team0Attacks": other})
        directions = {"segments": segments, "confidence": 1.0, "source": "reviewer"}
    calibration_error = float(calibration.get("rms") or 0) if calibration else 0.0
    states, ball_velocity = control_states(projected, player_of, calibration_error)
    spells = control_spells(projected, states, sample_fps)
    events = detect_events(projected, states, spells, template, directions, sample_fps, ball_velocity)
    events, _ = apply_review(events, review)
    stats = summarise(projected, states, spells, events, template, directions, player_of, sample_fps, duration, start)
    serial_directions = None
    if directions:
        serial_directions = {
            **directions,
            "segments": [{**s, "end": None if s["end"] == math.inf else s["end"]} for s in directions["segments"]],
        }
    return {
        "schemaVersion": 1,
        "calibrated": template is not None,
        "template": template,
        "directions": serial_directions,
        "params": PARAMS,
        "events": events,
        "stats": stats,
        "review": {
            "decisions": len(review.get("decisions", [])) if review else 0,
            "confirmed": sum(1 for e in events if e["status"] == "confirmed"),
            "rejected": sum(1 for e in events if e["status"] == "rejected"),
            "pending": sum(1 for e in events if e["status"] == "proposed" and e.get("needsReview", True)),
        },
        "positions": compact_positions(projected, player_of) if template else None,
    }


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
