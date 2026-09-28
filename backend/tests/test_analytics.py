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
    assert team0["passAccuracy"] == 75.0
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
