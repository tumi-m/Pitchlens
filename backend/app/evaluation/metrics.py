"""Evaluators for ball, events, possession and calibration against human labels.

Label file (JSON), every section optional:

    {
      "match": "tekkerz-court2-2026-09-12",      # identifier, used for grouping
      "split": "development" | "test" | "regression",
      "note": "who labelled, how",
      "ball": [{"t": 301.8, "x": 493, "y": 170, "visibility": "visible"}],
      "ballTolerancePixels": 3,
      "events": [{"t": 12.4, "type": "shot", "team": 0, "outcome": "saved"}],
      "eventTolerance": {"default": 1.0, "shot": 2.0, "goal": 2.0},
      "possession": [{"start": 10.0, "end": 14.5, "team": 0}],
      "landmarks": [{"t": 60.0, "x": 136, "y": 147, "pitch": [0, 0]}]
    }

Every metric is reported with its sample size; small samples are flagged.
One-to-one matching (Hungarian) prevents one detection counting twice.
"""

import math

import numpy as np
from scipy.optimize import linear_sum_assignment

from app.vision import pitch as pitchlib


def wilson(successes, n, z=1.96):
    """95% Wilson interval for a proportion (honest on small samples)."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def _rate(numerator, denominator):
    return {
        "value": round(numerator / denominator, 3) if denominator else None,
        "n": denominator,
        "interval95": wilson(numerator, denominator),
        "smallSample": denominator < 30,
    }


# --------------------------------------------------------------------- ball


def evaluate_ball(result, labels, tolerance=3.0):
    """Visible-ball recall/precision at a pixel tolerance; occluded labels test false observations."""
    frames = result["frames"]
    times = np.array([f["t"] for f in frames])
    rows = []
    for label in labels:
        i = int(np.argmin(np.abs(times - label["t"]))) if len(times) else -1
        if i < 0 or abs(times[i] - label["t"]) > 0.6 / max(1.0, result.get("sampleFps") or 5):
            rows.append({**label, "status": "no-frame"})
            continue
        ball = frames[i].get("ball")
        observed = ball is not None and not ball.get("inferred")
        visible = label.get("visibility", "visible") == "visible"
        if visible:
            if not observed:
                status = "missed"
            else:
                error = math.hypot(ball["x"] - label["x"], ball["y"] - label["y"])
                status = "hit" if error <= tolerance else "wrong-place"
                rows.append({**label, "status": status, "errorPixels": round(error, 2)})
                continue
        else:
            status = "false-observation" if observed else "correct-abstain"
        rows.append({**label, "status": status})
    visible = [r for r in rows if r.get("visibility", "visible") == "visible" and r["status"] != "no-frame"]
    hits = sum(r["status"] == "hit" for r in visible)
    emitted = sum(r["status"] in ("hit", "wrong-place") for r in visible) + sum(r["status"] == "false-observation" for r in rows)
    return {
        "recall": _rate(hits, len(visible)),
        "precision": _rate(hits, emitted),
        "wrongPlace": sum(r["status"] == "wrong-place" for r in visible),
        "falseObservations": sum(r["status"] == "false-observation" for r in rows),
        "unmatchedLabels": sum(r["status"] == "no-frame" for r in rows),
        "rows": rows,
    }


# --------------------------------------------------------------------- events


def evaluate_events(predicted, labels, tolerance=None, types=None):
    """Per event type: one-to-one matching within a time tolerance, then team accuracy.

    predicted: analysis events (rejected ones are ignored).
    Returns precision/recall/F1 per type plus the missed and false events so
    each failure can be opened as a clip.
    """
    tolerance = {"default": 1.0, "shot": 2.0, "goal": 2.0, "goal-candidate": 2.0, **(tolerance or {})}
    alias = {"goal-candidate": "goal"}
    predicted = [e for e in predicted if e.get("status") != "rejected"]
    kinds = sorted(set(types or []) | {alias.get(e["type"], e["type"]) for e in labels})
    report = {}
    for kind in kinds:
        pred = [e for e in predicted if alias.get(e["type"], e["type"]) == kind]
        truth = [e for e in labels if alias.get(e["type"], e["type"]) == kind]
        window = tolerance.get(kind, tolerance["default"])
        matches = []
        if pred and truth:
            cost = np.full((len(truth), len(pred)), 1e6)
            for i, a in enumerate(truth):
                for j, b in enumerate(pred):
                    gap = abs(a["t"] - b["t"])
                    if gap <= window:
                        cost[i, j] = gap
            rows, cols = linear_sum_assignment(cost)
            matches = [(i, j) for i, j in zip(rows, cols) if cost[i, j] < 1e6]
        matched_truth = {i for i, _ in matches}
        matched_pred = {j for _, j in matches}
        team_ok = sum(1 for i, j in matches if truth[i].get("team") is None or truth[i].get("team") == pred[j].get("team"))
        tp = len(matches)
        precision = _rate(tp, len(pred))
        recall = _rate(tp, len(truth))
        f1 = None
        if precision["value"] is not None and recall["value"] is not None and precision["value"] + recall["value"] > 0:
            f1 = round(2 * precision["value"] * recall["value"] / (precision["value"] + recall["value"]), 3)
        report[kind] = {
            "labelled": len(truth),
            "predicted": len(pred),
            "matched": tp,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "teamAccuracy": _rate(team_ok, tp),
            "timingErrorSeconds": round(float(np.mean([abs(truth[i]["t"] - pred[j]["t"]) for i, j in matches])), 2) if matches else None,
            "missed": [truth[i] for i in range(len(truth)) if i not in matched_truth],
            "false": [{"id": pred[j].get("id"), "t": pred[j]["t"], "team": pred[j].get("team"), "status": pred[j].get("status")} for j in range(len(pred)) if j not in matched_pred],
        }
    return report


# --------------------------------------------------------------------- possession


def evaluate_possession(analysis, labels, step=0.2):
    """Second-by-second team agreement and the possession-share error."""
    sequences = (analysis.get("stats") or {}).get("possessionSequences") or []
    if not labels:
        return None
    start = min(l["start"] for l in labels)
    end = max(l["end"] for l in labels)
    agree = disagree = unknown = 0
    truth_time = [0.0, 0.0]
    pred_time = [0.0, 0.0]
    for k in range(int(round((end - start) / step))):
        t = start + (k + 0.5) * step  # sample mid-step: no floating drift at boundaries
        truth = next((l["team"] for l in labels if l["start"] <= t < l["end"]), None)
        pred = next((s["team"] for s in sequences if s["start"] <= t < s["end"]), None)
        if truth is not None:
            truth_time[truth] += step
            if pred is None:
                unknown += 1
            elif pred == truth:
                agree += 1
            else:
                disagree += 1
        if pred is not None and truth is not None:
            pred_time[pred] += step
    decided = agree + disagree
    share_truth = truth_time[0] / sum(truth_time) * 100 if sum(truth_time) else None
    share_pred = pred_time[0] / sum(pred_time) * 100 if sum(pred_time) else None
    return {
        "agreement": _rate(agree, decided),
        "coverage": _rate(decided, decided + unknown),
        "shareTruth": round(share_truth, 1) if share_truth is not None else None,
        "sharePredicted": round(share_pred, 1) if share_pred is not None else None,
        "shareErrorPoints": round(abs(share_truth - share_pred), 1) if share_truth is not None and share_pred is not None else None,
    }


# --------------------------------------------------------------------- calibration


def evaluate_calibration(result, calibration, labels):
    """Held-out landmark error in metres through each frame's calibration."""
    if not calibration or calibration.get("state") != "ready" or not labels:
        return None
    frames = result["frames"]
    times = np.array([f["t"] for f in frames])
    errors, missing = [], 0
    for label in labels:
        i = int(np.argmin(np.abs(times - label["t"])))
        entry = (calibration.get("frames") or [None] * len(frames))[i]
        if not entry:
            missing += 1
            continue
        H = np.asarray(entry["H"], float).reshape(3, 3)
        point = pitchlib.image_to_pitch(calibration, [[label["x"], label["y"]]], H)[0]
        errors.append(float(np.linalg.norm(point - np.asarray(label["pitch"], float))))
    if not errors:
        return {"n": 0, "uncalibratedLabels": missing}
    return {
        "n": len(errors),
        "medianMetres": round(float(np.median(errors)), 2),
        "p95Metres": round(float(np.percentile(errors, 95)), 2),
        "uncalibratedLabels": missing,
        "smallSample": len(errors) < 30,
    }


