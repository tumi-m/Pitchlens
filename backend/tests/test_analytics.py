import math

import pytest
import numpy as np

from app.vision import analytics, pitch

TEMPLATE = pitch.normalise_template("futsal")  # 40 x 20 m, 3 m goals
SCALE, OX, OY = 15.0, 20.0, 20.0
FPS = 5.0
TO_IMAGE = np.array([[SCALE, 0, OX], [0, SCALE, OY], [0, 0, 1]])


def img(xy):
    return (xy[0] * SCALE + OX, xy[1] * SCALE + OY)


def player(pid, team, xy):
    x, y = img(xy)
    return {"id": pid, "team": team, "role": "player", "box": [x - 5, y - 30, x + 5, y], "confidence": 0.9}


def ball(xy, inferred=False):
    x, y = img(xy)
    return {"x": x, "y": y - 2, "box": [x - 2, y - 4, x + 2, y], "confidence": 0.8, "trackId": 1, "inferred": inferred}


def build_match():
    """Team 0 (ids 1-3) keeps the left half and attacks right; team 1 (ids 11-13) defends right."""
    frames = []
    t = 0.0

    def frame(ball_xy, a_xy=(10, 10), b_xy=(18, 12), extra=None):
        nonlocal t
        players = [
            player(1, 0, a_xy),
            player(2, 0, b_xy),
            player(3, 0, (6, 5)),
            player(11, 1, (27, 10.5)),
            player(12, 1, (30, 4)),
            player(13, 1, (34, 16)),
        ] + (extra or [])
        frames.append({"t": round(t, 2), "scene": 0, "players": players, "ball": ball(ball_xy) if ball_xy else None, "camera": [1, 0, 0, 0, 1, 0]})
        t += 1 / FPS

    for k in range(10):  # A dribbles 10 -> 12 m
        a = (10 + 0.2 * k, 10)
        frame((a[0] + 0.3, a[1]), a_xy=a)
    for k in range(1, 4):  # pass travels to B
        frame((12 + 2 * k, 10 + 0.67 * k), a_xy=(12, 10))
    for k in range(6):  # B controls
        frame((18.3, 12), a_xy=(12, 10))
    for k in range(1, 9):  # B shoots: 3 m per frame (15 m/s) towards the goal mouth
        frame((18.3 + 3 * k, 12 - 0.2 * k), a_xy=(12, 10))
    for k in range(5):
        frame(None, a_xy=(12, 10))
    return frames


def calibration_for(frames, rms=0.2):
    H = np.linalg.inv(TO_IMAGE)
    return {
        "template": TEMPLATE,
        "k1": 0.0,
        "size": [640, 360],
        "rms": rms,
        "frames": [{"H": H.reshape(-1).tolist(), "d": 0} for _ in frames],
    }


def result_for(frames):
    return {"frames": frames, "sampleFps": FPS, "analysedStart": 0, "analysedDuration": frames[-1]["t"] + 0.2, "video": {"width": 640, "height": 360}}


def test_pass_and_shot_on_goal_are_found_in_pitch_space():
    frames = build_match()
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert out["calibrated"]
    assert out["directions"]["segments"][0]["team0Attacks"] == "right"
    passes = [e for e in out["events"] if e["type"] == "pass"]
    shots = [e for e in out["events"] if e["type"] == "shot"]
    assert len(passes) == 1 and passes[0]["team"] == 0 and passes[0]["outcome"] == "complete"
    assert 5 < passes[0]["length"] < 8
    assert len(shots) == 1 and shots[0]["team"] == 0 and shots[0]["onTarget"]
    assert shots[0]["speed"] >= 10
    assert any(e["type"] == "goal-candidate" for e in out["events"])
    team0 = out["stats"]["teams"][0]
    team1 = out["stats"]["teams"][1]
    # Possession runs from control through the pass flight; the long unseen
    # spell after the shot keeps coverage below the bar for showing a share.
    assert team0["possessionSeconds"] > 2 and team1["possessionSeconds"] == 0
    assert out["stats"]["coverage"]["possessionShown"] is (out["stats"]["coverage"]["possessionPercent"] >= 60)
    assert team0["shots"]["value"] == 1 and team0["shots"]["pending"] == 1
    # A goal is never counted from geometry alone.
    assert team0["goals"]["value"] == 0 and team0["goals"]["candidates"] == 1


def test_a_fast_ball_passing_a_defender_is_not_his_control():
    frames = build_match()
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert out["stats"]["teams"][1]["controlSeconds"] == 0


def test_review_decisions_confirm_reject_and_add():
    frames = build_match()
    first = analytics.analyse(result_for(frames), calibration_for(frames))
    shot = next(e for e in first["events"] if e["type"] == "shot")
    goal = next(e for e in first["events"] if e["type"] == "goal-candidate")
    review = {
        "decisions": [
            {"action": "accept", "eventId": shot["id"]},
            {"action": "accept", "eventId": goal["id"]},
            {"action": "add", "type": "shot", "t": 1.0, "team": 1, "outcome": "off-target", "id": "added-1"},
        ]
    }
    out = analytics.analyse(result_for(frames), calibration_for(frames), review)
    assert out["stats"]["teams"][0]["shots"]["confirmed"] == 1
    assert out["stats"]["teams"][0]["goals"]["value"] == 1
    assert out["stats"]["teams"][1]["shots"]["value"] == 1
    rejected = analytics.analyse(result_for(frames), calibration_for(frames), {"decisions": [{"action": "reject", "eventId": shot["id"]}]})
    assert rejected["stats"]["teams"][0]["shots"]["value"] == 0


def test_reviewer_can_set_attacking_direction():
    frames = build_match()
    out = analytics.analyse(result_for(frames), calibration_for(frames), {"decisions": [{"action": "direction", "value": "left"}]})
    assert out["directions"]["source"] == "reviewer"
    assert out["directions"]["segments"][0]["team0Attacks"] == "left"
    # Shooting towards its own goal is not a shot under the corrected direction.
    assert not any(e["type"] == "shot" and e["team"] == 0 for e in out["events"])


def test_uncalibrated_results_report_unavailable_not_zero():
    frames = build_match()
    out = analytics.analyse(result_for(frames), None)
    assert not out["calibrated"]
    team0 = out["stats"]["teams"][0]
    assert team0["shots"] is None
    # Goals a reviewer confirms still count without a pitch setup; candidates need one.
    assert team0["goals"] == {"value": 0, "candidates": None}
    assert out["stats"]["heatmaps"] is None
    assert team0["possessionSeconds"] > 0 and team0["passes"]["value"] >= 1


def test_interception_counts_for_both_sides():
    frames = []
    t = 0.0
    for k in range(20):
        if k < 6:
            b = (10.3, 10)
        elif k < 9:
            b = (10.3 + 2.5 * (k - 5), 10)
        else:
            b = (18.3, 10)
        players = [player(1, 0, (10, 10)), player(2, 0, (5, 5)), player(3, 0, (8, 15)), player(11, 1, (18, 10)), player(12, 1, (30, 5)), player(13, 1, (32, 15))]
        frames.append({"t": round(t, 2), "scene": 0, "players": players, "ball": ball(b), "camera": [1, 0, 0, 0, 1, 0]})
        t += 0.2
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    kinds = [(e["type"], e["team"]) for e in out["events"]]
    assert ("interception", 1) in kinds
    assert ("pass", 0) in kinds
    p = next(e for e in out["events"] if e["type"] == "pass")
    assert p["outcome"] == "intercepted"
    # One attempt is far too few for an accuracy percentage: withheld.
    assert out["stats"]["teams"][0]["passAccuracy"] is None
    assert out["stats"]["teams"][0]["passes"]["value"] == 1


