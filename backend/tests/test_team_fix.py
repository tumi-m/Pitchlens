"""The owner's team fix: one tapped player per kit relabels the stored kit colours (E24).

The synthetic match is Tryzub's failure: kit clustering made the spectators
Kit B and left the green team unlabelled.
"""

import json
import time
from collections import OrderedDict

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.vision.engine import lab_hex
from app.vision.metrics import derive_metrics

JOB, OWNER = "a" * 32, "b" * 32
RED, GREEN, DARK = [100.0, 165.0, 150.0], [150.0, 104.0, 144.0], [35.0, 130.0, 126.0]
YELLOW, BLUE = [178.0, 109.0, 187.0], [80.0, 150.0, 70.0]
FRAMES = 40


def build_result():
    rng = np.random.default_rng(0)
    frames = []
    for k in range(FRAMES):
        players = []

        def add(pid, team, colour, x, y, role="player", height=40):
            feature = np.array(colour) + rng.normal(0, 2, 3)
            players.append({
                "id": pid, "team": team, "role": role, "box": [x - 8, y - height, x + 8, y],
                "confidence": 0.9, "kitFeature": [round(float(v), 1) for v in feature],
            })

        for j, pid in enumerate((1, 2, 3)):  # red: the engine's Kit A
            add(pid, 0, RED, 100 + 60 * j + 3 * k, 150 + 40 * j)
        for j, pid in enumerate((11, 12, 13)):  # green: the kit the engine missed
            add(pid, -1, GREEN, 500 - 60 * j - 3 * k, 160 + 40 * j)
        for j, pid in enumerate((21, 22, 23)):  # spectators, standing: the engine's Kit B
            add(pid, 1, DARK, 40 + 250 * j, 345)
        add(31, -1, YELLOW, 320, 100, role="referee")
        add(41, -1, BLUE, 600, 120)  # a keeper in his own colour
        add(51, -1, GREEN, 300, 60, height=20)  # too small to judge by eye
        # Red has the ball for the first half, green for the second.
        owner = players[0] if k < FRAMES // 2 else players[3]
        x, y = (owner["box"][0] + owner["box"][2]) / 2, owner["box"][3]
        ball = {"x": x, "y": y - 2, "box": [x - 2, y - 4, x + 2, y], "confidence": 0.8, "trackId": 1}
        frames.append({"t": round(k / 5, 2), "frame": 5 * k, "scene": 0, "players": players, "ball": ball, "camera": [1, 0, 0, 0, 1, 0]})
    duration = FRAMES / 5
    return {
        "pipelineVersion": "local-vision-2.8",
        "video": {"width": 640, "height": 360, "fps": 25, "duration": duration},
        "sampleFps": 5.0, "analysedStart": 0, "analysedDuration": duration, "ballSearch": "whole-frame",
        "teams": [{"id": 0, "label": "Kit A", "colour": lab_hex(RED)}, {"id": 1, "label": "Kit B", "colour": lab_hex(DARK)}],
        "metrics": derive_metrics(frames, 5.0, duration),
        "frames": frames,
        "limitations": [],
    }


@pytest.fixture
def job(tmp_path, monkeypatch):
    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setattr(server, "active", None)
    monkeypatch.setattr(server, "RETENTION_HOURS", 0)
    for cache in ("_prepared", "_slim", "_crops"):
        monkeypatch.setattr(server, cache, OrderedDict())
    # Frame extraction is the ball labeller's (tested there): ready at once here.
    monkeypatch.setattr(server, "_prepare_label_frames", lambda *a: True)
    directory = tmp_path / JOB
    directory.mkdir()
    server.write_status(directory, {"id": JOB, "owner": OWNER, "status": "completed", "createdAt": time.time()})
    (directory / "video").write_bytes(b"0123456789")
    (directory / "result.json").write_text(json.dumps(build_result()))
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers.update({"Authorization": "Bearer test-token", "x-pitchlens-owner": OWNER})
    return server, client, directory


def stored(directory):
    return json.loads((directory / "result.json").read_text())


def teams_by_track(result):
    seen = {}
    for f in result["frames"]:
        for p in f["players"]:
            seen.setdefault(p["id"], set()).add(p["team"])
    return seen


def pick(pid, index=3):
    return {"index": index, "id": pid}


def test_another_owner_cannot_read_or_fix_the_teams(job):
    _, client, directory = job
    before = (directory / "result.json").read_bytes()
    other = {"x-pitchlens-owner": "c" * 32}
    assert client.get(f"/jobs/{JOB}/teams", headers=other).status_code == 404
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)}, headers=other).status_code == 404
    assert client.post(f"/jobs/{JOB}/teams", json={"reset": True}, headers=other).status_code == 404
    assert (directory / "result.json").read_bytes() == before
    assert client.get(f"/jobs/{JOB}/teams").status_code == 200


