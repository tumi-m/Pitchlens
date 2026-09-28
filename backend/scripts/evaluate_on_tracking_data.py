"""Measure the event engine against human-labelled events on public tracking data.

    python scripts/evaluate_on_tracking_data.py METRICA_DATA_DIR [--game 1] [--fps 5]
        [--noise 0.3] [--ball-noise 0.5] [--drop 0.3] [--feet-drop 0.5] [--fragment 5]

Uses the Metrica Sports sample data (github.com/metrica-sports/sample-data;
please acknowledge Metrica Sports; the data is not redistributed here). The
25 fps optical tracking is converted into Pitchlens frames on a calibrated
105 x 68 m pitch and degraded to resemble one amateur camera: sampling rate,
position noise, the ball missing at random and more often at players' feet,
and track IDs broken into fragments. The real analytics (app/vision/analytics)
then run unchanged and are scored against Metrica's hand-labelled events
(passes, shots, recoveries) and event-derived possession.

This validates the event logic, not the vision pipeline: positions here are
far better than what a 360p camera produces before degradation.
"""

import argparse
import csv
import json
import math
import random
import sys
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


def build(data_dir, game, fps, noise, ball_noise, drop, feet_drop, fragment, seed=1):
    base = Path(data_dir) / f"Sample_Game_{game}"
    _, home = read_tracking(base / f"Sample_Game_{game}_RawTrackingData_Home_Team.csv")
    _, away = read_tracking(base / f"Sample_Game_{game}_RawTrackingData_Away_Team.csv")
    events = read_events(base / f"Sample_Game_{game}_RawEventsData.csv")
    rng = random.Random(seed)
    step = int(round(25 / fps))
    ids = {}
    next_id = [1]

    def fragment_id(name, t):
        """Break each real player into fragments (a new ID every ~`fragment` s on average)."""
        current = ids.get(name)
        if current is None or t >= current[1]:
            ids[name] = (next_id[0], t + rng.expovariate(1 / fragment) if fragment else math.inf)
            next_id[0] += 1
        return ids[name][0]

    frames = []
    for k in range(0, min(len(home), len(away)), step):
        period, _, t, hp = home[k]
        _, _, _, ap = away[k]
        players = []
        for team, positions in ((0, hp), (1, ap)):
            for name, (x, y) in positions.items():
                if name == "Ball":
                    continue
                x += rng.gauss(0, noise)
                y += rng.gauss(0, noise)
                px, py = x * SCALE, y * SCALE
                players.append({"id": fragment_id(f"{team}-{name}", t), "team": team if rng.random() > 0.1 else -1, "role": "player",
                                "box": [px - 4, py - 24, px + 4, py], "confidence": 0.9})
        ball = None
        bpos = hp.get("Ball")
        if bpos is not None:
            near = min((math.dist(bpos, p) for n, p in list(hp.items()) + list(ap.items()) if n != "Ball"), default=99)
            p_drop = drop + (feet_drop if near < 1.5 else 0)
            if rng.random() >= p_drop:
                bx, by = bpos[0] + rng.gauss(0, ball_noise), bpos[1] + rng.gauss(0, ball_noise)
                ball = {"x": bx * SCALE, "y": by * SCALE - 1, "box": [bx * SCALE - 1.5, by * SCALE - 3, bx * SCALE + 1.5, by * SCALE], "confidence": 0.6, "trackId": 1}
        frames.append({"t": round(t, 3), "scene": period - 1, "players": players, "ball": ball, "camera": [1, 0, 0, 0, 1, 0]})
    result = {"frames": frames, "sampleFps": fps, "analysedStart": frames[0]["t"], "analysedDuration": frames[-1]["t"] - frames[0]["t"],
              "video": {"width": int(L * SCALE), "height": int(W * SCALE)}}
    H = np.array([[1 / SCALE, 0, 0], [0, 1 / SCALE, 0], [0, 0, 1]])
    template = {"length": L, "width": W, "goalWidth": 7.32, "centreRadius": 9.15, "areaRadius": None, "areaDepth": 16.5, "areaWidth": 40.32, "penaltySpot": 11.0, "walls": False}
    calibration = {"state": "ready", "template": template, "k1": 0.0, "size": [int(L * SCALE), int(W * SCALE)], "rms": 0.3,
                   "frames": [{"H": H.reshape(-1).tolist(), "d": 0} for _ in frames]}
    return result, calibration, events


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
        elif kind == "SHOT":
            outcome = "goal" if "GOAL" in sub else "saved" if "SAVED" in sub else "blocked" if "BLOCKED" in sub else "off-target"
            out.append({"t": t, "type": "shot", "team": tm, "outcome": outcome})
            if "GOAL" in sub:
                out.append({"t": t, "type": "goal", "team": tm})
        elif kind == "RECOVERY" and "INTERCEPTION" in sub:
            out.append({"t": t, "type": "interception", "team": tm})
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("data", type=Path)
    parser.add_argument("--game", type=int, default=1)
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--noise", type=float, default=0.3)
    parser.add_argument("--ball-noise", type=float, default=0.5)
    parser.add_argument("--drop", type=float, default=0.3)
    parser.add_argument("--feet-drop", type=float, default=0.3)
    parser.add_argument("--fragment", type=float, default=5.0)
    parser.add_argument("--minutes", type=float, default=0, help="limit to the first N minutes (0 = all)")
    args = parser.parse_args()
    result, calibration, events = build(args.data, args.game, args.fps, args.noise, args.ball_noise, args.drop, args.feet_drop, args.fragment)
    if args.minutes:
        cutoff = result["frames"][0]["t"] + args.minutes * 60
        result["frames"] = [f for f in result["frames"] if f["t"] <= cutoff]
        calibration["frames"] = calibration["frames"][: len(result["frames"])]
        result["analysedDuration"] = cutoff - result["frames"][0]["t"]
        events = [e for e in events if float(e["Start Time [s]"]) <= cutoff]
    analysis = analytics.analyse(result, calibration)
    truth = ground_truth(events)
    report = {
        "settings": vars(args) | {"data": str(args.data)},
        "events": {k: {kk: vv for kk, vv in v.items() if kk not in ("missed", "false")} for k, v in evaluate_events(analysis["events"], truth, types=["pass", "shot", "interception", "goal"]).items()},
        "possession": evaluate_possession(analysis, possession_truth(events)),
        "coverage": analysis["stats"]["coverage"],
        "teams": [{k: t[k] for k in ("possession", "passAccuracy")} for t in analysis["stats"]["teams"]],
        "tracks": analysis["stats"]["tracks"],
    }
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
