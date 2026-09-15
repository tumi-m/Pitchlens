from __future__ import annotations

from collections import defaultdict
from typing import Optional

from app.engine.events import CandidateEvent
from app.engine.geometry import distance
from app.engine.track import FrameTracks


def possession_share(frames, team_of, owners):
    counts = {"home": 0, "away": 0, "unknown": 0, "no_ball": 0}
    for owner in owners:
        if owner in counts:
            counts[owner] += 1
        else:
            counts["unknown"] += 1
    known = counts["home"] + counts["away"]
    total = len(owners) or 1
    return {
        "home": round(100 * counts["home"] / known, 1) if known else None,
        "away": round(100 * counts["away"] / known, 1) if known else None,
        "knownFrames": known,
        "totalFrames": total,
        "ballVisibleRate": round(1 - counts["no_ball"] / total, 3),
        "unknownRate": round((total - known) / total, 3),
        "note": "Possession is time the nearest tracked player is within the radius while the ball is detected. Missing-ball time is excluded from the percentage.",
    }


def occupancy_heatmap(frames, team_of, pitch_pos_seq, length, width, bins=(21, 13)):
    grids = {"home": [[0.0] * bins[0] for _ in range(bins[1])], "away": [[0.0] * bins[0] for _ in range(bins[1])]}
    for positions in pitch_pos_seq:
        for tid, (x, y) in positions.items():
            team = team_of.get(tid)
            if team not in grids:
                continue
            xi = min(bins[0] - 1, max(0, int(x / max(length, 1e-6) * bins[0])))
            yi = min(bins[1] - 1, max(0, int(y / max(width, 1e-6) * bins[1])))
            grids[team][yi][xi] += 1
    out = {}
    for team, grid in grids.items():
        peak = max((v for row in grid for v in row), default=0) or 1
        cells = []
        for yi, row in enumerate(grid):
            for xi, val in enumerate(row):
                if val > 0:
                    cells.append({"x": (xi + 0.5) / bins[0] * length, "y": (yi + 0.5) / bins[1] * width, "intensity": round(val / peak, 3)})
        out[team] = cells
    return out


def track_summaries(frames, team_of, pitch_pos_seq, space):
    path = defaultdict(list)
    for frame, positions in zip(frames, pitch_pos_seq):
        for p in frame.players:
            pos = positions.get(p.track_id, p.foot)
            path[p.track_id].append((frame.timestamp, pos[0], pos[1]))
    summaries = []
    for tid, pts in path.items():
        if len(pts) < 2:
            continue
        dist = sum(distance(pts[i][1:], pts[i + 1][1:]) for i in range(len(pts) - 1))
        dt = max(1e-3, pts[-1][0] - pts[0][0])
        summaries.append({
            "trackId": tid, "team": team_of.get(tid, "unknown"), "samples": len(pts),
            "durationSec": round(dt, 2), "distance": round(dist, 2),
            "distanceUnit": "m" if space == "pitch" else "px",
            "meanX": round(sum(p[1] for p in pts) / len(pts), 2),
            "meanY": round(sum(p[2] for p in pts) / len(pts), 2),
        })
    summaries.sort(key=lambda s: -s["samples"])
    return summaries


def event_counts(events):
    out = {"home": defaultdict(int), "away": defaultdict(int), "unknown": defaultdict(int)}
    for e in events:
        out.get(e.team, out["unknown"])[e.type] += 1
    return {k: dict(v) for k, v in out.items()}