def test_track_fragments_are_stitched_across_short_gaps():
    frames = []
    for k in range(30):
        pid = 1 if k < 10 else 2 if k < 20 else 3  # same runner, three fragment ids
        players = [] if k in (10, 20) else [player(pid, 0, (5 + 0.8 * k, 10))]
        frames.append({"t": round(k * 0.2, 2), "scene": 0, "players": players, "ball": None, "camera": [1, 0, 0, 0, 1, 0]})
    projected, _ = analytics.project(frames, calibration_for(frames))
    mapping = analytics.stitch_tracks(projected, FPS)
    assert len(set(mapping.values())) == 1


def test_teleporting_fragments_are_not_stitched():
    frames = []
    for k in range(20):
        pid = 1 if k < 10 else 2
        x = 5 if k < 10 else 35
        frames.append({"t": round(k * 0.2, 2), "scene": 0, "players": [player(pid, 0, (x, 10))], "ball": None, "camera": [1, 0, 0, 0, 1, 0]})
    projected, _ = analytics.project(frames, calibration_for(frames))
    mapping = analytics.stitch_tracks(projected, FPS)
    assert len(set(mapping.values())) == 2


# ------------------------------------------------------------ worker endpoints


def make_job(server, frames):
    import json
    import uuid

    job_id = uuid.uuid4().hex
    directory = server.ROOT / job_id
    directory.mkdir()
    (directory / "status.json").write_text(json.dumps({"id": job_id, "status": "completed", "stage": "done", "progress": 100, "createdAt": 0, "title": "t"}))
    (directory / "result.json").write_text(json.dumps(result_for(frames)))
    return job_id, directory


def clicks():
    names = ["corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left", "centre-spot", "halfway-far"]
    marks = pitch.landmarks(TEMPLATE)
    return [{"name": n, "x": img(marks[n])[0], "y": img(marks[n])[1]} for n in names]


def test_calibration_review_and_analysis_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    # Run the background calibration inline so the test is deterministic.
    monkeypatch.setattr(server.post_pool, "submit", lambda fn, *a: fn(*a))
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    job_id, directory = make_job(server, build_match())

    before = client.get(f"/jobs/{job_id}/analysis").json()
    assert before["calibrated"] is False and before["stats"]["teams"][0]["shots"] is None
    assert client.get(f"/jobs/{job_id}/calibration").json()["state"] == "none"

    body = {"template": "futsal", "points": clicks(), "t": 0.0}
    preview = client.post(f"/jobs/{job_id}/calibration/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["fit"]["quality"] == "good" and preview.json()["lines"]

    bad = {"template": "futsal", "points": clicks()[:3], "t": 0.0}
    assert client.post(f"/jobs/{job_id}/calibration", json=bad).status_code == 400
    swapped = clicks()
    swapped[0]["name"], swapped[2]["name"] = swapped[2]["name"], swapped[0]["name"]
    assert client.post(f"/jobs/{job_id}/calibration", json={"template": "futsal", "points": swapped, "t": 0}).status_code == 400

    saved = client.post(f"/jobs/{job_id}/calibration", json=body)
    assert saved.status_code == 200, saved.text
    cal = client.get(f"/jobs/{job_id}/calibration").json()
    assert cal["state"] == "ready" and cal["static"] and cal["coverage"] == 100.0
    assert cal["job"]["state"] == "done"

    after = client.get(f"/jobs/{job_id}/analysis").json()
    assert after["calibrated"] and after["stats"]["teams"][0]["shots"]["value"] == 1
    shot = next(e for e in after["events"] if e["type"] == "shot")

    reviewed = client.post(f"/jobs/{job_id}/review", json={"decisions": [
        {"action": "accept", "eventId": shot["id"]},
        {"action": "add", "type": "goal", "t": 4.4, "team": 0},
    ]})
    assert reviewed.status_code == 200, reviewed.text
    stats = reviewed.json()["analysis"]["stats"]["teams"][0]
    assert stats["shots"]["confirmed"] == 1 and stats["goals"]["value"] == 1
    assert len(client.get(f"/jobs/{job_id}/review").json()["decisions"]) == 2
    # Invalid decisions are refused without touching the log.
    assert client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "delete-everything"}]}).status_code == 400
    assert client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "team", "eventId": shot["id"], "value": 5}]}).status_code == 400
    assert len(client.get(f"/jobs/{job_id}/review").json()["decisions"]) == 2
    # Unfinished jobs have no analysis.
    (directory / "result.json").unlink()
    assert client.get(f"/jobs/{job_id}/analysis").status_code == 409


def test_reviewer_shots_count_on_target_by_outcome():
    frames = build_match()
    review = {"decisions": [
        {"action": "add", "type": "shot", "t": 1.0, "team": 1, "outcome": "saved", "id": "added-a"},
        {"action": "add", "type": "shot", "t": 2.0, "team": 1, "outcome": "off-target", "id": "added-b"},
    ]}
    out = analytics.analyse(result_for(frames), calibration_for(frames), review)
    team1 = out["stats"]["teams"][1]
    assert team1["shots"]["value"] == 2 and team1["shotsOnTarget"]["value"] == 1



def test_pass_accuracy_appears_with_enough_attempts():
    spots = {1: (10, 10), 2: (18, 10), 11: (14, 16)}
    holders = [1, 2, 1, 2, 11] * 6 + [1]  # team 0: 3 complete + 1 intercepted per cycle
    people = [player(1, 0, spots[1]), player(2, 0, spots[2]), player(3, 0, (6, 4)), player(11, 1, spots[11]), player(12, 1, (30, 4)), player(13, 1, (32, 16))]
    frames = []
    t = 0.0
    for a, b in zip(holders[:-1], holders[1:]):
        for _ in range(5):
            frames.append({"t": round(t, 2), "scene": 0, "players": people, "ball": ball((spots[a][0] + 0.3, spots[a][1])), "camera": [1, 0, 0, 0, 1, 0]})
            t += 0.2
        for j in (1, 2):
            x = spots[a][0] + (spots[b][0] - spots[a][0]) * j / 3
            y = spots[a][1] + (spots[b][1] - spots[a][1]) * j / 3
            frames.append({"t": round(t, 2), "scene": 0, "players": people, "ball": ball((x, y)), "camera": [1, 0, 0, 0, 1, 0]})
            t += 0.2
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    team0 = out["stats"]["teams"][0]
    assert team0["passes"]["value"] == 24
    assert team0["passAccuracy"] is None  # confidence alone cannot validate an event
    review = {"decisions": [{"action": "accept", "eventId": e["id"]} for e in out["events"] if e["type"] == "pass"]}
    reviewed = analytics.analyse(result_for(frames), calibration_for(frames), review)
    assert reviewed["stats"]["teams"][0]["passAccuracy"] == 75.0
    assert out["stats"]["coverage"]["possessionShown"] and team0["possession"] > 50


def test_play_stopped_is_excluded_and_a_centre_restart_suggests_a_goal():
    frames = []
    t = 0.0

    def add(players, ball_xy):
        nonlocal t
        frames.append({"t": round(t, 2), "scene": 0, "players": players, "ball": ball(ball_xy) if ball_xy else None, "camera": [1, 0, 0, 0, 1, 0]})
        t += 0.2

    moving = lambda k: [player(1, 0, (10 + 0.3 * (k % 10), 8)), player(2, 0, (14, 12 + 0.3 * (k % 7))), player(3, 0, (6, 5 + 0.3 * (k % 5))),
                        player(11, 1, (26 - 0.3 * (k % 10), 9)), player(12, 1, (30, 6 + 0.3 * (k % 6))), player(13, 1, (33, 14 - 0.3 * (k % 8)))]
    for k in range(60):  # open play, team 0 on the ball
        add(moving(k), (10.3 + 0.3 * (k % 10), 8))
    # Team 0 shoots from 12 m: 4 m per frame towards the right goal, over the line.
    for k in range(1, 7):
        add(moving(60 + k), (16 + 4 * k, 10))
    # Play stops for 20 s; everyone walks back to their own half; team 1 on the centre spot.
    still = [player(1, 0, (12, 8)), player(2, 0, (15, 13)), player(3, 0, (8, 6)), player(11, 1, (20.5, 10)), player(12, 1, (26, 6)), player(13, 1, (28, 14))]
    for k in range(100):
        add(still, (20, 10) if k > 80 else None)
    for k in range(20):
        add(moving(200 + k), (20 + 0.5 * k, 10))
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert out["stats"]["coverage"]["deadBallSeconds"] >= 15
    assert out["kickoffs"] and out["kickoffs"][0]["team"] == 1
    goals = [e for e in out["events"] if e["type"] == "goal-candidate"]
    assert goals and goals[0]["team"] == 0 and "centre-restart" in goals[0]["evidence"]
    assert out["stats"]["teams"][0]["goals"]["value"] == 0  # still needs a reviewer



