"""Measure the event engine against human-labelled events on public tracking data.

    python scripts/evaluate_on_tracking_data.py METRICA_DATA_DIR [--game 1] [--fps 5]
        [--noise 0.3] [--ball-noise 0.5] [--drop 0.3] [--feet-drop 0.5] [--fragment 5]
        [--visible 0.4 --run 2] [--team-flip 0.2] [--camera broadcast] [--ball-fp 0.2] [--seed 1]
    python scripts/evaluate_on_tracking_data.py METRICA_DATA_DIR --identity fixed|pan [--speed-miss 2]
        [--game 2] [--minutes 0] [--seed 1]

Owner-like footage (E16): --visible 0.4 --run 2 (ball seen in ~25% of all
frames in bursts of ~2 samples), --team-flip 0.2 (a fragment's whole kit label
wrong, as a per-track kit vote fails), --camera broadcast (640x360 perspective
view panning with the ball, no calibration).

Uses the Metrica Sports sample data (github.com/metrica-sports/sample-data;
please acknowledge Metrica Sports; the data is not redistributed here). The
25 fps optical tracking is converted into Pitchlens frames on a calibrated
105 x 68 m pitch and degraded to resemble one amateur camera: sampling rate,
position noise, the ball missing at random and more often at players' feet,
and track IDs broken into fragments. The real analytics (app/vision/analytics)
then run unchanged and are scored against Metrica's hand-labelled events
(passes, shots, recoveries) and event-derived possession.

This validates the event logic, not the vision pipeline: positions here are
far better than what a 360p camera produces before degradation. Game 2 is the
held-out match: tune on game 1, report game 2. Seeds are fixed (--seed).

Sampling rate: --fps is rounded the way the engine rounds it on a 25 fps video
(every ceil(25 / fps)-th frame: 6 -> 5.0, 10 -> 8.33, 12.5 -> 12.5); the rate
analysed is reported as settings.effectiveFps. --visible/--run and the
default ball drops are drawn per sample, so the occlusion timescale changes
with the rate.

Event definitions: "pass" keeps the E8 scoring (every pass event against
Metrica's completed passes). "passAttempt" counts every BALL LOST outside
NOT_PASS_LOSS as a failed pass, including those with an empty subtype (30 in
game 2), which may be lost dribbles. "interception" is scored against RECOVERY
INTERCEPTION, "turnover" (interceptions and tackles) against every RECOVERY,
"tackle" against RECOVERY THEFT.

Identity (--identity, E19): the default mode starts a new ID at the same
position on the next frame, so any stitcher rejoins everything and identity is
not measured. This mode instead films the players with a 640x360 camera
(fixed, or panning after the ball), misses and occludes detections, runs the
real ByteTracker and then analytics.stitch_tracks, and reports the junction
links it made (correct, wrong, precision) first, then purity next to the
unstitched ceiling; IDF1 is secondary (an over-merging stitcher wins on it).
A full match takes ~5-7 minutes.
"""

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.metrics import evaluate_events, evaluate_possession  # noqa: E402
from app.vision import analytics  # noqa: E402

L, W = 105.0, 68.0
SCALE = 6.0  # "pixels" per metre in the synthetic image


def read_tracking(path):
    with open(path) as f:
        rows = list(csv.reader(f))
    header = rows[2]
    players = [h for h in header if h.startswith("Player")]
    columns = {}
    for i, h in enumerate(header):
        if h.startswith("Player") or h == "Ball":
            columns[h] = i
    out = []
    for row in rows[3:]:
        period, frame, t = int(row[0]), int(row[1]), float(row[2])
        positions = {}
        for name, i in columns.items():
            x, y = row[i], row[i + 1]
            if x not in ("NaN", "") and y not in ("NaN", ""):
                positions[name] = (float(x) * L, float(y) * W)
        out.append((period, frame, t, positions))
    return players, out


def read_events(path):
    with open(path) as f:
        return list(csv.DictReader(f))


