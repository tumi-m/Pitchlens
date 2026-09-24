"""Conservative temporal measurements from observed detections, never filled-in scores."""

import math
from bisect import bisect_left, bisect_right


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
        if distance <= 0.55:
            candidates.append((distance, p))
    candidates.sort(key=lambda item: item[0])
    if not candidates or (len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 0.12):
        return None
    return candidates[0][1]


def derive_metrics(frames, sample_fps, duration):
    dt = 1 / sample_fps
    # Require consecutive observations, never bridge unseen-ball intervals.
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
        length = min(duration - r["start"], r["samples"] * dt)
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
        if not observed or any(f["ball"] is None for f in observed):
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
            first, last = observed[0]["ball"], observed[-1]["ball"]
            if math.hypot(last["x"] - first["x"], last["y"] - first["y"]) < height * 0.5:
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
    chains = possession_chains(stable, events, dt, duration)
    return {
        "sampledFrames": len(frames),
        "playerFrames": sum(bool(f["players"]) for f in frames),
        "ballFrames": sum(f["ball"] is not None for f in frames),
        "teamSeconds": [round(x, 2) for x in seconds],
        "unknownSeconds": round(max(0, duration - coverage), 2),
        "possessionShare": [round(x / coverage * 100, 1) if coverage else None for x in seconds],
        "possessionCoverage": round(coverage / duration * 100, 1) if duration else 0,
        "events": events,
        "trackCount": len({p["id"] for f in frames for p in f["players"]}),
        "possessions": chains,
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