def test_entered_score_is_reported_and_validated(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    job_id, _ = make_job(server, build_match())
    assert client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "score", "value": [3, -1]}]}).status_code == 400
    assert client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "score", "value": [3, True]}]}).status_code == 400
    ok = client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "score", "value": [3, 2]}]})
    assert ok.status_code == 200 and ok.json()["analysis"]["enteredScore"] == [3, 2]


def test_stitching_scales_to_heavily_fragmented_matches():
    import time
    import tracemalloc

    rng = np.random.default_rng(0)
    frames = []
    for k in range(3000):  # 10 minutes at 5 fps, 10 players, new fragment id every ~1 s
        players = [player(1000 * j + k // 5, j % 2, (5 + j * 3 + rng.normal(0, 0.2), 10 + rng.normal(0, 0.2))) for j in range(10)]
        frames.append({"t": round(k * 0.2, 2), "scene": 0, "players": players, "ball": None, "camera": [1, 0, 0, 0, 1, 0]})
    projected, _ = analytics.project(frames, calibration_for(frames))
    tracemalloc.start()
    started = time.monotonic()
    mapping = analytics.stitch_tracks(projected, FPS)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(mapping) == 6000 and len(set(mapping.values())) <= 60
    assert peak < 200 * 1024 * 1024 and time.monotonic() - started < 60


def test_review_decisions_survive_re_analysis_and_never_retarget_another_event():
    from app.vision.analytics import apply_review

    before = [
        {"id": "ev-0", "type": "pass", "t": 10.0, "team": 0, "status": "proposed"},
        {"id": "ev-1", "type": "shot", "t": 20.0, "team": 0, "status": "proposed", "outcome": "unresolved"},
    ]
    review = {"decisions": [
        {"action": "accept", "eventId": "ev-1", "event": {"type": "shot", "t": 20.0, "team": 0}},
        {"action": "reject", "eventId": "ev-0", "event": {"type": "pass", "t": 10.0, "team": 0}},
    ]}
    # Re-analysis inserted a new event first: every id shifted by one.
    after = [
        {"id": "ev-0", "type": "tackle", "t": 5.0, "team": 1, "status": "proposed"},
        {"id": "ev-1", "type": "pass", "t": 10.2, "team": 0, "status": "proposed"},
        {"id": "ev-2", "type": "shot", "t": 20.3, "team": 0, "status": "proposed"},
    ]
    merged, _ = apply_review(after, review)
    status = {e["type"]: e["status"] for e in merged}
    assert status == {"tackle": "proposed", "pass": "rejected", "shot": "confirmed"}
    # The confirmed shot vanished entirely after re-analysis: it is kept, not moved.
    merged, _ = apply_review([{"id": "ev-0", "type": "pass", "t": 20.0, "team": 0, "status": "proposed"}], review)
    kept = [e for e in merged if e["type"] == "shot"]
    assert len(kept) == 1 and kept[0]["status"] == "confirmed" and kept[0]["source"] == "reviewer"
    assert [e for e in merged if e["type"] == "pass"][0]["status"] == "proposed"


def test_a_shot_confirmed_as_goal_counts_once():
    from app.vision.analytics import confirmed_goals

    events = [
        {"type": "shot", "t": 30.0, "team": 0, "status": "confirmed", "outcome": "goal-candidate"},
        {"type": "goal-candidate", "t": 30.0, "team": 0, "status": "confirmed"},
        {"type": "goal", "t": 95.0, "team": 0, "status": "confirmed"},
        {"type": "shot", "t": 120.0, "team": 0, "status": "proposed", "outcome": "goal-candidate"},
    ]
    assert confirmed_goals(events, 0) == 2


def _seen(e):
    """The decision fingerprint the review queue sends (ReviewQueue's seen())."""
    return {k: e[k] for k in ("type", "t", "team", "outcome") if e.get(k) is not None}


def test_a_rejected_possible_goal_does_not_come_back_through_its_shot():
    frames = build_match()
    first = analytics.analyse(result_for(frames), calibration_for(frames))
    shot = next(e for e in first["events"] if e["type"] == "shot")
    candidate = next(e for e in first["events"] if e["type"] == "goal-candidate")
    assert shot["outcome"] == "goal-candidate" and abs(shot["t"] - candidate["t"]) < 0.01

    def goals(*decisions):
        out = analytics.analyse(result_for(frames), calibration_for(frames), {"decisions": list(decisions)})
        return out["stats"]["teams"][0]["goals"]["value"]

    reject = {"action": "reject", "eventId": candidate["id"], "event": _seen(candidate)}
    accept_candidate = {"action": "accept", "eventId": candidate["id"], "event": _seen(candidate)}
    accept_shot = {"action": "accept", "eventId": shot["id"], "event": _seen(shot)}
    # Confirming "Shot · goal candidate" only says a shot happened.
    assert goals(reject, accept_shot) == 0
    assert goals(accept_candidate, accept_shot) == 1
    # Choosing Goal as the shot's outcome is the reviewer's own call.
    assert goals(reject, {"action": "outcome", "eventId": shot["id"], "event": _seen(shot), "value": "goal"}) == 1
    assert goals({"action": "add", "type": "shot", "t": 1.0, "team": 0, "outcome": "goal", "id": "added-g"}) == 1


def test_a_rejected_possible_goal_stays_rejected_through_the_worker(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    job_id, _ = make_job(server, build_match())
    assert client.post(f"/jobs/{job_id}/calibration", json={"template": "futsal", "points": clicks(), "t": 0.0}).status_code == 200
    events = client.get(f"/jobs/{job_id}/analysis").json()["events"]
    candidate = next(e for e in events if e["type"] == "goal-candidate")
    shot = next(e for e in events if e["type"] == "shot")
    review = lambda d: client.post(f"/jobs/{job_id}/review", json={"decisions": [d]}).json()["analysis"]
    review({"action": "reject", "eventId": candidate["id"], "event": _seen(candidate)})
    out = review({"action": "accept", "eventId": shot["id"], "event": _seen(shot)})
    assert [t["goals"]["value"] for t in out["stats"]["teams"]] == [0, 0]
    out = review({"action": "outcome", "eventId": shot["id"], "event": _seen(shot), "value": "goal"})
    assert [t["goals"]["value"] for t in out["stats"]["teams"]] == [1, 0]
    assert next(e for e in out["events"] if e["id"] == shot["id"])["outcomeBy"] == "reviewer"


def test_a_player_load_job_reports_shots_as_not_looked_for_not_zero():
    frames = build_match()
    for f in frames:
        f["ball"] = None  # ball_search "none": no ball in any frame
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert out["calibrated"] and out["directions"]["segments"]
    team0 = out["stats"]["teams"][0]
    assert team0["shots"] is None and team0["shotsOnTarget"] is None
    assert out["stats"]["coverage"]["shotsBasis"] == "no-ball"
    # Centre restarts are found without the ball: possible goals stay measured.
    assert isinstance(team0["goals"]["candidates"], int)
    # Uncalibrated: still "no-ball" (setting up the pitch would not help).
    assert analytics.analyse(result_for(frames), None)["stats"]["coverage"]["shotsBasis"] == "no-ball"


def test_reviewer_shots_count_without_a_pitch_setup():
    frames = build_match()
    review = {"decisions": [
        {"action": "add", "type": "shot", "t": 1.0, "team": 0, "outcome": "on-target", "id": "added-s1"},
        {"action": "add", "type": "shot", "t": 6.0, "team": 0, "outcome": "off-target", "id": "added-s2"},
    ]}
    out = analytics.analyse(result_for(frames), None, review)
    team0 = out["stats"]["teams"][0]
    assert team0["shots"]["value"] == 2 and team0["shotsOnTarget"]["value"] == 1
    assert team0["goals"]["candidates"] is None
    assert out["stats"]["coverage"]["shotsBasis"] is None
    assert analytics.analyse(result_for(frames), None)["stats"]["coverage"]["shotsBasis"] == "no-pitch"


def test_a_confirmed_goal_is_also_a_shot_on_target_counted_once():
    frames = build_match()
    # A goal the reviewer added, with no shot logged: one shot, on target, once
    # shots are counted at all (here a reviewer logged one for the other side).
    goal = {"action": "add", "type": "goal", "t": 2.0, "team": 1, "id": "added-g"}
    other = {"action": "add", "type": "shot", "t": 6.0, "team": 0, "outcome": "off-target", "id": "added-s"}
    out = analytics.analyse(result_for(frames), None, {"decisions": [goal, other]})
    team1 = out["stats"]["teams"][1]
    assert team1["goals"]["value"] == 1
    assert team1["shots"]["confirmed"] == 1 and team1["shotsOnTarget"]["confirmed"] == 1
    # The goal alone does not make the shot counts known (the other side's 0 would be invented).
    alone = analytics.analyse(result_for(frames), None, {"decisions": [goal]})
    assert [t["shots"] for t in alone["stats"]["teams"]] == [None, None]
    assert alone["stats"]["teams"][1]["goals"]["value"] == 1
    # A confirmed possible goal whose automatic shot (same moment) is still pending: one shot, not two.
    first = analytics.analyse(result_for(frames), calibration_for(frames))
    candidate = next(e for e in first["events"] if e["type"] == "goal-candidate")
    out = analytics.analyse(result_for(frames), calibration_for(frames), {"decisions": [{"action": "accept", "eventId": candidate["id"], "event": _seen(candidate)}]})
    team0 = out["stats"]["teams"][0]
    assert team0["goals"]["value"] == 1
    assert team0["shots"]["value"] == 1 and team0["shots"]["confirmed"] == 1
    assert team0["shotsOnTarget"]["value"] == 1
    assert len(out["stats"]["shotMap"]) == 1
    # The events the reviewer works through are unchanged: the shot is still to review.
    assert next(e for e in out["events"] if e["type"] == "shot")["status"] == "proposed"


def test_add_decision_with_only_x_is_stored_without_coordinates(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    job_id, _ = make_job(server, build_match())
    ok = client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "add", "type": "shot", "t": 1.0, "team": 0, "x": 5}]})
    assert ok.status_code == 200
    assert client.get(f"/jobs/{job_id}/analysis").status_code == 200