class BroadcastCamera:
    """Pinhole camera behind the near touchline, panning with the ball (640x360)."""

    def __init__(self, width=640, height=360, hfov=50.0, back=15.0, height_m=18.0):
        self.w, self.h = width, height
        self.f = width / 2 / math.tan(math.radians(hfov / 2))
        self.back, self.hm = back, height_m
        self.cx, self.ty = L / 2, W / 2

    def aim(self, ball):
        if ball is not None:
            self.cx += 0.35 * (min(90.0, max(15.0, ball[0])) - self.cx)
            self.ty += 0.35 * (min(50.0, max(20.0, ball[1])) - self.ty)
        C = np.array([self.cx, -self.back, self.hm])
        f = np.array([self.cx, self.ty, 0.0]) - C
        f /= np.linalg.norm(f)
        r = np.cross(f, [0, 0, 1.0])
        r /= np.linalg.norm(r)
        self.C, self.R = C, np.stack([r, np.cross(f, r), f])

    def project(self, x, y, z=0.0):
        p = self.R @ (np.array([x, y, z]) - self.C)
        if p[2] <= 1.0:
            return None
        u, v = self.w / 2 + self.f * p[0] / p[2], self.h / 2 + self.f * p[1] / p[2]
        return (u, v, p[2]) if 0 <= u < self.w and 0 <= v < self.h else None


def effective_rate(fps):
    """(stride, rate) the engine would analyse on Metrica's 25 fps video when asked
    for `fps`: every ceil(25 / fps)-th frame (the tolerance takes a typed 8.333,
    25 / 3 rounded, as stride 3)."""
    stride = max(1, math.ceil(25 / fps - 1e-3))
    return stride, 25 / stride


def markov_visible(n, visible, run, rng):
    """Bursty ball visibility: a two-state chain with mean visible run `run` samples."""
    p_off, p_on = 1 / run, visible / (run * (1 - visible))
    on, out = rng.random() < visible, []
    for _ in range(n):
        on = (rng.random() >= p_off) if on else (rng.random() < p_on)
        out.append(on)
    return out