def test_crops_show_each_kit_and_the_missing_kit_first(job):
    _, client, _ = job
    data = client.get(f"/jobs/{JOB}/teams").json()
    assert data["framesReady"] is True and data["size"] == [640, 360] and data["teamsSetBy"] is None
    assert data["colours"] == [lab_hex(RED), lab_hex(DARK)]
    groups = data["groups"]
    assert {c["id"] for c in groups["A"]} == {1, 2, 3}
    assert {c["id"] for c in groups["B"]} == {21, 22, 23}
    unsorted = [c["id"] for c in groups["unsorted"]]
    # The densest unsorted colour (the green team) first, then the farthest colour;
    # never the referee, and never a player too small to judge.
    assert unsorted[0] in (11, 12, 13) and unsorted[1] == 41 and len(unsorted) == 4
    assert 31 not in unsorted and 51 not in unsorted
    for crop in groups["A"] + groups["B"] + groups["unsorted"]:
        assert set(crop) == {"index", "id", "box"} and crop["box"][3] - crop["box"][1] >= 0.09 * 360


@pytest.mark.parametrize(
    "body, detail",
    [
        ({"kitA": pick(1), "kitB": pick(11), "colour": "#ffffff"}, "Unknown key"),
        ({"kitA": pick(1), "kitB": {"index": 3, "id": 11, "x": 1}}, "must be a player"),
        ({"kitA": pick(1), "kitB": {"index": True, "id": 11}}, "must be a player"),
        ({"kitA": pick(1)}, "must be a player"),
        ({"kitA": pick(1), "kitB": pick(11, index=FRAMES)}, "Unknown frame"),
        ({"kitA": pick(1), "kitB": pick(77)}, "not in this frame"),
        ({"kitA": pick(1), "kitB": pick(31)}, "outfield player"),
        ({"kitA": pick(1), "kitB": pick(1, index=7)}, "different player"),
        ({"kitA": pick(1), "kitB": pick(2)}, "look the same"),
        ({"reset": False}, "reset"),
        ({"reset": True, "kitA": pick(1)}, "reset"),
    ],
)
def test_a_wrong_team_fix_is_refused_and_changes_nothing(job, body, detail):
    _, client, directory = job
    before = (directory / "result.json").read_bytes()
    response = client.post(f"/jobs/{JOB}/teams", json=body)
    assert response.status_code == 400 and detail in response.json()["detail"], response.text
    assert (directory / "result.json").read_bytes() == before


@pytest.mark.parametrize("raw", [b"[1]", b"{\"reset\": NaN}", b"{" * 50, b"x" * 2000])
def test_malformed_team_fix_bodies_are_refused(job, raw):
    _, client, _ = job
    response = client.post(f"/jobs/{JOB}/teams", content=raw, headers={"content-type": "application/json"})
    assert response.status_code == 400


def test_a_running_job_or_a_result_without_kit_colours_cannot_be_fixed(job):
    server, client, directory = job
    status = server.load_status(directory)
    server.write_status(directory, {**status, "status": "processing"})
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)}).status_code == 409
    server.write_status(directory, status)
    server.active = JOB
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)}).status_code == 409
    server.active = None
    # Results from before pipeline 2.2 kept no kit colour per detection.
    old = build_result()
    for f in old["frames"]:
        for p in f["players"]:
            del p["kitFeature"]
    (directory / "result.json").write_text(json.dumps(old))
    assert client.get(f"/jobs/{JOB}/teams").status_code == 409
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)}).status_code == 409


def test_crops_need_the_footage(job):
    _, client, directory = job
    (directory / "video").unlink()
    response = client.get(f"/jobs/{JOB}/teams")
    assert response.status_code == 409 and "footage" in response.json()["detail"]