def test_airborne_ball_projected_far_behind_the_goal_is_not_a_goal_candidate():
    frames = build_match()
    # Replace the shot's flight with a lofted ball that projects 8 m behind the line.
    for f in frames:
        if f["t"] >= 5.0 and f["ball"] is not None:
            f["ball"] = ball((48.0, 11.0))
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert not any(e["type"] == "goal-candidate" for e in out["events"])


def test_goalkeeper_collecting_a_shot_is_a_save():
    frames = build_match()
    keeper = {"id": 99, "team": -1, "role": "goalkeeper", "box": None}
    for f in frames:
        x, y = img((39.2, 11.2))
        f["players"].append({**keeper, "box": [x - 5, y - 30, x + 5, y], "confidence": 0.9})
    # The keeper stops the ball on the line instead of it going in.
    for f in frames:
        if f["ball"] is not None and f["ball"]["x"] > img((39.0, 0))[0]:
            f["ball"] = ball((39.4, 11.2))
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    shot = next(e for e in out["events"] if e["type"] == "shot")
    assert shot["outcome"] == "saved" and shot["onTarget"] is True


def test_worker_rejects_nan_bool_teams_and_recovers_stuck_calibrations(tmp_path, monkeypatch):
    import json as _json

    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    job_id, directory = make_job(server, build_match())
    raw = b'{"decisions": [{"action": "add", "type": "shot", "t": NaN}]}'
    assert client.post(f"/jobs/{job_id}/review", content=raw, headers={"content-type": "application/json"}).status_code == 400
    assert client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "add", "type": "shot", "t": 1, "team": True}]}).json()["added"][0].get("team") is None
    huge = {"decisions": [{"action": "add", "type": "note", "t": 1}] * 200, "pad": "x" * 300000}
    assert client.post(f"/jobs/{job_id}/review", json=huge).status_code == 413
    # A calibration left 'processing' by a restart is reported as failed and can be retried.
    (directory / "calibration-job.json").write_text(_json.dumps({"state": "processing", "startedAt": 0}))
    server.recover_calibrations()
    assert client.get(f"/jobs/{job_id}/calibration").json()["job"]["state"] == "failed"
    monkeypatch.setattr(server.post_pool, "submit", lambda fn, *a: fn(*a))
    body = {"template": "futsal", "points": clicks(), "t": 0.0}
    assert client.post(f"/jobs/{job_id}/calibration", json=body).status_code == 200
    assert client.get(f"/jobs/{job_id}/calibration").json()["state"] == "ready"


def test_venue_calibration_is_saved_and_reused_and_refused_when_it_does_not_fit(tmp_path, monkeypatch):
    import json as _json

    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setattr(server.post_pool, "submit", lambda fn, *a: fn(*a))
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    first, _ = make_job(server, build_match())
    assert client.post("/venues", json={"name": "Court 2", "jobId": first}).status_code == 409  # no setup yet
    assert client.post(f"/jobs/{first}/calibration", json={"template": "futsal", "points": clicks(), "t": 0.0}).status_code == 200
    venue = client.post("/venues", json={"name": "Court 2", "jobId": first}).json()
    assert venue["name"] == "Court 2" and [v["id"] for v in client.get("/venues").json()] == [venue["id"]]
    second, _ = make_job(server, build_match())
    assert client.post(f"/jobs/{second}/calibration", json={"venue": venue["id"]}).status_code == 200
    cal = client.get(f"/jobs/{second}/calibration").json()
    assert cal["state"] == "ready" and cal["venue"]["name"] == "Court 2" and cal["coverage"] == 100.0
    assert client.get(f"/jobs/{second}/analysis").json()["calibrated"]
    # A different video size cannot reuse the venue.
    third, third_dir = make_job(server, build_match())
    result = _json.loads((third_dir / "result.json").read_text())
    result["video"] = {"width": 1280, "height": 720}
    (third_dir / "result.json").write_text(_json.dumps(result))
    client.post(f"/jobs/{third}/calibration", json={"venue": venue["id"]})
    job = client.get(f"/jobs/{third}/calibration").json()["job"]
    assert job["state"] == "failed" and "1280x720" in job["error"]
    assert client.post(f"/jobs/{third}/calibration", json={"venue": "0" * 32}).status_code == 404


def test_venue_reuse_is_verified_against_the_painted_lines(tmp_path):
    import cv2

    from app.vision import calibrate
    from tests.test_pitch import SIZE, TEMPLATE as FUTSAL, camera_homography, render

    H = camera_homography()
    frames, video = [], tmp_path / "match.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, SIZE)
    for i in range(30):
        writer.write(render(H))
        frames.append({"t": round(i / 5, 3), "scene": 0, "players": [], "ball": None, "camera": [1, 0, 0, 0, 1, 0]})
    writer.release()
    result = {"frames": frames, "sampleFps": 5.0, "video": {"width": SIZE[0], "height": SIZE[1]}}
    good = {"id": "v1", "name": "Court 1", "template": FUTSAL, "size": list(SIZE), "k1": 0.0, "H": np.linalg.inv(H).tolist()}
    out = calibrate.build_from_venue(result, good, video)
    assert out["venue"]["lineScore"] > 0.6 and out["coverage"] == 100.0
    # The camera was re-aimed since the venue was saved: refuse rather than map wrongly.
    moved = np.array([[1, 0, 60.0], [0, 1, 25.0], [0, 0, 1]])
    bad = {**good, "H": (np.linalg.inv(H) @ np.linalg.inv(moved)).tolist()}
    with pytest.raises(ValueError, match="do not line up"):
        calibrate.build_from_venue(result, bad, video)