def build(data_dir, game, fps, noise, ball_noise, drop, feet_drop, fragment, seed=1,
          visible=None, run=2.0, team_flip=0.0, camera="top", ball_fp=0.0):
    base = Path(data_dir) / f"Sample_Game_{game}"
    _, home = read_tracking(base / f"Sample_Game_{game}_RawTrackingData_Home_Team.csv")
    _, away = read_tracking(base / f"Sample_Game_{game}_RawTrackingData_Away_Team.csv")
    events = read_events(base / f"Sample_Game_{game}_RawEventsData.csv")
    stride, fps = effective_rate(fps)
    rng = random.Random(seed)
    # Separate streams, so the default degradation stays identical to E8.
    flip_rng, burst_rng, fp_rng = random.Random(seed + 100), random.Random(seed + 200), random.Random(seed + 300)
    n_rows = len(range(0, min(len(home), len(away)), stride))
    shown = markov_visible(n_rows, visible, run, burst_rng) if visible is not None and visible < 1 else None
    cam = BroadcastCamera() if camera == "broadcast" else None
    last_seen = {}
    ids = {}
    next_id = [1]

    def fragment_id(name, t, k):
        """Break each real player into fragments (a new ID every ~`fragment` s on average,
        and on re-entering a panning camera's view); a whole fragment's kit may be wrong."""
        current = ids.get(name)
        reentry = cam is not None and last_seen.get(name) not in (None, k - 1)
        if current is None or t >= current[1] or reentry:
            flipped = bool(team_flip) and flip_rng.random() < team_flip
            ids[name] = (next_id[0], t + rng.expovariate(1 / fragment) if fragment else math.inf, flipped)
            next_id[0] += 1
        last_seen[name] = k
        return ids[name][0], ids[name][2]

    frames = []
    for row, k in enumerate(range(0, min(len(home), len(away)), stride)):
        period, _, t, hp = home[k]
        _, _, _, ap = away[k]
        bpos = hp.get("Ball")
        if cam is not None:
            cam.aim(bpos)
        players, feet = [], []
        for team, positions in ((0, hp), (1, ap)):
            for name, (x, y) in positions.items():
                if name == "Ball":
                    continue
                x += rng.gauss(0, noise)
                y += rng.gauss(0, noise)
                if cam is None:
                    px, py = x * SCALE, y * SCALE
                    box = [px - 4, py - 24, px + 4, py]
                else:
                    foot, head = cam.project(x, y), cam.project(x, y, 1.8)
                    if foot is None or head is None:
                        continue
                    h = foot[1] - head[1]
                    box = [foot[0] - 0.22 * h, head[1], foot[0] + 0.22 * h, foot[1]]
                feet.append((x, y))
                fid, flipped = fragment_id(f"{team}-{name}", t, row)
                label = (1 - team) if flipped else team
                players.append({"id": fid, "team": label if rng.random() > 0.1 else -1, "role": "player", "box": box, "confidence": 0.9})
        ball = None
        if bpos is not None:
            if shown is not None:
                keep = shown[row]
            else:
                near = min((math.dist(bpos, p) for n, p in list(hp.items()) + list(ap.items()) if n != "Ball"), default=99)
                p_drop = drop + (feet_drop if near < 1.5 else 0)
                keep = rng.random() >= p_drop
            if keep:
                bx, by = bpos[0] + rng.gauss(0, ball_noise), bpos[1] + rng.gauss(0, ball_noise)
                if ball_fp and feet and fp_rng.random() < ball_fp:
                    # A boot or a head detected as the ball (E14).
                    fx, fy = feet[fp_rng.randrange(len(feet))]
                    bx, by = fx + fp_rng.gauss(0, 0.3), fy + fp_rng.gauss(0, 0.3)
                if cam is None:
                    ball = {"x": bx * SCALE, "y": by * SCALE - 1, "box": [bx * SCALE - 1.5, by * SCALE - 3, bx * SCALE + 1.5, by * SCALE], "confidence": 0.6, "trackId": 1}
                else:
                    c = cam.project(bx, by, 0.11)
                    if c is not None:
                        r = max(1.0, 0.11 * cam.f / c[2])
                        ball = {"x": c[0], "y": c[1], "box": [c[0] - r, c[1] - r, c[0] + r, c[1] + r], "confidence": 0.6, "trackId": 1}
        frames.append({"t": round(t, 3), "scene": period - 1, "players": players, "ball": ball, "camera": [1, 0, 0, 0, 1, 0]})
    size = (cam.w, cam.h) if cam is not None else (int(L * SCALE), int(W * SCALE))
    result = {"frames": frames, "sampleFps": fps, "analysedStart": frames[0]["t"], "analysedDuration": frames[-1]["t"] - frames[0]["t"],
              "video": {"width": size[0], "height": size[1]}}
    if cam is not None:
        return result, None, events
    H = np.array([[1 / SCALE, 0, 0], [0, 1 / SCALE, 0], [0, 0, 1]])
    template = {"length": L, "width": W, "goalWidth": 7.32, "centreRadius": 9.15, "areaRadius": None, "areaDepth": 16.5, "areaWidth": 40.32, "penaltySpot": 11.0, "walls": False}
    calibration = {"state": "ready", "template": template, "k1": 0.0, "size": [int(L * SCALE), int(W * SCALE)], "rms": 0.3,
                   "frames": [{"H": H.reshape(-1).tolist(), "d": 0} for _ in frames]}
    return result, calibration, events


NOT_PASS_LOSS = {"THEFT", "FORCED", "HAND BALL", "OFFSIDE", "END HALF", "FORCED-END HALF", "REFEREE HIT"}


