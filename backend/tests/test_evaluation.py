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


def test_possession_can_be_scored_on_every_sequence_not_only_the_reports_last_400():
    # The report keeps the last 400 possessions; a long match has more.
    sequences = [{"team": i % 2, "start": float(i), "end": i + 1.0} for i in range(500)]
    analysis = {"stats": {"possessionSequences": sequences[-400:]}}
    # The first 100 s are labelled for the other team: wrong there, right after.
    labels = [{"start": float(i), "end": i + 1.0, "team": i % 2 if i >= 100 else 1 - i % 2} for i in range(500)]
    tail = evaluate_possession(analysis, labels)
    assert tail["coverage"]["value"] == 0.8 and tail["agreement"]["value"] == 1.0
    full = evaluate_possession(analysis, labels, sequences=sequences)
    assert full["coverage"]["value"] == 1.0 and full["agreement"]["value"] == 0.8
    report = evaluate({"frames": [], "sampleFps": 5}, analysis, {"possession": labels}, sequences=sequences)
    assert report["possession"] == full


def _harness():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_on_tracking_data.py"
    spec = importlib.util.spec_from_file_location("evaluate_on_tracking_data", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


def test_the_tracking_harness_samples_at_the_rate_it_reports_like_the_engine():
    import math

    effective_rate = _harness().effective_rate
    expected = {5: (5, 5.0), 6: (5, 5.0), 10: (3, 25 / 3), 25 / 3: (3, 25 / 3), 8.333: (3, 25 / 3), 12.5: (2, 12.5),
                4: (7, 25 / 7), 3: (9, 25 / 9), 25: (1, 25.0), 30: (1, 25.0)}
    for requested, (stride, fps) in expected.items():
        assert effective_rate(requested)[0] == stride and math.isclose(effective_rate(requested)[1], fps), requested
    # The engine's stride on a 25 fps video (engine.run_video).
    for requested in (3, 4, 5, 6, 10, 12.5, 15, 25):
        assert effective_rate(requested)[0] == max(1, math.ceil(25 / requested))


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


def test_identity_mode_scores_each_stitched_junction_by_the_true_player():
    harness = _harness()

    def frame(t, players):
        return {"t": t, "scene": 0, "players": [{"id": i, "gt": g} for i, g in players]}

    # Fragments 1 and 2 are player A; fragment 3 is player B.
    frames = [frame(0.0, [(1, "A"), (3, "B")]), frame(0.2, [(1, "A"), (3, "B")]), frame(0.4, [(2, "A")]), frame(0.6, [(2, "A")])]
    assert harness.junction_links(frames, {1: 1, 2: 1, 3: 2}) == (1, 0)
    assert harness.junction_links(frames, {1: 1, 3: 1, 2: 2}) == (0, 1)
    assert harness.junction_links(frames, {1: 1, 2: 2, 3: 3}) == (0, 0)
    metrics = harness.identity_metrics(frames, {1: 1, 3: 1, 2: 2})
    assert metrics["wrongLinks"] == 1 and metrics["linkPrecision"] == 0.0
    assert metrics["purity"] == round(4 / 6, 4)