def test_a_shot_is_inferred_when_the_shooters_touch_was_not_seen():
    frames = build_match()
    # Hide the ball whenever player 2 has it: the release is never observed.
    for f in frames:
        if f["ball"] is not None and abs(f["ball"]["x"] - img((18.3, 12))[0]) < 1:
            f["ball"] = None
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    shots = [e for e in out["events"] if e["type"] == "shot"]
    assert len(shots) == 1 and shots[0]["team"] == 0 and shots[0].get("inferred")
    assert shots[0]["needsReview"] and shots[0]["confidence"] <= 0.35


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setattr(server, "_prepared", server.OrderedDict())
    monkeypatch.setattr(server.post_pool, "submit", lambda fn, *a: fn(*a))
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    return server, client


def test_decisions_use_the_moment_the_reviewer_saw_and_kept_moments_can_be_undone(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    job_id, directory = make_job(server, build_match())
    stale = client.get(f"/jobs/{job_id}/analysis").json()["events"]  # uncalibrated list
    assert client.post(f"/jobs/{job_id}/calibration", json={"template": "futsal", "points": clicks(), "t": 0.0}).status_code == 200
    now = client.get(f"/jobs/{job_id}/analysis").json()["events"]
    seen = stale[0]
    assert now[0]["type"] != seen["type"] or abs(now[0]["t"] - seen["t"]) > 0.05 or len(now) != len(stale)
    # The reviewer confirms what they saw on their (stale) list.
    out = client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "accept", "eventId": seen["id"], "event": {k: seen.get(k) for k in ("type", "t", "team")}}]}).json()
    confirmed = [e for e in out["analysis"]["events"] if e["status"] == "confirmed"]
    assert len(confirmed) == 1 and confirmed[0]["type"] == seen["type"] and abs(confirmed[0]["t"] - seen["t"]) <= 1.0
    assert "positions" not in out["analysis"] or out["analysis"]["positions"] is None
    # A confirmed moment whose detection has gone is kept, and can still be undone.
    kept_decision = {"action": "accept", "eventId": "ev-99", "event": {"type": "shot", "t": 1.5, "team": 1}}
    kept = [e for e in client.post(f"/jobs/{job_id}/review", json={"decisions": [kept_decision]}).json()["analysis"]["events"] if e["id"].startswith("kept-")]
    assert len(kept) == 1
    undone = client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "reject", "eventId": kept[0]["id"]}]})
    assert undone.status_code == 200
    assert [e["status"] for e in undone.json()["analysis"]["events"] if e["id"] == kept[0]["id"]] == ["rejected"]


def test_review_keypresses_do_not_recompute_the_whole_analysis(tmp_path, monkeypatch):
    from app.vision import analytics as A

    server, client = _client(tmp_path, monkeypatch)
    job_id, _ = make_job(server, build_match())
    calls = []
    real = A.prepare
    monkeypatch.setattr(A, "prepare", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = client.get(f"/jobs/{job_id}/analysis").json()
    for e in first["events"][:3]:
        assert client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "accept", "eventId": e["id"]}]}).status_code == 200
    assert len(calls) == 1
    # Changing the attacking direction does need a re-analysis.
    client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "direction", "value": "left"}]})
    assert len(calls) == 2