def ground_truth(events):
    """Metrica events mapped to Pitchlens event types; team 0 = Home."""
    team = {"Home": 0, "Away": 1}
    out = []
    for e in events:
        t = float(e["Start Time [s]"])
        kind, sub = e["Type"], e["Subtype"]
        tm = team.get(e["Team"])
        if kind == "PASS":
            out.append({"t": t, "type": "pass", "team": tm})
            out.append({"t": t, "type": "passAttempt", "team": tm})
            out.append({"t": t, "type": "passComplete", "team": tm})
        elif kind == "BALL LOST" and sub not in NOT_PASS_LOSS:
            # A failed pass: Pitchlens counts it as a pass attempt (outcome "intercepted").
            out.append({"t": t, "type": "passAttempt", "team": tm})
        elif kind == "SHOT":
            outcome = "goal" if "GOAL" in sub else "saved" if "SAVED" in sub else "blocked" if "BLOCKED" in sub else "off-target"
            out.append({"t": t, "type": "shot", "team": tm, "outcome": outcome})
            if "GOAL" in sub:
                out.append({"t": t, "type": "goal", "team": tm})
        elif kind == "RECOVERY":
            # Pitchlens "interception" is any won ball that is not a short tackle,
            # so it is also scored against every recovery ("turnover").
            out.append({"t": t, "type": "turnover", "team": tm})
            if "INTERCEPTION" in sub:
                out.append({"t": t, "type": "interception", "team": tm})
            if "THEFT" in sub:
                out.append({"t": t, "type": "tackle", "team": tm})
    return out


def possession_truth(events):
    """Team in possession between events: from a team's pass/recovery/set piece until the next event."""
    team = {"Home": 0, "Away": 1}
    rows = sorted(events, key=lambda e: float(e["Start Time [s]"]))
    spans = []
    for a, b in zip(rows, rows[1:]):
        if a["Type"] in ("PASS", "RECOVERY", "SET PIECE", "CHALLENGE") and a["Team"] in team and b["Type"] != "SET PIECE":
            start, end = float(a["Start Time [s]"]), float(b["Start Time [s]"])
            if 0 < end - start < 30:
                spans.append({"start": start, "end": end, "team": team[a["Team"]]})
    return spans


# --------------------------------------------------------------------- identity


class FollowCamera:
    """Pinhole camera on the halfway line, 22 m behind the near touchline and 11 m
    up (640x360, 46 degrees wide): fixed on the pitch, or panning after the ball
    with ~1 s lag."""

    WIDTH, HEIGHT = 640, 360

    def __init__(self, mode):
        self.mode = mode
        self.C = np.array([L / 2, -22.0, 11.0])
        f = (self.WIDTH / 2) / math.tan(math.radians(46) / 2)
        self.K = np.array([[f, 0, self.WIDTH / 2], [0, f, self.HEIGHT / 2], [0, 0, 1]])

    def P(self, target_x):
        forward = np.array([target_x if self.mode == "pan" else L / 2, W * 0.55, 0.0]) - self.C
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, np.array([0, 0, 1.0]))
        right /= np.linalg.norm(right)
        R = np.stack([right, np.cross(forward, right), forward])  # image right, down, forward
        return self.K @ np.hstack([R, (-R @ self.C)[:, None]])

    @staticmethod
    def project(P, pts):
        pts = np.asarray(pts, float)
        h = np.hstack([pts, np.ones((len(pts), 1))]) @ P.T
        return h[:, :2] / h[:, 2:3], h[:, 2]


