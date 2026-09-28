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
    assert team0["shots"] is None and team0["goals"] is None
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