def test_calibration_can_be_polled_without_its_frames(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    job_id, _ = make_job(server, build_match())
    client.post(f"/jobs/{job_id}/calibration", json={"template": "futsal", "points": clicks(), "t": 0.0})
    assert "frames" in client.get(f"/jobs/{job_id}/calibration").json()
    light = client.get(f"/jobs/{job_id}/calibration?frames=0").json()
    assert light["state"] == "ready" and "frames" not in light


def test_venues_are_private_to_the_browser_that_saved_them(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    job_id, _ = make_job(server, build_match())
    client.post(f"/jobs/{job_id}/calibration", json={"template": "futsal", "points": clicks(), "t": 0.0})
    mine, theirs = "a" * 32, "b" * 32
    venue = client.post(f"/venues?owner={mine}", json={"name": "Court 1", "jobId": job_id}).json()
    assert [v["id"] for v in client.get(f"/venues?owner={mine}").json()] == [venue["id"]]
    assert client.get(f"/venues?owner={theirs}").json() == []
    other, _ = make_job(server, build_match())
    assert client.post(f"/jobs/{other}/calibration?owner={theirs}", json={"venue": venue["id"]}).status_code == 404
    assert client.post(f"/jobs/{other}/calibration?owner={mine}", json={"venue": venue["id"]}).status_code == 200


def test_an_unverifiable_venue_is_refused_for_a_moving_camera():
    from app.vision import calibrate

    frames = build_match()
    for f in frames:
        f["camera"] = [1, 0, 8.0, 0, 1, 0]  # panning
    result = result_for(frames)
    venue = {"id": "v", "name": "Court", "template": TEMPLATE, "size": [640, 360], "k1": 0.0, "H": np.linalg.inv(TO_IMAGE).tolist()}
    with pytest.raises(ValueError, match="no longer on the server"):
        calibrate.build_from_venue(result, venue, None)


def test_a_stale_accept_never_confirms_a_goal_the_reviewer_did_not_see():
    from app.vision.analytics import apply_review, confirmed_goals

    # The reviewer accepted a shot whose outcome was unknown.
    review = {"decisions": [{"action": "accept", "eventId": "ev-1", "event": {"type": "shot", "t": 3.6, "team": 0, "outcome": "unresolved"}}]}
    # Re-analysis (new calibration or direction) now reads it as a possible goal.
    after = [
        {"id": "ev-0", "type": "pass", "t": 1.0, "team": 0, "status": "proposed"},
        {"id": "ev-1", "type": "shot", "t": 3.6, "team": 0, "status": "proposed", "outcome": "goal-candidate", "onTarget": True},
    ]
    merged, _ = apply_review(after, review)
    shots = [e for e in merged if e["type"] == "shot"]
    assert len(shots) == 1 and shots[0]["status"] == "confirmed" and shots[0]["outcome"] == "unresolved"
    assert confirmed_goals(merged, 0) == 0
    # A fingerprint without an outcome is read as an unresolved shot too.
    review["decisions"][0]["event"].pop("outcome")
    merged, _ = apply_review(after, review)
    assert confirmed_goals(merged, 0) == 0


def test_an_outfield_defender_winning_the_ball_near_goal_is_a_block_not_a_save():
    frames = build_match()
    for f in frames:
        f["players"].append(player(14, 1, (36.5, 11.2)))
        if f["ball"] is not None and f["ball"]["x"] > img((36.0, 0))[0]:
            f["ball"] = ball((36.8, 11.2))
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    shot = next(e for e in out["events"] if e["type"] == "shot")
    assert shot["outcome"] == "blocked" and shot["onTarget"] is False
    assert out["stats"]["teams"][0]["shotsOnTarget"]["value"] == 0


def test_the_opening_kick_off_after_a_warm_up_is_not_a_possible_goal():
    frames = []
    t = 0.0

    def add(players, ball_xy):
        nonlocal t
        frames.append({"t": round(t, 2), "scene": 0, "players": players, "ball": ball(ball_xy) if ball_xy else None, "camera": [1, 0, 0, 0, 1, 0]})
        t += 0.2

    moving = lambda k: [player(1, 0, (10 + 0.3 * (k % 10), 8)), player(2, 0, (14, 12 + 0.3 * (k % 7))), player(3, 0, (6, 5 + 0.3 * (k % 5))),
                        player(11, 1, (26 - 0.3 * (k % 10), 9)), player(12, 1, (30, 6 + 0.3 * (k % 6))), player(13, 1, (33, 14 - 0.3 * (k % 8)))]
    for k in range(150):  # 30 s of warm-up
        add(moving(k), (10.3 + 0.3 * (k % 10), 8))
    # Line-up: everyone in their own half, team 1 on the centre spot, then kick-off.
    still = [player(1, 0, (12, 8)), player(2, 0, (15, 13)), player(3, 0, (8, 6)), player(11, 1, (20.5, 10)), player(12, 1, (26, 6)), player(13, 1, (28, 14))]
    for k in range(100):
        add(still, (20, 10) if k > 80 else None)
    for k in range(20):
        add(moving(200 + k), (20 + 0.5 * k, 10))
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert out["kickoffs"] and out["kickoffs"][0]["t"] > 20
    assert not any(e["type"] == "goal-candidate" for e in out["events"])


def test_counts_are_unmeasured_not_zero_without_kit_labels():
    frames = build_match()
    for f in frames:
        for p in f["players"]:
            p["team"] = -1
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    team0 = out["stats"]["teams"][0]
    for key in ("passes", "passesComplete", "shots", "shotsOnTarget", "interceptions", "tackles", "possessions"):
        assert team0[key] is None, key
    # A pass the reviewer logged by hand is counted.
    review = {"decisions": [{"action": "add", "type": "pass", "t": 1.0, "team": 0}]}
    out = analytics.analyse(result_for(frames), calibration_for(frames), review=review)
    assert out["stats"]["teams"][0]["passes"]["value"] == 1


def _control_summary(control, seconds=120, events=()):
    """summarise() over `seconds` of play at 5 fps; `control` maps a frame index to the team in control."""
    n = int(seconds * FPS)
    projected = [{"t": i / FPS, "calibrated": False, "players": [], "ball": None} for i in range(n)]
    states = [{"state": "control", "team": control[i]} if i in control else {"state": "unknown"} for i in range(n)]
    sequences, seconds_by_team, run = [], [0.0, 0.0], None
    for i in range(n + 1):
        team = control.get(i)
        if run and (team != run["team"] or i == n):
            run["seconds"] = round((i - run["first"]) / FPS, 1)
            seconds_by_team[run["team"]] += run["seconds"]
            sequences.append(run)
            run = None
        if team is not None and run is None:
            run = {"team": team, "first": i, "start": i / FPS}
    return analytics.summarise(projected, states, [], list(events), None, None, {}, FPS, seconds, 0, [True] * n, None, sequences, seconds_by_team, float(seconds))


def test_possession_counts_follow_the_coverage_gate_of_the_share():
    # One short blip of control for team 0 over two minutes of play (job 4255b6f5: 1.7%).
    stats = _control_summary({i: 0 for i in range(2)})
    assert stats["coverage"]["possessionShown"] is False
    for team in stats["teams"]:
        assert team["possession"] is None and team["possessions"] is None and team["averagePossession"] is None
    # Followed for most of play, the counts are measured (and a team without a possession is 0).
    stats = _control_summary({i: 0 for i in range(500)} | {i: 1 for i in range(500, 600)})
    assert stats["coverage"]["possessionShown"] is True
    assert [t["possessions"] for t in stats["teams"]] == [1, 1]
    assert [t["averagePossession"] for t in stats["teams"]] == [100.0, 20.0]
    stats = _control_summary({i: 0 for i in range(600)})
    assert [t["possessions"] for t in stats["teams"]] == [1, 0]


def test_a_momentum_minute_needs_enough_observed_control():
    # Minute 1: 20 s of team 0 against 10 s of team 1. Minute 2: 2.6 s observed (2.2 s against 0.4 s).
    control = {i: 0 for i in range(100)} | {i: 1 for i in range(100, 150)}
    control |= {i: 0 for i in range(300, 311)} | {i: 1 for i in range(311, 313)}
    momentum = _control_summary(control)["momentum"]
    assert momentum[0] == pytest.approx(1 / 3, abs=0.01)
    assert momentum[1] is None


def _reviewer(kind, team, t):
    return {"id": f"added-{kind}-{t}", "type": kind, "t": t, "team": team, "status": "confirmed", "source": "reviewer", "confidence": 1.0,
            **({"outcome": "on-target", "onTarget": True} if kind == "shot" else {})}


def test_reviewer_shots_count_without_a_pitch_setup_but_a_goal_alone_does_not():
    # The goal two seconds after the shot is that shot's goal: one shot, not two.
    stats = _control_summary({}, events=[_reviewer("shot", 0, 40.0), _reviewer("goal", 0, 42.0)])
    a, b = stats["teams"]
    assert a["shots"]["value"] == 1 and b["shots"]["value"] == 0
    assert a["shotsOnTarget"]["value"] == 1 and b["shotsOnTarget"]["value"] == 0
    assert a["goals"] == {"value": 1, "candidates": None} and b["goals"]["candidates"] is None
    # A reviewer goal alone does not make "no shots" known next to a 1-0 score.
    stats = _control_summary({}, events=[_reviewer("goal", 0, 44.0)])
    assert [t["shots"] for t in stats["teams"]] == [None, None]
    assert stats["teams"][0]["goals"]["value"] == 1
    # Nothing logged: still unmeasured.
    assert [t["shots"] for t in _control_summary({})["teams"]] == [None, None]


def test_an_off_pitch_bystander_does_not_split_a_player_across_a_missed_frame():
    def run(bystander_start):
        frames = []
        for k in range(21):
            players = []
            if k != 10:
                players.append(player(1 if k < 10 else 2, 0, (5 + 0.8 * k, 10)))
            if bystander_start is not None and k >= bystander_start:
                players.append(player(3, -1, (20, -2.5)))  # beyond the off-pitch margin
            frames.append({"t": round(k * 0.2, 2), "scene": 0, "players": players, "ball": None, "camera": [1, 0, 0, 0, 1, 0]})
        projected, _ = analytics.project(frames, calibration_for(frames))
        return analytics.stitch_tracks(projected, FPS)

    for start in (None, 10, 12):
        mapping = run(start)
        assert mapping[1] == mapping[2], start


def test_malformed_review_decisions_are_refused_not_server_errors(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    job_id, _ = make_job(server, build_match())
    for decision in (
        {"action": ["accept"]},
        {"action": "add", "type": ["shot"], "t": 1.0},
        {"action": "add", "type": "shot", "t": "soon"},
        {"action": "add", "type": "shot", "t": 10**400},
        {"action": "add", "type": "shot", "t": True},
    ):
        assert client.post(f"/jobs/{job_id}/review", json={"decisions": [decision]}).status_code == 400, decision
    # A fingerprint with an unhashable type is ignored, not a crash.
    ok = client.post(f"/jobs/{job_id}/review", json={"decisions": [{"action": "accept", "eventId": "ev-0", "event": {"type": ["shot"], "t": 1.0}}]})
    assert ok.status_code == 200


def test_possession_bounds_include_unseen_play_not_detector_accuracy():
    frames = build_match()
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    coverage = out["stats"]["coverage"]
    teams = out["stats"]["teams"]
    low, high = coverage["possessionMissingBounds"]
    total = coverage["inPlaySeconds"]
    assigned = sum(t["possessionSeconds"] for t in teams)
    assert low == pytest.approx(teams[0]["possessionSeconds"] / total * 100, abs=0.1)
    assert high - low == pytest.approx((total - assigned) / total * 100, abs=0.2)
    assert "errors are not included" in coverage["uncertaintyNote"]
    for frame in frames:
        frame["ball"] = None
    empty = analytics.analyse(result_for(frames), calibration_for(frames))
    assert empty["stats"]["coverage"]["possessionMissingBounds"] is None


def test_checked_sample_estimates_the_unchecked_detections():
    from app.vision import analytics as an

    def passes():
        return [{"id": f"ev-{i}", "type": "pass", "t": float(i), "team": i % 2, "confidence": 0.6, "status": "proposed", "outcome": "complete"} for i in range(100)]

    seen = ([{"t": 0.0, "calibrated": False, "players": [], "ball": None}], [{"state": "control", "team": 0}])

    def stats_for(review):
        events, _ = an.apply_review(passes(), review)
        return an.summarise(*seen, [], events, None, {"segments": []}, {}, 5, 100, 0, [], None, [], [0.0, 0.0], 0.0)

    # The reviewer checked 20 in the random-sample view: 15 right, 5 wrong.
    review = {"decisions": [{"action": "accept" if i < 15 else "reject", "eventId": f"ev-{i}", "sample": True} for i in range(20)]}
    team0 = stats_for(review)["teams"][0]["passes"]
    # Team 0: 8 confirmed of its 10 checked, and 40 unchecked, estimated at 75%.
    assert team0["confirmed"] == 8 and team0["pending"] == 40 and team0["checked"] == 20
    assert team0["estimate"] == 8 + 30
    lo, hi = team0["estimateRange"]
    assert lo < team0["estimate"] < hi and lo >= 8 and hi <= 48
    # The same decisions made outside the random sample do not estimate passes.
    unsampled = {"decisions": [{k: v for k, v in d.items() if k != "sample"} for d in review["decisions"]]}
    assert "estimate" not in stats_for(unsampled)["teams"][0]["passes"]
    # A check is judged against what the detection originally said: a 'complete'
    # pass re-labelled 'intercepted' is a miss for completed passes, a hit for passes.
    relabel = {"decisions": [{"action": "outcome", "eventId": f"ev-{i}", "value": "intercepted", "sample": True} for i in range(10)]}
    stats = stats_for(relabel)["teams"][0]
    assert stats["passesComplete"]["checked"] == 10 and stats["passesComplete"]["checkedCorrect"] == 0
    assert stats["passesComplete"]["estimate"] == 0
    assert stats["passes"]["checkedCorrect"] == 10


def test_off_pitch_points_do_not_pile_onto_the_heatmap_edge():
    grid = analytics._grid([(-5, 10), (0, 0), (40, 20), (50, 10)], {"length": 40, "width": 20}, nx=4, ny=2)
    assert grid[0][0] == 0.5
    assert grid[1][3] == 0.5
    assert sum(sum(row) for row in grid) == pytest.approx(1)


def test_a_camera_pan_is_not_a_pass():
    from app.vision.metrics import derive_metrics

    def person(pid, x):
        return {"id": pid, "team": 0, "box": [x - 20, 80, x + 20, 200]}

    frames = []
    for i in range(3):
        frames.append({
            "t": i / 5, "scene": 0,
            "players": [person(1, 100), person(2, 400)],
            "ball": {"x": 100, "y": 200, "confidence": 0.8, "trackId": 1},
            "camera": [1, 0, 0, 0, 1, 0],
        })
    for i in range(3, 6):
        frames.append({
            "t": i / 5, "scene": 0,
            "players": [person(1, 480), person(2, 180)],
            "ball": {"x": 180, "y": 200, "confidence": 0.8, "trackId": 1},
            "camera": [1, 0, 80 if i == 3 else 0, 0, 1, 0],
        })
    assert derive_metrics(frames, 5, 1.2)["events"] == []


def test_possession_survives_a_long_unseen_stretch_only_between_same_team_controls():
    from app.vision import analytics as an

    fps = 5.0
    n = 200
    projected = [{"t": i / fps, "scene": 0} for i in range(n)]
    in_play = [True] * n

    def spell(team, first, last):
        return {"team": team, "first": first, "last": last, "start": first / fps, "end": last / fps, "scene": 0}

    # Team 0 controls, the ball is unseen for 20 s, team 0 controls again.
    owner, _, seconds, _ = an.possession_sequences(projected, [spell(0, 0, 9), spell(0, 110, 119)], in_play, fps)
    assert all(o == 0 for o in owner[:120])
    # Different teams either side: only the usual short bridge, the rest is unknown.
    owner, _, _, _ = an.possession_sequences(projected, [spell(0, 0, 9), spell(1, 110, 119)], in_play, fps)
    assert owner[50] is None and owner[115] == 1
    # Never across a stoppage.
    stopped = [True] * n
    stopped[60] = False
    owner, _, _, _ = an.possession_sequences(projected, [spell(0, 0, 9), spell(0, 110, 119)], stopped, fps)
    assert owner[50] is None


def test_interceptions_are_estimated_from_sampled_checks_and_are_not_key_moments():
    from app.vision import analytics as an

    events = [{"id": f"ev-{i}", "type": "interception", "t": float(i), "team": i % 2, "confidence": 0.5, "status": "proposed"} for i in range(40)]
    events.append({"id": "ev-99", "type": "shot", "t": 99.0, "team": 0, "confidence": 0.5, "status": "proposed", "needsReview": True})
    review = {"decisions": [{"action": "accept" if i < 8 else "reject", "eventId": f"ev-{i}", "sample": True} for i in range(10)]}
    merged, _ = an.apply_review(events, review)
    seen = ([{"t": 0.0, "calibrated": False, "players": [], "ball": None}], [{"state": "control", "team": 0}])
    stats = an.summarise(*seen, [], merged, None, {"segments": []}, {}, 5, 100, 0, [], None, [], [0.0, 0.0], 0.0)
    team0 = stats["teams"][0]["interceptions"]
    assert team0["checked"] == 10 and team0["checkedCorrect"] == 8 and team0["estimate"] == 4 + round(15 * 0.8)
    assert an.SAMPLED_TYPES == ("pass", "interception", "tackle")
    # A decision made outside the random-sample view is not part of the estimate.
    review["decisions"].append({"action": "accept", "eventId": "ev-20"})
    merged, _ = an.apply_review(events, review)
    stats = an.summarise(*seen, [], merged, None, {"segments": []}, {}, 5, 100, 0, [], None, [], [0.0, 0.0], 0.0)
    assert stats["teams"][0]["interceptions"]["checked"] == 10
    # The review queue: interceptions are sampled, never key moments; the shot is.
    base = {"projected": seen[0], "states": seen[1], "spells": [], "events": events, "template": None,
            "directions": {"segments": []}, "player_of": {}, "sample_fps": 5, "duration": 100, "start": 0,
            "in_play": [], "owner": None, "sequences": [], "possession_seconds": [0.0, 0.0], "in_play_seconds": 0.0,
            "kickoffs": [], "positions": None}
    queue = an.finish(base)["review"]
    assert queue["pendingKey"] == 1 and queue["pendingByType"]["interception"] == 40


def test_a_finished_match_is_set_up_from_a_saved_venue_that_matches_its_lines(tmp_path, monkeypatch):
    import json as _json

    from app.vision import calibrate, server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    owner = "a" * 32
    job, directory = make_job(server, build_match())
    (directory / "video").write_bytes(b"footage")
    venues = server._venue_dir()
    size = [640, 360]
    for k, (name, who) in enumerate((("Court 1", owner), ("Court 2", owner), ("Theirs", "b" * 32))):
        (venues / f"{k:032x}.json").write_text(_json.dumps({"id": f"{k:032x}", "name": name, "owner": who, "size": size, "createdAt": k, "template": TEMPLATE, "H": [1, 0, 0, 0, 1, 0, 0, 0, 1]}))
    tried = []

    def fake(result, venue, video):
        tried.append(venue["name"])
        if venue["name"] != "Court 1":
            raise ValueError("The painted lines in this match do not line up with the saved venue")
        return {"state": "ready", "template": TEMPLATE, "frames": [], "coverage": 100.0, "venue": {"id": venue["id"], "name": venue["name"], "lineScore": 0.8, "verified": True}}

    monkeypatch.setattr(calibrate, "build_from_venue", fake)
    monkeypatch.setattr(server, "compute_analysis", lambda d: None)
    assert server._auto_venue(directory, {"owner": owner}) == f"{0:032x}"
    # The newest of the owner's venues is tried first; another owner's never.
    assert tried == ["Court 2", "Court 1"]
    cal = _json.loads((directory / "calibration.json").read_text())
    assert cal["auto"] is True and cal["venue"]["name"] == "Court 1"
    # Never over an existing setup, and never without footage to check against.
    tried.clear()
    assert server._auto_venue(directory, {"owner": owner}) is None and tried == []
    (directory / "calibration.json").unlink()
    (directory / "video").unlink()
    assert server._auto_venue(directory, {"owner": owner}) is None and tried == []


def test_off_pitch_players_are_left_out_of_heatmaps_and_average_positions():
    from app.vision import analytics as an

    def stats_for(bystander):
        projected = []
        for i in range(200):  # 40 s
            players = [{"id": 1, "team": 0, "role": "player", "xy": (10.0, 8.0)}, {"id": 11, "team": 1, "role": "player", "xy": (30.0, 12.0)}]
            if bystander:
                players.append({"id": 2, "team": 0, "role": "player", "xy": (-3.0, 10.0)})  # behind the goal line
            projected.append({"t": i / 5, "calibrated": True, "players": players, "ball": None})
        states = [{"state": "unknown"} for _ in projected]
        directions = {"segments": [{"start": 0.0, "end": math.inf, "team0Attacks": "right"}]}
        return an.summarise(projected, states, [], [], TEMPLATE, directions, {}, 5, 40, 0, [True] * 200, None, [], [0.0, 0.0], 40.0)

    clean, crowded = stats_for(False), stats_for(True)
    assert crowded["heatmaps"] == clean["heatmaps"]
    assert sorted(a["player"] for a in crowded["averagePositions"]) == [1, 11]


def test_a_finished_analysis_starts_the_automatic_venue_setup(tmp_path, monkeypatch):
    import threading

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    job, directory = make_job(server, build_match())
    calls = []
    monkeypatch.setattr(server, "_auto_venue", lambda d, status: calls.append((d, status["id"])))
    monkeypatch.setattr(server, "run_video", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.gpu, "modal_enabled", lambda: False)
    monkeypatch.setattr(server.gpu, "public_base", lambda: "")

    class Inline:
        """Runs the thread's target at start(), so the test does not race a daemon thread."""

        def __init__(self, target=None, args=(), kwargs=None, **options):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(server.threading, "Thread", Inline)
    status = {"id": job, "status": "processing", "createdAt": 0, "owner": "a" * 32}
    server.work(directory, status, threading.Event())
    assert status["status"] == "completed"
    assert calls == [(directory, job)]


def _restart_frames(open_play, restart_seconds=15, after=None):
    """Frames at 5 fps: `open_play(k)` players for each k, then a centre restart
    (everyone in their own half, team 1 on the spot), then play resumes."""
    frames = []
    t = 0.0

    def add(players, ball_xy):
        nonlocal t
        frames.append({"t": round(t, 2), "scene": 0, "players": players, "ball": ball(ball_xy) if ball_xy else None, "camera": [1, 0, 0, 0, 1, 0]})
        t += 0.2

    for k, (players, ball_xy) in enumerate(open_play):
        add(players, ball_xy)
    still = [player(1, 0, (12, 8)), player(2, 0, (15, 13)), player(3, 0, (8, 6)), player(11, 1, (20.5, 10)), player(12, 1, (26, 6)), player(13, 1, (28, 14))]
    for k in range(int(restart_seconds * 5)):
        add(still, (20, 10) if k > restart_seconds * 5 - 10 else None)
    moving = lambda k: [player(1, 0, (10 + 0.3 * (k % 10), 8)), player(2, 0, (14, 12 + 0.3 * (k % 7))), player(3, 0, (6, 5 + 0.3 * (k % 5))),
                        player(11, 1, (26 - 0.3 * (k % 10), 9)), player(12, 1, (30, 6 + 0.3 * (k % 6))), player(13, 1, (33, 14 - 0.3 * (k % 8)))]
    for k in range(20):
        add(moving(200 + k), (20 + 0.5 * k, 10))
    return frames


def test_a_goal_restart_minutes_into_the_match_is_a_possible_goal_even_as_the_first_kick_off():
    """Recordings that start at kick-off never show the opening restart: the first
    restart found, nine minutes in after real play, is evidence of a goal."""
    import math as m

    def mixed(k):
        # Real play: one player of each team in the other team's half.
        w = m.sin(k / 7)
        return [player(1, 0, (10 + 3 * w, 8)), player(2, 0, (14, 12 + 2 * w)), player(3, 0, (25 + 2 * m.sin(k / 5), 15)),
                player(11, 1, (28 - 3 * w, 9)), player(12, 1, (32, 6 + 2 * w)), player(13, 1, (15 - 2 * m.sin(k / 6), 4))], (10.3 + 3 * w, 8)

    frames = _restart_frames([mixed(k) for k in range(9 * 60 * 5)])
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert len(out["kickoffs"]) == 1 and out["kickoffs"][0]["t"] > 540
    goals = [e for e in out["events"] if e["type"] == "goal-candidate"]
    assert len(goals) == 1 and goals[0]["team"] == 0 and "centre-restart" in goals[0]["evidence"]
    assert "the shot itself was not seen" in goals[0]["note"]
    assert out["stats"]["teams"][0]["goals"]["value"] == 0  # a reviewer confirms goals


def test_a_line_up_interrupted_by_a_shuffle_is_one_kick_off_not_a_goal():
    moving = lambda k: [player(1, 0, (10 + 0.3 * (k % 10), 8)), player(2, 0, (14, 12 + 0.3 * (k % 7))), player(3, 0, (6, 5 + 0.3 * (k % 5))),
                        player(11, 1, (26 - 0.3 * (k % 10), 9)), player(12, 1, (30, 6 + 0.3 * (k % 6))), player(13, 1, (33, 14 - 0.3 * (k % 8)))]

    def shuffle(k):
        d = 0.5 * (k if k < 5 else 10 - k)  # players walk out 2.5 m and back
        return [player(1, 0, (12 - d, 8)), player(2, 0, (15 - d, 13)), player(3, 0, (8 - d, 6)),
                player(11, 1, (20.5 + 3 + d, 10)), player(12, 1, (26 + d, 6)), player(13, 1, (28 + d, 14))]

    still = [player(1, 0, (12, 8)), player(2, 0, (15, 13)), player(3, 0, (8, 6)), player(11, 1, (20.5, 10)), player(12, 1, (26, 6)), player(13, 1, (28, 14))]
    warm_up = [(moving(k), (10.3 + 0.3 * (k % 10), 8)) for k in range(150)]
    line_up = [(still, None)] * 50 + [(shuffle(k), None) for k in range(11)]  # 10 s, then a 2 s shuffle
    frames = _restart_frames(warm_up + line_up, restart_seconds=10)
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert len(out["kickoffs"]) == 1
    assert not any(e["type"] == "goal-candidate" for e in out["events"])


def test_a_single_restart_like_frame_in_open_play_is_not_a_kick_off():
    from app.vision import analytics as an

    moving = lambda k: [player(1, 0, (10 + 0.3 * (k % 10), 8)), player(2, 0, (14, 12 + 0.3 * (k % 7))), player(3, 0, (6, 5 + 0.3 * (k % 5))),
                        player(11, 1, (26 - 0.3 * (k % 10), 9)), player(12, 1, (30, 6 + 0.3 * (k % 6))), player(13, 1, (33, 14 - 0.3 * (k % 8)))]
    frames = []
    for k in range(300):
        players = moving(k)
        if k == 150:  # one sample with a player passing over the centre spot
            players[3] = player(11, 1, (20.5, 10))
        frames.append({"t": round(k * 0.2, 2), "scene": 0, "players": players, "ball": ball((10.3 + 0.3 * (k % 10), 8)), "camera": [1, 0, 0, 0, 1, 0]})
    projected, template = an.project(frames, calibration_for(frames))
    directions = an.attacking_directions(projected, template)
    assert an.detect_kickoffs(projected, [], directions, template, FPS) == []
    out = analytics.analyse(result_for(frames), calibration_for(frames))
    assert out["kickoffs"] == []