def build_identity(data_dir, game, mode, fps=5.0, minutes=0, seed=1, speed_miss=0.0, miss_rate=0.05, long_share=0.4,
                   short_return=0.4, long_return=0.06, occlusion=0.7, box_noise=0.04, cam_noise=0.5, team_noise=0.02,
                   unknown=0.07, ball_visible=0.25, ball_sighting=2.0):
    """Engine-like track fragments with known identities (E19).

    Metrica positions are seen by FollowCamera; detections get bursty misses (a
    two-state chain with short and long misses; with speed_miss the miss rate
    rises with the player's speed), occlusion of the farther of two overlapping
    boxes, box noise proportional to the box height and per-detection kit-label
    noise. The real ByteTracker (app.vision.tracking) links them with
    camera-motion matrices fitted like the engine's, then the engine's
    stabilise_roles/vote_teams run. Every detection keeps its true player in
    "gt" (the tracker copies observation keys). Returns the result (each frame
    carries "H", the pitch-to-image ground homography).
    """
    import cv2

    from app.vision.engine import stabilise_roles, vote_teams
    from app.vision.tracking import ByteTracker

    stride, fps = effective_rate(fps)
    width, height = FollowCamera.WIDTH, FollowCamera.HEIGHT
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    base = Path(data_dir) / f"Sample_Game_{game}"
    _, home = read_tracking(base / f"Sample_Game_{game}_RawTrackingData_Home_Team.csv")
    _, away = read_tracking(base / f"Sample_Game_{game}_RawTrackingData_Away_Team.csv")
    cam = FollowCamera(mode)
    tracker = ByteTracker()
    target, prev_P, period_prev, scene, cutoff = L / 2, None, None, 0, None
    det_state, prev_pos = {}, {}  # true player -> (missing?, long miss?); position one sample ago
    ball_vis = False
    p_ball_off = 1 / ball_sighting
    p_ball_on = p_ball_off * ball_visible / (1 - ball_visible)
    kit = {0: np.array([120.0, 170.0, 150.0]), 1: np.array([200.0, 128.0, 128.0])}
    gx, gy = np.meshgrid(np.linspace(0, L, 15), np.linspace(0, W, 9))
    ground = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], 1)

    def inside(uv):
        return (uv[:, 0] > 0) & (uv[:, 0] < width) & (uv[:, 1] > 0) & (uv[:, 1] < height)

    frames = []
    for k in range(0, min(len(home), len(away)), stride):
        period, _, t, hp = home[k]
        _, _, _, ap = away[k]
        if cutoff is None:
            cutoff = t + minutes * 60 if minutes else math.inf
        if t > cutoff:
            break
        cut = period_prev is not None and period != period_prev
        if cut:
            scene += 1
            prev_P = None
        period_prev = period
        bpos = hp.get("Ball")
        if mode == "pan" and bpos is not None:
            desired = min(max(bpos[0], 18.0), L - 18.0)
            target += (desired - target) * (1 - math.exp(-(1 / fps) / 1.0))
        P = cam.P(target)
        # Camera motion like the engine: a partial affine between frames, plus noise.
        matrix, camera = np.array([[1, 0, 0], [0, 1, 0]], np.float32), None
        if prev_P is not None:
            a, za = FollowCamera.project(prev_P, ground)
            b, zb = FollowCamera.project(P, ground)
            ok = (za > 0) & (zb > 0) & inside(a) & inside(b)
            m = cv2.estimateAffinePartial2D(a[ok].astype(np.float32), b[ok].astype(np.float32))[0] if ok.sum() >= 4 else None
            if m is not None:
                m = m.astype(float)
                m[:, 2] += nrng.normal(0, cam_noise, 2)
                matrix, camera = m.astype(np.float32), [round(float(v), 5) for v in m.reshape(-1)]
        prev_P = P
        candidates = []
        for team, positions in ((0, hp), (1, ap)):
            for name, (x, y) in positions.items():
                if name == "Ball":
                    continue
                gid = f"{team}-{name}"
                pp, prev_pos[gid] = prev_pos.get(gid), (x, y)
                uv, z = FollowCamera.project(P, [(x, y, 0.0)])
                (fx, fy), (_, hy) = uv[0], FollowCamera.project(P, [(x, y, 1.8)])[0][0]
                if z[0] <= 0:
                    continue
                tall = fy - hy
                if not (-5 <= fx <= width + 5 and 0 <= fy <= height + 5) or tall < 6:
                    det_state.pop(gid, None)
                    continue
                missing, long_miss = det_state.get(gid, (False, False))
                if missing:
                    if rng.random() < (long_return if long_miss else short_return):
                        missing = False
                else:
                    speed = math.dist(pp, (x, y)) * fps if pp is not None else 0.0
                    rate = miss_rate * (1 + speed_miss * speed / 3.0) / (1 + speed_miss * 0.5)
                    if rng.random() < rate:
                        missing, long_miss = True, rng.random() < long_share
                det_state[gid] = (missing, long_miss)
                if not missing:
                    candidates.append((gid, team, fx, fy, tall))
        # Occlusion: the farther (higher in the image) of two strongly overlapping boxes is often lost.
        candidates.sort(key=lambda c: -c[3])
        boxes, kept = [], []
        for gid, team, fx, fy, h in candidates:
            box = np.array([fx - 0.2 * h, fy - h, fx + 0.2 * h, fy])
            hidden = False
            for b in boxes:
                ix = max(0, min(box[2], b[2]) - max(box[0], b[0]))
                iy = max(0, min(box[3], b[3]) - max(box[1], b[1]))
                if ix * iy > 0.5 * (box[2] - box[0]) * (box[3] - box[1]):
                    hidden = rng.random() < occlusion
                    break
            boxes.append(box)
            if not hidden:
                kept.append((gid, team, fx, fy, h))
        observations = []
        for gid, team, fx, fy, h in kept:
            sx, sy = nrng.normal(0, box_noise * h), nrng.normal(0, box_noise * h)
            hh = h * (1 + nrng.normal(0, 0.06))
            box = [fx + sx - 0.2 * hh, fy + sy - hh, fx + sx + 0.2 * hh, fy + sy]
            r = rng.random()
            label = -1 if r < unknown else (1 - team) if r < unknown + team_noise else team
            feature = kit[label if label in (0, 1) else team] + nrng.normal(0, 8, 3)
            observations.append({
                "team": label, "role": "player", "box": [round(float(v), 1) for v in box],
                "confidence": round(float(min(0.95, max(0.15, nrng.normal(0.7, 0.15)))), 3),
                "kitFeature": [round(float(v), 1) for v in feature], "gt": gid,
            })
        players = tracker.update(observations, t, matrix, cut=cut)
        ball = None
        if bpos is not None:  # bursty sightings (mean 2 samples, ~25% of frames)
            ball_vis = (rng.random() >= p_ball_off) if ball_vis else (rng.random() < p_ball_on)
            if ball_vis:
                uv, z = FollowCamera.project(P, [(bpos[0], bpos[1], 0.0)])
                bx, by = uv[0]
                if z[0] > 0 and 0 <= bx < width and 0 <= by < height:
                    bx, by = bx + nrng.normal(0, 1.5), by + nrng.normal(0, 1.5)
                    ball = {"x": round(bx, 1), "y": round(by - 1, 1), "box": [bx - 1.5, by - 3, bx + 1.5, by], "confidence": 0.6, "trackId": 1}
        frames.append({"t": round(t, 3), "scene": scene, "players": players, "ball": ball, "camera": camera, "H": P[:, [0, 1, 3]].tolist()})
    stabilise_roles(frames)
    vote_teams(frames)
    return {"frames": frames, "sampleFps": fps, "analysedStart": frames[0]["t"], "analysedDuration": frames[-1]["t"] - frames[0]["t"],
            "video": {"width": width, "height": height}}