def test_tapping_one_player_per_kit_relabels_every_detection(job):
    _, client, directory = job
    before = stored(directory)
    first = client.get(f"/jobs/{JOB}/analysis").json()  # caches the engine's analysis
    assert first["teams"][1]["colour"] == lab_hex(DARK)
    response = client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)})
    assert response.status_code == 200, response.text
    assert response.json()["swapped"] is False and response.json()["teamsSetBy"] == "owner"
    after = stored(directory)
    labels = teams_by_track(after)
    assert all(labels[t] == {0} for t in (1, 2, 3))
    assert all(labels[t] == {1} for t in (11, 12, 13))
    # Spectators, the referee and the keeper are neither kit now.
    assert all(labels[t] == {-1} for t in (21, 22, 23, 31, 41))
    colours = [t["colour"] for t in after["teams"]]
    assert colours[1] != lab_hex(DARK) and after["teamsSetBy"] == "owner"
    assert np.linalg.norm(np.array(colours_lab(colours[1])) - GREEN) < 6
    # The engine's labels are kept for an undo.
    assert after["engineTeams"] == before["teams"]
    assert all(p["engineTeam"] == q["team"] for f, g in zip(after["frames"], before["frames"]) for p, q in zip(f["players"], g["players"]))
    # Running distance and possession follow the new labels.
    load_before, load_after = before["metrics"]["playerLoad"]["metres"], after["metrics"]["playerLoad"]["metres"]
    assert load_after[1] and (load_before[1] or 0) < load_after[1]
    assert before["metrics"]["teamSeconds"][1] == 0 < after["metrics"]["teamSeconds"][1]
    # No cached analysis survives the rewrite: both reads see the new kits.
    for _ in range(2):
        analysis = client.get(f"/jobs/{JOB}/analysis").json()
        assert [t["colour"] for t in analysis["teams"]] == colours
        assert analysis["playerLoad"]["metres"] == load_after
    groups = client.get(f"/jobs/{JOB}/teams").json()
    assert groups["teamsSetBy"] == "owner" and {c["id"] for c in groups["groups"]["B"]} == {11, 12, 13}


def colours_lab(hex_colour):
    import cv2

    rgb = np.uint8([int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)]).reshape(1, 1, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).reshape(3).astype(float)


def test_a_crosswise_tap_keeps_kit_a_and_says_so(job):
    _, client, directory = job
    response = client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(11), "kitB": pick(1)})
    assert response.status_code == 200 and response.json()["swapped"] is True
    after = stored(directory)
    labels = teams_by_track(after)
    # Index 0 stays on the engine's Kit A colour (red): the site swaps the names instead.
    assert all(labels[t] == {0} for t in (1, 2, 3)) and all(labels[t] == {1} for t in (11, 12, 13))
    assert np.linalg.norm(colours_lab(after["teams"][0]["colour"]) - RED) < 6


def test_reset_restores_the_engines_labels(job):
    _, client, directory = job
    original = stored(directory)
    assert client.post(f"/jobs/{JOB}/teams", json={"reset": True}).json()["teamsSetBy"] is None  # nothing to undo
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)}).status_code == 200
    # A second fix still remembers the engine's labels, not the first fix's.
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(2), "kitB": pick(12)}).status_code == 200
    assert stored(directory)["engineTeams"] == original["teams"]
    response = client.post(f"/jobs/{JOB}/teams", json={"reset": True})
    assert response.status_code == 200 and response.json()["teams"] == original["teams"]
    assert stored(directory) == original
    analysis = client.get(f"/jobs/{JOB}/analysis").json()
    assert [t["colour"] for t in analysis["teams"]] == [t["colour"] for t in original["teams"]]


def test_a_confirmed_goal_keeps_its_team_after_a_fix(job):
    _, client, _ = job
    review = {"decisions": [{"action": "add", "type": "goal", "t": 2.0, "team": 0}]}
    assert client.post(f"/jobs/{JOB}/review", json=review).status_code == 200

    def goals():
        return [t["goals"]["value"] for t in client.get(f"/jobs/{JOB}/analysis").json()["stats"]["teams"]]

    assert goals() == [1, 0]
    for body in ({"kitA": pick(11), "kitB": pick(1)}, {"kitA": pick(1), "kitB": pick(11)}, {"reset": True}):
        assert client.post(f"/jobs/{JOB}/teams", json=body).status_code == 200
        assert goals() == [1, 0], body


def test_a_player_load_job_keeps_possession_unmeasured(job):
    _, client, directory = job
    result = build_result()
    result["ballSearch"] = "none"
    for f in result["frames"]:
        f["ball"] = None
    result["metrics"].update(teamSeconds=None, possessionCoverage=None, possessionShare=[None, None], possessions=None)
    (directory / "result.json").write_text(json.dumps(result))
    assert client.post(f"/jobs/{JOB}/teams", json={"kitA": pick(1), "kitB": pick(11)}).status_code == 200
    metrics = stored(directory)["metrics"]
    assert metrics["teamSeconds"] is None and metrics["possessionCoverage"] is None
    assert metrics["possessionShare"] == [None, None] and metrics["possessions"] is None
    assert metrics["playerLoad"]["metres"][1]