# --------------------------------------------------------------------- from review


def review_metrics(analysis, review, full_review=False):
    """What a reviewer's decisions say about the automatic events.

    Precision per type = confirmed / (confirmed + rejected) among reviewed
    automatic events. Recall needs a reviewer who watched the whole match and
    added everything missed (`full_review`), otherwise it is not reported.
    """
    decisions = (review or {}).get("decisions", [])
    added = [d for d in decisions if d.get("action") == "add"]
    events = [e for e in analysis.get("events", []) if e.get("source") != "reviewer"]
    out = {}
    for kind in sorted({e["type"] for e in events} | {d.get("type") for d in added if d.get("type")}):
        auto = [e for e in events if e["type"] == kind]
        confirmed = sum(1 for e in auto if e["status"] == "confirmed")
        rejected = sum(1 for e in auto if e["status"] == "rejected")
        missed = sum(1 for d in added if d.get("type") == kind)
        out[kind] = {
            "automatic": len(auto),
            "reviewed": confirmed + rejected,
            "precision": _rate(confirmed, confirmed + rejected),
            "recall": _rate(confirmed, confirmed + missed) if full_review else None,
            "addedByReviewer": missed,
        }
    return out


def evaluate(result, analysis, labels, calibration=None, review=None):
    """Everything that the labels allow, in one report."""
    report = {"match": labels.get("match"), "split": labels.get("split"), "note": labels.get("note")}
    if labels.get("ball"):
        report["ball"] = evaluate_ball(result, labels["ball"], labels.get("ballTolerancePixels", 3.0))
    if labels.get("events"):
        report["events"] = evaluate_events(analysis.get("events", []), labels["events"], labels.get("eventTolerance"))
    if labels.get("possession"):
        report["possession"] = evaluate_possession(analysis, labels["possession"])
    if labels.get("landmarks"):
        report["calibration"] = evaluate_calibration(result, calibration, labels["landmarks"])
    if review:
        report["review"] = review_metrics(analysis, review, labels.get("fullReview", False))
    return report