def exact_calibration(result):
    """The true per-frame image-to-pitch calibration of a build_identity() result."""
    template = {"length": L, "width": W, "goalWidth": 7.32, "centreRadius": 9.15, "areaRadius": None, "areaDepth": 16.5, "areaWidth": 40.32, "penaltySpot": 11.0, "walls": False}
    frames = []
    for f in result["frames"]:
        H = np.linalg.inv(np.asarray(f["H"]))
        frames.append({"H": (H / H[2, 2]).reshape(-1).tolist(), "d": 0})
    return {"state": "ready", "template": template, "k1": 0.0, "size": [FollowCamera.WIDTH, FollowCamera.HEIGHT], "rms": 0.3, "frames": frames}


def junction_links(frames, player_of):
    """(correct, wrong) links a stitcher made: consecutive fragments of one stitched
    player, judged by the true player at the end of one and the start of the next."""
    first, last, start = {}, {}, {}
    for f in frames:
        for p in f["players"]:
            who = (p["gt"], f["scene"])
            first.setdefault(p["id"], who)
            start.setdefault(p["id"], f["t"])
            last[p["id"]] = who
    chains = defaultdict(list)
    for fragment, player in player_of.items():
        if fragment in start:
            chains[player].append(fragment)
    correct = wrong = 0
    for fragments in chains.values():
        fragments.sort(key=lambda x: start[x])
        for a, b in zip(fragments, fragments[1:]):
            if last[a] == first[b]:
                correct += 1
            else:
                wrong += 1
    return correct, wrong


