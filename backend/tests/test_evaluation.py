import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from app.evaluation.metrics import evaluate, evaluate_ball, evaluate_events, evaluate_possession, review_metrics, wilson


def test_event_matching_is_one_to_one_and_reports_failures():
    predicted = [
        {"id": "ev-0", "type": "shot", "t": 10.2, "team": 0, "status": "proposed"},
        {"id": "ev-1", "type": "shot", "t": 10.9, "team": 0, "status": "proposed"},  # duplicate of the same shot
        {"id": "ev-2", "type": "shot", "t": 40.0, "team": 1, "status": "proposed"},
        {"id": "ev-3", "type": "shot", "t": 70.0, "team": 1, "status": "rejected"},
    ]
    labels = [{"t": 10.0, "type": "shot", "team": 0}, {"t": 41.0, "type": "shot", "team": 0}, {"t": 90.0, "type": "shot", "team": 1}]
    report = evaluate_events(predicted, labels)["shot"]
    assert report["matched"] == 2
    assert report["precision"]["value"] == round(2 / 3, 3)  # the duplicate cannot also count
    assert report["recall"]["value"] == round(2 / 3, 3)
    assert report["teamAccuracy"]["value"] == 0.5
    assert [m["t"] for m in report["missed"]] == [90.0]
    assert [f["t"] for f in report["false"]] == [10.9]
    assert report["precision"]["smallSample"]


def test_goal_candidates_are_scored_as_goals():
    report = evaluate_events([{"type": "goal-candidate", "t": 5.0, "team": 1, "status": "confirmed"}], [{"type": "goal", "t": 6.5, "team": 1}])
    assert report["goal"]["matched"] == 1


def test_ball_evaluation_separates_misses_wrong_places_and_false_observations():
    frames = [
        {"t": 0.0, "ball": {"x": 100, "y": 100}},
        {"t": 0.2, "ball": {"x": 120, "y": 100}},
        {"t": 0.4, "ball": None},
        {"t": 0.6, "ball": {"x": 50, "y": 50}},
        {"t": 0.8, "ball": {"x": 60, "y": 60, "inferred": True}},
    ]
    labels = [
        {"t": 0.0, "x": 101, "y": 100},
        {"t": 0.2, "x": 140, "y": 100},
        {"t": 0.4, "x": 150, "y": 100},
        {"t": 0.6, "x": 0, "y": 0, "visibility": "occluded"},
        {"t": 0.8, "x": 0, "y": 0, "visibility": "occluded"},
    ]
    report = evaluate_ball({"frames": frames, "sampleFps": 5}, labels, tolerance=3)
    assert report["recall"]["value"] == round(1 / 3, 3)
    assert report["wrongPlace"] == 1 and report["falseObservations"] == 1
    statuses = [r["status"] for r in report["rows"]]
    assert statuses == ["hit", "wrong-place", "missed", "false-observation", "correct-abstain"]


def test_possession_agreement_and_share_error():
    analysis = {"stats": {"possessionSequences": [{"team": 0, "start": 0, "end": 6}, {"team": 1, "start": 6, "end": 10}]}}
    labels = [{"start": 0, "end": 5, "team": 0}, {"start": 5, "end": 10, "team": 1}]
    report = evaluate_possession(analysis, labels)
    assert 0.85 <= report["agreement"]["value"] <= 0.95
    assert report["shareTruth"] == 50.0 and abs(report["sharePredicted"] - 60.0) < 2.5


def test_review_decisions_become_precision_and_recall():
    analysis = {"events": [
        {"type": "shot", "status": "confirmed"},
        {"type": "shot", "status": "rejected"},
        {"type": "shot", "status": "proposed"},
        {"type": "pass", "status": "confirmed"},
    ]}
    review = {"decisions": [{"action": "add", "type": "shot"}, {"action": "add", "type": "goal"}]}
    partial = review_metrics(analysis, review)
    assert partial["shot"]["precision"]["value"] == 0.5 and partial["shot"]["recall"] is None
    full = review_metrics(analysis, review, full_review=True)
    assert full["shot"]["recall"]["value"] == 0.5 and full["goal"]["addedByReviewer"] == 1


def test_wilson_interval_widens_on_small_samples():
    small, large = wilson(4, 5), wilson(80, 100)
    assert small[1] - small[0] > large[1] - large[0]
    assert wilson(0, 0) is None


def test_cli_recomputes_analysis_and_writes_markdown(tmp_path):
    frames = [{"t": i * 0.2, "scene": 0, "players": [], "ball": None} for i in range(10)]
    (tmp_path / "result.json").write_text(json.dumps({"frames": frames, "sampleFps": 5, "analysedDuration": 2, "video": {"width": 640, "height": 360}}))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"match": "fixture", "split": "regression", "events": [{"t": 1.0, "type": "shot", "team": 0}]}))
    out = subprocess.run(
        [sys.executable, "scripts/evaluate_match.py", str(tmp_path), str(labels), "--markdown", str(tmp_path / "r.md")],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True,
    )
    report = json.loads(out.stdout)
    assert report["events"]["shot"]["recall"]["value"] == 0.0
    assert "Missed at: 1.0s" in (tmp_path / "r.md").read_text()


def test_evaluate_combines_sections():
    analysis = {"events": [], "stats": {"possessionSequences": []}}
    report = evaluate({"frames": [], "sampleFps": 5}, analysis, {"match": "m", "possession": [{"start": 0, "end": 1, "team": 0}]})
    assert report["match"] == "m" and report["possession"]["coverage"]["value"] == 0.0
    assert np.isfinite(report["possession"]["shareTruth"])