def identity_metrics(frames, player_of, fps=5.0):
    """Link counts and precision first; then purity (share of detections in a
    stitched player that belong to its main true player), also over players
    followed for 20 s or more (average-position dots); IDF1 last, since an
    over-merging stitcher can win on it."""
    from scipy.optimize import linear_sum_assignment

    correct, wrong = junction_links(frames, player_of)
    per_player = defaultdict(Counter)
    for f in frames:
        for p in f["players"]:
            per_player[player_of.get(p["id"], ("fragment", p["id"]))][p["gt"]] += 1
    n = sum(sum(c.values()) for c in per_player.values())
    long_ = [c for c in per_player.values() if sum(c.values()) >= 20 * fps]
    players = list(per_player)
    truths = sorted({g for c in per_player.values() for g in c})
    overlap = np.array([[per_player[p][g] for g in truths] for p in players], float)
    rows, cols = linear_sum_assignment(-overlap)
    return {
        "fragments": len({p["id"] for f in frames for p in f["players"]}),
        "players": len(per_player),
        "correctLinks": correct,
        "wrongLinks": wrong,
        "linkPrecision": round(correct / (correct + wrong), 3) if correct + wrong else None,
        "purity": round(sum(c.most_common(1)[0][1] for c in per_player.values()) / n, 4) if n else None,
        "purity20s": round(sum(c.most_common(1)[0][1] for c in long_) / max(1, sum(sum(c.values()) for c in long_)), 4),
        "players20s": len(long_),
        "idf1": round(float(overlap[rows, cols].sum()) / n, 4) if n else None,
    }


def evaluate_identity(args):
    """Score analytics.stitch_tracks on build_identity() fragments, in image space and calibrated."""
    result = build_identity(args.data, args.game, args.identity, fps=args.fps, minutes=args.minutes, seed=args.seed, speed_miss=args.speed_miss)
    frames, fps = result["frames"], result["sampleFps"]
    report = {"settings": vars(args) | {"data": str(args.data), "effectiveFps": fps}}
    for name, calibration in (("image", None), ("calibrated", exact_calibration(result))):
        projected, _ = analytics.project(frames, calibration)
        report[name] = {
            # Every fragment its own player: the purity ceiling, no links.
            "unstitched": identity_metrics(frames, {p["id"]: p["id"] for f in frames for p in f["players"]}, fps),
            "stitched": identity_metrics(frames, analytics.stitch_tracks(projected, fps), fps),
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("data", type=Path)
    parser.add_argument("--game", type=int, default=1)
    parser.add_argument("--fps", type=float, default=5,
                        help="sampling rate, rounded like the engine's on 25 fps video (6 -> 5.0, 10 -> 8.33, 12.5 -> 12.5); "
                             "--visible/--run count samples, so the occlusion timescale changes with it")
    parser.add_argument("--noise", type=float, default=0.3)
    parser.add_argument("--ball-noise", type=float, default=0.5)
    parser.add_argument("--drop", type=float, default=0.3)
    parser.add_argument("--feet-drop", type=float, default=0.3)
    parser.add_argument("--fragment", type=float, default=5.0)
    parser.add_argument("--minutes", type=float, default=0, help="limit to the first N minutes (0 = all)")
    parser.add_argument("--visible", type=float, default=None, help="bursty ball: share of in-play samples seen (replaces --drop/--feet-drop)")
    parser.add_argument("--run", type=float, default=2.0, help="bursty ball: mean visible run in samples")
    parser.add_argument("--team-flip", type=float, default=0.0, help="share of track fragments whose whole kit label is wrong")
    parser.add_argument("--camera", choices=("top", "broadcast"), default="top", help="broadcast = panning 640x360 perspective, uncalibrated")
    parser.add_argument("--ball-fp", type=float, default=0.0, help="share of ball sightings replaced by a player's feet")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--identity", choices=("fixed", "pan"), default=None,
                        help="score track stitching on simulated tracker fragments (camera fixed or panning) instead of events")
    parser.add_argument("--speed-miss", type=float, default=0.0, help="identity mode: detector misses rise with player speed")
    args = parser.parse_args()
    if args.identity:
        print(json.dumps(evaluate_identity(args), indent=1, default=str))
        return
    result, calibration, events = build(args.data, args.game, args.fps, args.noise, args.ball_noise, args.drop, args.feet_drop, args.fragment,
                                        seed=args.seed, visible=args.visible, run=args.run, team_flip=args.team_flip, camera=args.camera, ball_fp=args.ball_fp)
    if args.minutes:
        cutoff = result["frames"][0]["t"] + args.minutes * 60
        result["frames"] = [f for f in result["frames"] if f["t"] <= cutoff]
        if calibration:
            calibration["frames"] = calibration["frames"][: len(result["frames"])]
        result["analysedDuration"] = cutoff - result["frames"][0]["t"]
        events = [e for e in events if float(e["Start Time [s]"]) <= cutoff]
    # analytics.analyse() in two steps: possession is scored on every sequence,
    # not only the last 400 that the report keeps.
    base = analytics.prepare(result, calibration)
    analysis = analytics.finish(base)
    truth = ground_truth(events)
    # "pass" keeps the E8 definition (every pass event, complete or intercepted,
    # against Metrica's completed passes); passAttempt and passComplete compare like with like.
    plain = [t for t in truth if t["type"] in ("pass", "shot", "goal", "interception", "tackle")]
    events_report = evaluate_events(analysis["events"], plain, types=["pass", "shot", "interception", "tackle", "goal"])
    passes = [e for e in analysis["events"] if e["type"] == "pass"]
    events_report |= evaluate_events([{**e, "type": "passAttempt"} for e in passes], [t for t in truth if t["type"] == "passAttempt"], types=["passAttempt"])
    events_report |= evaluate_events([{**e, "type": "passComplete"} for e in passes if e.get("outcome") == "complete"],
                                     [t for t in truth if t["type"] == "passComplete"], types=["passComplete"])
    # Every won ball (interception or tackle) against every recovery.
    won = [{**e, "type": "turnover"} for e in analysis["events"] if e["type"] in ("interception", "tackle")]
    events_report |= evaluate_events(won, [t for t in truth if t["type"] == "turnover"], types=["turnover"])
    report = {
        "settings": vars(args) | {"data": str(args.data), "effectiveFps": result["sampleFps"]},
        "events": {k: {kk: vv for kk, vv in v.items() if kk not in ("missed", "false")} for k, v in events_report.items()},
        "possession": evaluate_possession(analysis, possession_truth(events), sequences=base["sequences"]),
        "coverage": analysis["stats"]["coverage"],
        "teams": [{k: t[k] for k in ("possession", "passAccuracy")} for t in analysis["stats"]["teams"]],
        "tracks": analysis["stats"]["tracks"],
    }
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
