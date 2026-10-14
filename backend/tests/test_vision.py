import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.vision.engine import assign_team, field_mask, inside_field, probe
from app.vision.metrics import derive_metrics, possession_owner


def player(id=1, team=0, x=0):
    return {"id": id, "team": team, "box": [x, 0, x + 20, 60], "confidence": 0.9}


def frame(t, players=None, ball=True, scene=0):
    players = players or [player()]
    return {
        "t": t,
        "scene": scene,
        "players": players,
        "ball": {"x": players[0]["box"][0] + 10, "y": 60, "confidence": 0.8} if ball else None,
    }


def test_missing_ball_is_unknown_not_zero_possession():
    out = derive_metrics([frame(t, ball=False) for t in [0, 0.2, 0.4, 0.6, 0.8]], 5, 1)
    assert out["possessionShare"] == [None, None]
    assert out["unknownSeconds"] == 1
    assert out["events"] == []


def test_ambiguous_and_unknown_kit_have_no_owner():
    assert possession_owner([player(1, 0), player(2, 1)], {"x": 10, "y": 60}) is None
    assert possession_owner([player(team=-1)], {"x": 10, "y": 60}) is None


def test_single_frame_contact_does_not_create_possession():
    assert derive_metrics([frame(0), frame(0.2, ball=False)], 5, 0.4)["teamSeconds"] == [0, 0]


def test_visible_same_team_transfer_is_candidate_with_evidence():
    frames = [
        frame(0, [player(), player(2, 0, x=60)]),
        frame(0.2, [player(), player(2, 0, x=60)]),
        frame(0.4, [player(2, 0, x=60), player()]),
        frame(0.6, [player(2, 0, x=60), player()]),
    ]
    out = derive_metrics(frames, 5, 0.8)
    assert len(out["events"]) == 1
    assert out["events"][0]["type"] == "pass-candidate"
    assert out["events"][0]["source"] == "computer-vision"
    assert out["events"][0]["status"] == "unreviewed"
    assert out["teamSeconds"] == [0.8, 0]


def test_ball_gap_and_scene_cut_prevent_false_pass():
    for middle in [frame(0.4, ball=False), frame(0.4, scene=1)]:
        frames = [
            frame(0),
            frame(0.2),
            middle,
            frame(0.6, [player(2, 0)], scene=middle["scene"]),
            frame(0.8, [player(2, 0)], scene=middle["scene"]),
        ]
        assert derive_metrics(frames, 5, 1)["events"] == []


def test_temporal_opponent_transfer_is_turnover_candidate():
    frames = [frame(0), frame(0.2), frame(0.4, [player(2, 1)]), frame(0.6, [player(2, 1)])]
    out = derive_metrics(frames, 5, 0.8)
    assert out["events"][0]["type"] == "turnover-candidate"
    assert out["possessionShare"] == [50, 50]


def test_player_load_is_a_ball_search_mode():
    from app.vision.engine import run_video

    with pytest.raises(ValueError, match="Unknown ball search"):
        run_video("/no/such/video.mp4", "/tmp/out", ball_search="sideways")


def test_limitations_describe_running_distance_as_a_per_kit_estimate():
    import re

    from app.vision.engine import limitations

    notes = limitations(None, 180, 180, 180)
    text = " ".join(notes)
    assert not re.search(r"distance[^.;]*not measured", text)
    assert "per-kit estimate" in text and "speed and xG are not measured" in text
    # A kit failure leads the list; a file that ends early says so.
    short = limitations("Kits failed.", 180, 60, 180)
    assert short[0] == "Kits failed." and "decoding ended at 60 s" in short[-1]


def _load_player(pid=1, team=0, box=(92, 40, 108, 60)):
    return {"id": pid, "team": team, "role": "player", "box": list(box), "confidence": 0.9}


def test_a_camera_pan_is_not_player_running():
    from app.vision.metrics import player_load

    frames = []
    x = 100.0
    for i in range(6):
        frames.append({
            "t": i * 0.2,
            "scene": 0,
            "camera": None if i == 0 else [1, 0, 40, 0, 1, 0],
            "players": [_load_player(box=(x - 8, 40, x + 8, 60))],
            "ball": None,
        })
        x += 40
    # Kit A was measured and stood still (0.0); nobody in kit B was seen (None).
    assert player_load(frames)["metres"] == [0.0, None]


def test_running_distance_uses_body_height_and_drops_a_partial_box():
    from app.vision.metrics import player_load

    frames = []
    x = 100.0
    for i in range(6):
        height = 10 if i == 3 else 20
        frames.append({
            "t": i * 0.2,
            "scene": 0,
            "camera": None if i == 0 else [1, 0, 0, 0, 1, 0],
            "players": [_load_player(box=(x - 8, 80 - height, x + 8, 80))],
            "ball": None,
        })
        x += 10
    # 50 px of true movement (4.5 m at 1.8 m / 20 px). The half-height box is
    # skipped, the gap over it is bridged, and steps are at least 0.3 s: 0-0.4 s
    # and 0.4-0.8 s count (20 px each); the last 10 px is still inside its step.
    load = player_load(frames)
    assert load["metres"] == [3.6, None]
    assert load["measuredSamples"] == [4, 0]


def test_slow_walking_adds_up_instead_of_being_dropped_step_by_step():
    from app.vision.metrics import player_load

    # 1 px per sample at 10 fps: every step is under the 3 px band, but the
    # walk is 30 px (2.7 m) over 3 s.
    frames = [
        {"t": i * 0.1, "scene": 0, "camera": None if i == 0 else [1, 0, 0, 0, 1, 0],
         "players": [_load_player(box=(92 + i, 60, 108 + i, 80))], "ball": None}
        for i in range(31)
    ]
    assert player_load(frames)["metres"][0] == pytest.approx(2.7, abs=0.1)


def test_a_cut_without_a_camera_is_not_a_sprint():
    from app.vision.metrics import player_load

    frames = [
        {"t": 0, "scene": 0, "camera": None, "players": [_load_player()], "ball": None},
        {"t": 0.2, "scene": 1, "camera": None, "players": [_load_player(box=(200, 0, 216, 20))], "ball": None},
    ]
    # Nothing could be measured across the cut: not a measured 0.
    assert player_load(frames)["metres"] == [None, None]
    assert player_load(frames)["maxSpeed"] is None
    assert derive_metrics(frames, 5, 0.4)["playerLoad"]["steps"] == 0


def test_kits_that_were_not_separated_have_no_distance():
    from app.vision.metrics import player_load

    # The engine's kit warning path: every player's team is -1.
    frames = [
        {"t": i * 0.2, "scene": 0, "camera": None if i == 0 else [1, 0, 0, 0, 1, 0],
         "players": [_load_player(team=-1, box=(92 + 10 * i, 60, 108 + 10 * i, 80))], "ball": None}
        for i in range(6)
    ]
    load = player_load(frames)
    assert load["metres"] == [None, None]
    assert load["maxSpeed"] is None and load["measuredSamples"] == [0, 0]


def test_a_youtube_job_defaults_to_player_load(hosted):
    _server, client, submitted = hosted
    plain = client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ")
    assert plain.status_code == 200
    assert plain.json()["ballSearch"] == "none"
    asked = client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ&search=exhaustive")
    assert asked.status_code == 409
    assert submitted[0][2]["ballSearch"] == "none"
    refused = client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ&search=magic")
    assert refused.status_code == 400


def test_background_is_not_a_pitch_and_offscreen_is_rejected():
    mask = field_mask(np.zeros((100, 100, 3), np.uint8))
    assert mask.sum() == 0
    assert not inside_field(mask, [-30, 0, -10, 30])
    grass = np.full((100, 120), 255, np.uint8)
    assert inside_field(grass, [10, 40, 30, 102])  # feet two pixels past the bottom
    assert not inside_field(grass, [10, -40, 30, -5])  # entirely above the frame


def test_unknown_jersey_is_not_forced_to_a_team():
    assert assign_team(None, np.array([[100, 170, 140], [140, 100, 140]])) == -1


def test_invalid_video_fails(tmp_path):
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="decoded"):
        probe(f)


@pytest.fixture
def service(tmp_path, monkeypatch):
    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setattr(server, "active", None)
    monkeypatch.setattr(server, "cancellations", {})
    monkeypatch.setattr(server, "running", {})
    monkeypatch.setattr(server, "stopping", False)
    # A GPU error waits this long for a shutdown's cancellation (5 s in production).
    monkeypatch.setattr(server, "GPU_ERROR_GRACE", 0)
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    return server, client


def test_service_auth_and_path_validation(service):
    server, client = service
    assert client.get("/jobs", headers={"Authorization": "wrong"}).status_code == 401
    assert client.get("/jobs/not-a-job").status_code == 404
    assert client.get("/jobs").json() == []


def test_range_playback_and_interrupted_status(service):
    server, client = service
    job = "a" * 32
    folder = server.ROOT / job
    folder.mkdir()
    (folder / "video").write_bytes(b"0123456789")
    server.write_status(folder, {"id": job, "status": "processing", "createdAt": 0})
    assert client.get(f"/jobs/{job}").json()["status"] == "interrupted"
    r = client.get(f"/jobs/{job}/video", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.content == b"2345"
    assert r.headers["Content-Range"] == "bytes 2-5/10"
    assert client.get(f"/jobs/{job}/video", headers={"Range": "bytes=-3"}).content == b"789"
    assert client.get(f"/jobs/{job}/video", headers={"Range": "bytes=20-"}).status_code == 416
    assert client.get(f"/jobs/{job}/result").status_code == 409


def test_busy_upload_and_cancellation(service, monkeypatch, tmp_path):
    server, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    monkeypatch.setattr(server, "active", "b" * 32)
    assert (
        client.post("/jobs", content=b"data", headers={"Content-Type": "video/mp4"}).status_code
        == 409
    )
    folder = server.ROOT / ("b" * 32)
    folder.mkdir()
    server.write_status(folder, {"id": "b" * 32, "status": "processing", "createdAt": 0})
    event = threading.Event()
    server.cancellations["b" * 32] = event
    assert client.post("/jobs/" + ("b" * 32) + "/cancel").json()["requested"] is True
    assert event.is_set()


def test_oversized_upload_releases_worker_and_removes_partial_file(service, monkeypatch, tmp_path):
    server, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    monkeypatch.setattr(server, "MAX_BYTES", 4)
    assert (
        client.post("/jobs", content=b"12345", headers={"Content-Type": "video/mp4"}).status_code
        == 413
    )
    assert server.active is None
    assert not list(server.ROOT.glob("*/video"))


def test_track_id_switch_with_stationary_ball_is_not_a_pass():
    frames = [frame(0), frame(0.2), frame(0.4, [player(2, 0)]), frame(0.6, [player(2, 0)])]
    assert derive_metrics(frames, 5, 0.8)["events"] == []


def test_corrupt_upload_returns_actionable_error_and_releases_worker(
    service, monkeypatch, tmp_path
):
    server, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    response = client.post("/jobs", content=b"broken mp4", headers={"Content-Type": "video/mp4"})
    assert response.status_code == 400
    assert "decoded" in response.json()["detail"]
    assert server.active is None


def test_tracker_keeps_identity_through_pan_and_missing_detection():
    import numpy as np

    from app.vision.tracking import MotionTracker

    tracker = MotionTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    pan = np.array([[1.0, 0.0, 70.0], [0.0, 1.0, 0.0]])

    def person(x):
        return {"box": [x, 10, x + 15, 60], "team": 0, "confidence": 0.9}

    first = tracker.update([person(10)], 0, identity)[0]["id"]
    assert tracker.update([person(80)], 0.2, pan)[0]["id"] == first
    assert tracker.update([], 0.4, identity) == []
    assert tracker.update([person(80)], 0.6, identity)[0]["id"] == first
    assert tracker.update([person(80)], 2, identity)[0]["id"] != first


def test_tracker_keeps_opposing_kits_and_resets_on_cuts():
    import numpy as np

    from app.vision.tracking import MotionTracker

    tracker = MotionTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    people = [{"box": [x, 10, x + 15, 60], "team": team} for x, team in [(10, 0), (30, 1)]]
    first = tracker.update(people, 0, identity)
    crossed = [{**p, "box": people[1 - i]["box"]} for i, p in enumerate(people)]
    second = tracker.update(crossed, 0.2, identity)
    assert [p["id"] for p in second] == [p["id"] for p in first]
    cut = tracker.update(crossed, 0.4, identity, cut=True)
    assert not set(p["id"] for p in cut) & set(p["id"] for p in first)


def test_camera_motion_estimates_background_translation():
    import cv2
    import numpy as np

    from app.vision.tracking import camera_motion

    previous = np.random.default_rng(3).integers(0, 256, (180, 320, 3), dtype=np.uint8)
    current = cv2.warpAffine(previous, np.float32([[1, 0, 9], [0, 1, 3]]), (320, 180))
    matrix, valid = camera_motion(previous, current, [])
    assert valid
    assert abs(matrix[0, 2] - 9) < 1
    assert abs(matrix[1, 2] - 3) < 1


def test_ball_decoder_unpads_coordinates_and_suppresses_duplicates():
    from types import SimpleNamespace

    from app.vision.ball import BallDetector

    class Session:
        def run(self, outputs, feed):
            tensor = feed["images"]
            assert tensor.shape == (1, 3, 1280, 1280)
            assert tensor.dtype == np.float32
            # Coordinates in the padded model canvas, including an off-image candidate.
            return [
                np.array(
                    [[[640, 641, 20], [640, 640, 20], [20, 20, 10], [20, 20, 10], [0.8, 0.7, 0.9]]],
                    dtype=np.float32,
                )
            ]

    detector = BallDetector.__new__(BallDetector)
    detector.session = Session()
    detector.input = SimpleNamespace(name="images")
    detector.width = detector.height = 1280
    found = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
    assert len(found) == 1
    assert found[0]["x"] == 320 and found[0]["y"] == 240
    assert found[0]["box"] == [315, 235, 325, 245]


def test_shutdown_requests_cancellation_of_active_inference(service):
    server, _ = service
    event = threading.Event()
    server.cancellations["a" * 32] = event
    server.stop_jobs()
    assert event.is_set()


def test_missing_ball_weights_are_reported_before_upload(service, monkeypatch, tmp_path):
    server, client = service
    model = tmp_path / "player-model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(tmp_path / "missing"))
    health = client.get("/health").json()
    # Player load still runs; no profile is offered for a ball search.
    assert health["available"] is True and "general" not in health["profiles"]
    response = client.post("/jobs", content=b"data", headers={"Content-Type": "video/mp4"})
    assert response.status_code == 503
    assert server.active is None
    monkeypatch.setenv("VISION_MODEL_PATH", str(tmp_path / "missing-too"))
    assert client.get("/health").json()["available"] is False


def test_weak_detections_sustain_but_cannot_create_tracks():
    from app.vision.tracking import MotionTracker

    tracker = MotionTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    weak = {"box": [10, 10, 30, 60], "team": 0, "confidence": 0.2}
    assert tracker.update([weak], 0, identity) == []
    known = tracker.update([{**weak, "confidence": 0.8}], 0.2, identity)[0]["id"]
    assert tracker.update([weak], 0.4, identity)[0]["id"] == known


def test_ball_tracker_uses_motion_to_reject_a_distant_distractor():
    from app.vision.ball_tracking import BallTracker

    tracker = BallTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

    def ball(x, confidence=0.8):
        return {"x": x, "y": 100, "confidence": confidence, "box": [x - 3, 97, x + 3, 103]}

    first = tracker.update([ball(50)], 0, identity, (360, 640))
    for t, x in [(0.1, 55), (0.2, 60), (0.3, 65)]:
        assert tracker.update([ball(x)], t, identity, (360, 640))["trackId"] == first["trackId"]
    found = tracker.update([ball(70, 0.7), ball(550, 0.85)], 0.4, identity, (360, 640))
    assert found["x"] == 70
    assert found["observed"] is True


def test_ball_tracker_does_not_fill_gaps_and_resets_after_a_cut():
    from app.vision.ball_tracking import BallTracker

    tracker = BallTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    ball = {"x": 50, "y": 100, "confidence": 0.8}
    first = tracker.update([ball], 0, identity, (360, 640))
    assert tracker.update([], 0.1, identity, (360, 640)) is None
    assert tracker.update([ball], 0.2, identity, (360, 640))["trackId"] == first["trackId"]
    assert (
        tracker.update([ball], 0.3, identity, (360, 640), cut=True)["trackId"] != first["trackId"]
    )


def test_ball_tracker_compensates_camera_pan_and_confirms_weak_candidates():
    from app.vision.ball_tracking import BallTracker

    tracker = BallTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    pan = np.array([[1.0, 0.0, 100.0], [0.0, 1.0, 0.0]])
    assert tracker.update([{"x": 10, "y": 20, "confidence": 0.3}], 0, identity, (360, 640)) is None
    assert (
        tracker.update([{"x": 110, "y": 20, "confidence": 0.3}], 0.1, pan, (360, 640))["x"] == 110
    )
    assert (
        tracker.update([{"x": 510, "y": 20, "confidence": 0.3}], 0.2, identity, (360, 640)) is None
    )


def test_ball_identity_switch_is_not_a_transfer():
    frames = [frame(0), frame(0.2), frame(0.4, [player(2, 1)]), frame(0.6, [player(2, 1)])]
    for i, f in enumerate(frames):
        f["ball"]["trackId"] = 1 if i < 2 else 2
    assert derive_metrics(frames, 5, 0.8)["events"] == []


def test_white_and_dark_jerseys_are_not_discarded():
    from app.vision.engine import jersey

    for value in (25, 230):
        image = np.full((100, 100, 3), value, dtype=np.uint8)
        assert jersey(image, [10, 10, 50, 90]) is not None


def test_invalid_analysis_options_are_rejected_before_upload(service, monkeypatch, tmp_path):
    _, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    for query in ("profile=unknown", "fps=nan", "fps=100"):
        response = client.post(
            f"/jobs?{query}", content=b"x", headers={"Content-Type": "video/mp4"}
        )
        assert response.status_code == 400


def test_tiled_ball_inference_merges_overlap_and_preserves_source_coordinates():
    from types import SimpleNamespace

    from app.vision.ball import TiledBallDetector

    class Model:
        def predict(self, images, **kwargs):
            out = []
            for image in images:
                ys, xs = np.where(image[:, :, 0] > 0)
                if len(xs):
                    boxes = np.array([[xs.min(), ys.min(), xs.max(), ys.max()]])
                    conf = np.array([0.8])
                else:
                    boxes, conf = np.zeros((0, 4)), np.zeros(0)
                out.append(SimpleNamespace(boxes=SimpleNamespace(xyxy=boxes, conf=conf)))
            return out

    detector = TiledBallDetector.__new__(TiledBallDetector)
    detector.model, detector.classes, detector.device = Model(), [0], "cpu"
    image = np.zeros((360, 640, 3), np.uint8)
    image[176:185, 316:325] = 255
    found = detector.detect(image)
    assert len(found) == 1
    assert (found[0]["x"], found[0]["y"]) == (320, 180)


def test_role_aware_kit_fit_keeps_light_and_dark_parts_of_a_striped_team():
    from app.vision.engine import train_colours

    # Representative LAB observations: one white kit and several shades of a striped kit.
    features = [[245.0, 126.0, 133.0]] * 22 + [[134.0, 119.0, 137.0]] * 12
    features += [[181.0, 122.0, 132.0]] * 6 + [[105.0, 124.0, 124.0]] * 10
    centres, _ = train_colours(features, n_clusters=2)
    dark = assign_team(np.array([105.0, 124.0, 124.0]), centres, use_hue=False)
    medium = assign_team(np.array([134.0, 119.0, 137.0]), centres, use_hue=False)
    white = assign_team(np.array([245.0, 126.0, 133.0]), centres, use_hue=False)
    assert dark == medium and dark >= 0
    assert white >= 0 and white != dark


def test_model_paths_do_not_depend_on_shell_directory(monkeypatch, tmp_path):
    from app.vision.profiles import model_paths

    monkeypatch.delenv("VISION_MODEL_PATH", raising=False)
    monkeypatch.delenv("VISION_BALL_MODEL_PATH", raising=False)
    before = model_paths()
    monkeypatch.chdir(tmp_path)
    assert model_paths() == before
    assert all(p.is_absolute() for p in before)


# ── Hosted worker: chunked uploads, ownership, retention ──────────────────
FIXTURE = (
    __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend/tests/fixtures/review.mp4"
)


@pytest.fixture
def hosted(service, monkeypatch, tmp_path):
    server, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    monkeypatch.setattr(server, "upload_activity", {})
    monkeypatch.setattr(server, "upload_started", {})
    monkeypatch.setattr(server, "upload_sizes", {})
    submitted = []
    monkeypatch.setattr(server.pool, "submit", lambda *args: submitted.append(args))
    return server, client, submitted


def test_healthz_is_public_and_reveals_nothing(service):
    server, client = service
    response = client.get("/healthz", headers={"Authorization": ""})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert client.get("/health", headers={"Authorization": ""}).status_code == 401


def test_chunked_upload_resumes_starts_and_scopes_listing_to_owner(hosted):
    server, client, submitted = hosted
    data = FIXTURE.read_bytes()
    owner = "c" * 32
    created = client.post(
        f"/jobs?size={len(data)}&owner={owner}&title=Chunked",
        headers={"Content-Type": "video/mp4"},
    )
    assert created.status_code == 200, created.text
    job = created.json()
    assert job["status"] == "uploading" and "owner" not in job
    # Worker is reserved while the browser sends chunks.
    busy = client.post(f"/jobs?size=10&owner={owner}", headers={"Content-Type": "video/mp4"})
    assert busy.status_code == 409
    client.headers["x-pitchlens-owner"] = owner
    # Starting before every byte arrives is rejected.
    assert client.post(f"/jobs/{job['id']}/start").status_code == 409
    step = 100_000
    for offset in range(0, len(data), step):
        r = client.put(
            f"/jobs/{job['id']}/video?offset={offset}", content=data[offset : offset + step]
        )
        assert r.status_code == 200 and r.json()["received"] == min(len(data), offset + step)
    # A retried chunk is acknowledged without being written twice.
    assert client.put(f"/jobs/{job['id']}/video?offset=0", content=data[:step]).json() == {
        "received": len(data)
    }
    # Partial video is never served during upload.
    assert client.get(f"/jobs/{job['id']}/video").status_code == 404
    started = client.post(f"/jobs/{job['id']}/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "processing" and started.json()["fileSize"] == len(data)
    assert len(submitted) == 1
    assert (server.ROOT / job["id"] / "video").read_bytes() == data
    assert [j["id"] for j in client.get(f"/jobs?owner={owner}").json()] == [job["id"]]
    del client.headers["x-pitchlens-owner"]
    assert client.get(f"/jobs?owner={'d' * 32}").json() == []
    assert client.get("/jobs?owner=../../etc").status_code == 400


def test_out_of_order_chunk_is_rejected_with_resync_offset(hosted):
    server, client, _ = hosted
    job = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).json()
    r = client.put(f"/jobs/{job['id']}/video?offset=4", content=b"4567")
    assert r.status_code == 409 and "Expected offset 0" in r.json()["detail"]
    too_big = client.put(f"/jobs/{job['id']}/video?offset=0", content=b"0123456789ABC")
    assert too_big.status_code == 413


def test_cancelled_and_abandoned_uploads_release_the_worker(hosted, monkeypatch):
    server, client, _ = hosted
    job = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).json()
    assert client.post(f"/jobs/{job['id']}/cancel").json() == {"requested": True}
    assert server.active is None
    assert not (server.ROOT / job["id"] / "video").exists()
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "failed"

    abandoned = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).json()
    monkeypatch.setattr(server, "UPLOAD_IDLE_SECONDS", 0)
    replacement = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"})
    assert replacement.status_code == 200
    assert client.get(f"/jobs/{abandoned['id']}").json()["stage"].startswith("Upload stopped")


def test_corrupt_chunked_upload_fails_cleanly(hosted):
    server, client, submitted = hosted
    job = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).json()
    client.put(f"/jobs/{job['id']}/video?offset=0", content=b"not a vid!")
    response = client.post(f"/jobs/{job['id']}/start")
    assert response.status_code == 400 and "decoded" in response.json()["detail"]
    assert server.active is None and not submitted


def test_retention_deletes_old_footage_but_keeps_results(service, monkeypatch):
    server, client = service
    monkeypatch.setattr(server, "RETENTION_HOURS", 1)
    old, new = "e" * 32, "f" * 32
    for job, created in ((old, 0), (new, __import__("time").time())):
        directory = server.ROOT / job
        directory.mkdir()
        (directory / "video").write_bytes(b"video")
        (directory / "result.json").write_text("{}")
        server.write_status(directory, {"id": job, "status": "completed", "createdAt": created})
    server.delete_expired()
    assert not (server.ROOT / old / "video").exists()
    assert (server.ROOT / old / "result.json").exists()
    assert client.get(f"/jobs/{old}").json()["videoDeleted"] is True
    assert (server.ROOT / new / "video").exists()


def test_open_ended_playback_range_is_capped(service, monkeypatch):
    server, client = service
    monkeypatch.setattr(server, "RANGE_CAP", 4)
    job = "a" * 32
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "video").write_bytes(b"0123456789")
    server.write_status(directory, {"id": job, "status": "completed", "createdAt": 0})
    r = client.get(f"/jobs/{job}/video", headers={"Range": "bytes=2-"})
    assert r.status_code == 206 and r.content == b"2345"
    assert r.headers["Content-Range"] == "bytes 2-5/10"


def test_allowed_hosts_default_to_loopback_and_are_configurable(monkeypatch):
    from app.vision import server

    monkeypatch.delenv("VISION_ALLOWED_HOSTS", raising=False)
    assert server.allowed_hosts() == ["127.0.0.1", "localhost", "testserver"]
    monkeypatch.setenv("VISION_ALLOWED_HOSTS", "pitchlens.up.railway.app, healthcheck.railway.app")
    assert server.allowed_hosts() == ["pitchlens.up.railway.app", "healthcheck.railway.app"]


def test_restart_marks_in_flight_work_interrupted_and_drops_partial_uploads(service):
    server, client = service
    uploading, processing = "1" * 32, "2" * 32
    for job, state in ((uploading, "uploading"), (processing, "processing")):
        directory = server.ROOT / job
        directory.mkdir()
        (directory / "video").write_bytes(b"partial")
        server.write_status(directory, {"id": job, "status": state, "createdAt": 0})
    server.recover_after_restart()
    assert client.get(f"/jobs/{uploading}").json()["status"] == "interrupted"
    assert not (server.ROOT / uploading / "video").exists()
    assert client.get(f"/jobs/{processing}").json()["status"] == "interrupted"
    assert (server.ROOT / processing / "video").exists()


def test_shutdown_during_analysis_is_interrupted_not_user_cancelled(service, monkeypatch):
    server, _ = service
    job = "3" * 32
    directory = server.ROOT / job
    directory.mkdir()
    status = {"id": job, "status": "processing", "createdAt": 0}

    def run_video(*args, cancelled, **kwargs):
        server.stop_jobs()
        assert cancelled()
        raise InterruptedError("Analysis cancelled")

    monkeypatch.setattr(server, "run_video", run_video)
    monkeypatch.setattr(server, "stopping", False)
    monkeypatch.setenv("VISION_SHUTDOWN_WAIT_SECONDS", "0")  # stop_jobs runs on the job's own thread here
    event = threading.Event()
    server.cancellations[job] = event
    server.work(directory, status, event)
    assert status["status"] == "interrupted"
    # Persisted, so the retry after the redeploy is not charged to the user.
    import json

    assert json.loads((directory / "status.json").read_text())["interruptedBy"] == "shutdown"


def _stopped_job(server, job, attempts, **extra):
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "video").write_bytes(b"saved video")
    server.write_status(directory, {
        "id": job, "status": "interrupted", "createdAt": __import__("time").time(), "profile": "general",
        "video": {"duration": 60}, "attempts": attempts, **extra,
    })
    return directory


def test_a_redeploy_does_not_use_up_the_retry_budget(hosted):
    import json

    server, client, submitted = hosted
    job = "4" * 32
    directory = _stopped_job(server, job, 3, interruptedBy="shutdown")
    response = client.post(f"/jobs/{job}/retry")
    assert response.status_code == 200, response.text
    saved = json.loads((directory / "status.json").read_text())
    assert saved["attempts"] == 3 and "interruptedBy" not in saved and len(submitted) == 1


def test_crash_recoveries_still_count_and_the_refusal_offers_an_action(hosted):
    server, client, submitted = hosted
    job = "5" * 32
    directory = _stopped_job(server, job, 3)
    server.write_status(directory, {**__import__("json").loads((directory / "status.json").read_text()), "status": "processing"})
    server.recover_after_restart()
    response = client.post(f"/jobs/{job}/retry")
    assert response.status_code == 409 and not submitted
    assert "support" not in response.json()["detail"] and "Upload it again" in response.json()["detail"]


def test_identical_kit_colours_fail_with_a_clear_message():
    from app.vision.engine import train_colours

    with pytest.raises(ValueError, match="kits could not be separated"):
        train_colours([[50.0, 10.0, 10.0]] * 20, n_clusters=2)


def test_trickling_upload_cannot_hold_the_worker_past_the_deadline(hosted, monkeypatch):
    server, client, _ = hosted
    job = client.post("/jobs?size=100", headers={"Content-Type": "video/mp4"}).json()
    # Still sending data, so never idle — but past the absolute deadline.
    client.put(f"/jobs/{job['id']}/video?offset=0", content=b"x")
    monkeypatch.setattr(server, "UPLOAD_MAX_SECONDS", 0)
    replacement = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"})
    assert replacement.status_code == 200
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "failed"


def test_upload_time_limit_applies_only_when_another_start_waits_and_says_so(hosted, monkeypatch):
    server, client, _ = hosted
    monkeypatch.setattr(server, "UPLOAD_MAX_SECONDS", 0)
    monkeypatch.setattr(server, "UPLOAD_IDLE_SECONDS", 10_000)
    mine, other = "c" * 32, "d" * 32
    job = client.post(f"/jobs?size=8&owner={mine}", headers={"Content-Type": "video/mp4"}).json()
    headers = {"x-pitchlens-owner": mine}
    assert client.put(f"/jobs/{job['id']}/video?offset=0", content=b"0123", headers=headers).status_code == 200
    # Polling (even this user's own dashboard) is not contention: a lone slow upload continues.
    client.get(f"/jobs?owner={mine}")
    client.get("/health")
    client.get(f"/jobs/{job['id']}", headers=headers)
    assert client.put(f"/jobs/{job['id']}/video?offset=4", content=b"45", headers=headers).status_code == 200
    # Someone else wants the worker: the overdue upload gives way, and its uploader learns why.
    assert client.post(f"/jobs?size=10&owner={other}", headers={"Content-Type": "video/mp4"}).status_code == 200
    late = client.put(f"/jobs/{job['id']}/video?offset=6", content=b"67", headers=headers)
    assert late.status_code == 409
    assert "within 1 minute" in late.json()["detail"] and "75%" in late.json()["detail"]
    assert client.get(f"/jobs/{job['id']}", headers=headers).json()["stage"] == "Upload took longer than the 1-minute limit."


def test_abandoned_upload_is_still_released_by_any_poll_with_the_idle_message(hosted, monkeypatch):
    server, client, _ = hosted
    job = client.post("/jobs?size=8", headers={"Content-Type": "video/mp4"}).json()
    # Past the abandon limit (2 h by default) a poll frees the worker; the 5-minute
    # idle limit applies only when another owner's start wants it.
    monkeypatch.setattr(server, "UPLOAD_ABANDON_SECONDS", 0)
    client.get("/jobs")
    assert server.active is None
    late = client.put(f"/jobs/{job['id']}/video?offset=0", content=b"0123")
    assert late.status_code == 409 and late.json()["detail"] == "This upload is no longer accepting data. Start again."


def test_late_chunk_after_cancel_does_not_resurrect_the_upload(hosted, monkeypatch):
    server, client, _ = hosted
    job = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).json()
    original = server.receiving
    calls = {"n": 0}

    def cancel_while_body_arrives(job_id):
        calls["n"] += 1
        if calls["n"] == 1:
            result = original(job_id)
            # The user cancels while this chunk's body is still streaming.
            client.post(f"/jobs/{job_id}/cancel")
            return result
        return original(job_id)

    monkeypatch.setattr(server, "receiving", cancel_while_body_arrives)
    late = client.put(f"/jobs/{job['id']}/video?offset=0", content=b"0123")
    assert late.status_code == 409
    assert not (server.ROOT / job["id"] / "video").exists()
    assert client.get(f"/jobs/{job['id']}").json()["stage"] == "Upload cancelled"


def test_duplicate_retry_of_the_same_chunk_is_written_once(hosted):
    server, client, _ = hosted
    job = client.post("/jobs?size=8", headers={"Content-Type": "video/mp4"}).json()
    for _ in range(3):
        assert client.put(f"/jobs/{job['id']}/video?offset=0", content=b"0123").json() == {
            "received": 4
        }
    assert (server.ROOT / job["id"] / "video").read_bytes() == b"0123"


def test_cancel_during_start_probe_never_queues_work(hosted, monkeypatch):
    server, client, submitted = hosted
    data = FIXTURE.read_bytes()
    job = client.post(f"/jobs?size={len(data)}", headers={"Content-Type": "video/mp4"}).json()
    client.put(f"/jobs/{job['id']}/video?offset=0", content=data)
    real_probe = server.probe

    def probe_then_cancel(path):
        meta = real_probe(path)
        client.post(f"/jobs/{job['id']}/cancel")
        return meta

    monkeypatch.setattr(server, "probe", probe_then_cancel)
    assert client.post(f"/jobs/{job['id']}/start").status_code == 409
    assert not submitted and server.active is None


def test_abandoned_upload_is_released_when_anyone_reads_status(hosted, monkeypatch):
    server, client, _ = hosted
    job = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).json()
    monkeypatch.setattr(server, "UPLOAD_ABANDON_SECONDS", 0)
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "failed"
    assert server.active is None


def test_expired_footage_is_not_served_even_before_the_sweep(service, monkeypatch):
    server, client = service
    monkeypatch.setattr(server, "RETENTION_HOURS", 1)
    job = "9" * 32
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "video").write_bytes(b"video")
    server.write_status(directory, {"id": job, "status": "completed", "createdAt": 0})
    assert client.get(f"/jobs/{job}/video").status_code == 404
    assert not (directory / "video").exists()
    assert client.get(f"/jobs/{job}").json()["videoDeleted"] is True


def _finished_job(server, job, created, frames=True):
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "video").write_bytes(b"video")
    (directory / "result.json").write_text("{}")
    if frames:
        (directory / "labelframes").mkdir()
        (directory / "labelframes" / "0.jpg").write_bytes(b"jpeg copy of the footage")
    server.write_status(directory, {"id": job, "status": "completed", "createdAt": created})
    return directory


def test_retention_sweep_also_deletes_label_frames(service, monkeypatch):
    import json

    server, client = service
    monkeypatch.setattr(server, "RETENTION_HOURS", 1)
    job = "6" * 32
    directory = _finished_job(server, job, 0)
    server.delete_expired()
    assert not (directory / "labelframes").exists() and not (directory / "video").exists()
    assert json.loads((directory / "status.json").read_text())["videoDeleted"] is True
    assert client.get(f"/jobs/{job}/frames/0").status_code == 404


def test_a_cached_label_frame_is_not_served_without_the_footage(service, monkeypatch):
    server, client = service
    monkeypatch.setattr(server, "RETENTION_HOURS", 0)
    job = "7" * 32
    directory = _finished_job(server, job, 0)
    assert client.get(f"/jobs/{job}/frames/0").status_code == 200
    (directory / "video").unlink()
    assert client.get(f"/jobs/{job}/frames/0").status_code == 404


def test_reading_an_expired_job_applies_retention_before_any_video_request(service, monkeypatch):
    server, client = service
    monkeypatch.setattr(server, "RETENTION_HOURS", 1)
    listed, read = "8" * 32, "9" * 32
    for job in (listed, read):
        _finished_job(server, job, 0)
    assert client.get(f"/jobs/{read}").json()["videoDeleted"] is True
    assert not (server.ROOT / read / "video").exists() and not (server.ROOT / read / "labelframes").exists()
    assert {j["id"]: j.get("videoDeleted") for j in client.get("/jobs").json()}[listed] is True
    assert not (server.ROOT / listed / "video").exists()


def _rerun_chain_job(server, job, created):
    source = server.ROOT / job
    source.mkdir()
    (source / "video").write_bytes(b"complete saved video")
    server.write_status(source, {"id": job, "owner": "c" * 32, "title": "Saved match", "status": "completed",
        "createdAt": created, "profile": "general", "sampleFps": 6, "video": {"duration": 600}})


def _rerun(server, client, job, mode, n):
    import json

    response = client.post(f"/jobs/{job}/rerun?mode={mode}&start=0&requestId={n:032x}",
                           headers={"x-pitchlens-owner": "c" * 32})
    if response.status_code == 200:
        # Let the next one start: these tests do not run the analysis.
        server.active = None
        server.cancellations.clear()
        done = server.ROOT / response.json()["id"]
        server.write_status(done, {**json.loads((done / "status.json").read_text()), "status": "completed"})
    return response


def test_a_rerun_keeps_the_original_footage_retention_clock(hosted, monkeypatch):
    import json
    import time as clock

    server, client, _ = hosted
    monkeypatch.setattr(server, "RETENTION_HOURS", 1)
    job, uploaded = "a" * 32, clock.time() - 3000
    _rerun_chain_job(server, job, uploaded)
    first = _rerun(server, client, job, "section", 1).json()["id"]
    second = _rerun(server, client, first, "section", 2).json()["id"]
    saved = json.loads((server.ROOT / second / "status.json").read_text())
    assert saved["footageCreatedAt"] == uploaded and saved["rootJobId"] == job
    assert saved["createdAt"] > uploaded
    # Fifty minutes after the upload, a 30-minute retention has expired every copy.
    monkeypatch.setattr(server, "RETENTION_HOURS", 0.5)
    assert server.expired(saved)
    server.delete_expired()
    assert not any((server.ROOT / j / "video").exists() for j in (job, first, second))


def test_rerun_limits_count_the_whole_chain_from_the_upload(hosted, monkeypatch):
    server, client, _ = hosted
    monkeypatch.setattr(server, "SECTION_RERUNS_PER_MATCH", 3)
    job = "a" * 32
    _rerun_chain_job(server, job, 0)
    first = _rerun(server, client, job, "section", 1).json()["id"]
    second = _rerun(server, client, first, "section", 2).json()["id"]
    third = _rerun(server, client, second, "section", 3).json()["id"]
    assert [_rerun(server, client, j, "section", 4 + i).status_code for i, j in enumerate((job, first, second, third))] == [409] * 4
    # A lost reply is still answered from its own source job.
    assert _rerun(server, client, first, "section", 2).json()["id"] == second


# ── YouTube links ─────────────────────────────────────────────────────────
def test_youtube_links_are_reduced_to_a_single_video_id():
    from app.vision.youtube import video_id

    for url in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=30",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
    ):
        assert video_id(url) == "dQw4w9WgXcQ"
    for bad in (
        "https://evil.example/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL123",
        "file:///etc/passwd",
        "",
    ):
        with pytest.raises(ValueError):
            video_id(bad)


def test_youtube_job_downloads_then_analyses(hosted, monkeypatch):
    server, client, submitted = hosted
    response = client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ&owner=" + "c" * 32)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "uploading" and job["title"] == "YouTube match"
    assert client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).status_code == 409
    fn, directory, status, event, url = submitted[0]
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def fake_download(url, directory, max_bytes, progress, cancelled):
        progress(stage="Downloading from YouTube", progress=50.0)
        target = directory / "download.mp4"
        target.write_bytes(FIXTURE.read_bytes())
        return target, {"title": "Sunday league final", "duration": 4}

    analysed = []
    monkeypatch.setattr("app.vision.youtube.download", fake_download)
    monkeypatch.setattr(server, "work", lambda d, s, e: analysed.append(s["title"]))
    fn(directory, status, event, url)
    assert analysed == ["Sunday league final"]
    assert (directory / "video").read_bytes() == FIXTURE.read_bytes()
    assert status["video"]["duration"] > 0


def test_youtube_block_fails_clearly_and_frees_the_worker(hosted, monkeypatch):
    server, client, submitted = hosted
    client.post("/jobs/from-url?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    fn, directory, status, event, url = submitted[0]

    def blocked(*args, **kwargs):
        from app.vision.youtube import friendly

        raise ValueError(friendly("Sign in to confirm you’re not a bot"))

    monkeypatch.setattr("app.vision.youtube.download", blocked)
    fn(directory, status, event, url)
    assert status["status"] == "failed" and "Upload a file" in status["stage"]
    assert server.active is None
    # A blocked download used no GPU: it does not use up the daily allowance.
    assert not (server.ROOT / "starts.json").exists()
    assert client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).status_code == 200


def test_youtube_analyses_count_toward_the_daily_cap(hosted, monkeypatch):
    import json

    server, client, submitted = hosted
    monkeypatch.setenv("VISION_MAX_JOBS_PER_DAY", "2")

    def fake_download(url, directory, max_bytes, progress, cancelled):
        target = directory / "download.mp4"
        target.write_bytes(FIXTURE.read_bytes())
        return target, {"title": "Match", "duration": 4}

    ran = []
    monkeypatch.setattr("app.vision.youtube.download", fake_download)
    monkeypatch.setattr(server, "work", lambda d, s, e: ran.append(s["id"]))
    codes = []
    for _ in range(3):
        response = client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ")
        codes.append(response.status_code)
        if response.status_code == 200:
            fn, directory, status, event, url = submitted[-1]
            fn(directory, status, event, url)  # what the inference thread does
            server.active = None  # the analysis finished
            server.cancellations.clear()
    assert codes == [200, 200, 429]
    assert len(ran) == 2
    assert len(json.loads((server.ROOT / "starts.json").read_text())) == 2


def test_invalid_youtube_link_is_rejected_before_reserving(hosted):
    server, client, submitted = hosted
    r = client.post("/jobs/from-url?url=https://example.com/video.mp4")
    assert r.status_code == 400 and not submitted and server.active is None


# ── GPU (Modal) dispatch ──────────────────────────────────────────────────
def test_gpu_result_is_used_when_modal_is_configured(service, monkeypatch):
    server, _ = service
    job = "4" * 32
    directory = server.ROOT / job
    directory.mkdir()
    status = {"id": job, "status": "processing", "createdAt": 0, "profile": "general"}

    def fake_modal(job_id, output, token, progress, **kw):
        progress(stage="GPU · Detecting players", progress=50)
        output.write_text("{}")

    cpu = []
    monkeypatch.setattr(server.gpu, "modal_enabled", lambda: True)
    monkeypatch.setattr(server.gpu, "run_on_modal", fake_modal)
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    event = threading.Event()
    server.work(directory, status, event)
    assert status["status"] == "completed" and status["engine"] == "gpu" and not cpu


def test_cpu_takes_over_when_the_gpu_is_unavailable(service, monkeypatch):
    server, _ = service
    job = "5" * 32
    directory = server.ROOT / job
    directory.mkdir()
    status = {"id": job, "status": "processing", "createdAt": 0}

    def broken(*a, **k):
        raise server.gpu.GPUUnavailable("bad token")

    cpu = []
    monkeypatch.setattr(server.gpu, "modal_enabled", lambda: True)
    monkeypatch.setattr(server.gpu, "run_on_modal", broken)
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    server.work(directory, status, threading.Event())
    assert cpu == [1] and status["engine"] == "cpu" and status["status"] == "completed"


def test_modal_needs_both_tokens_and_can_be_switched_off(monkeypatch):
    from app.vision import gpu

    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-x")
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    assert not gpu.modal_enabled()
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-y")
    assert gpu.modal_enabled()
    monkeypatch.setenv("VISION_USE_MODAL", "0")
    assert not gpu.modal_enabled()


# ── Possession chains (StatsBomb-style sequences) ─────────────────────────
def test_possession_chain_survives_a_short_unseen_gap_but_not_a_turnover():
    a, b = player(1, 0), player(2, 1, x=200)
    frames = (
        [frame(t / 5, [a]) for t in range(0, 3)]  # team 0 control 0.0-0.4
        + [frame(t / 5, [a], ball=False) for t in range(3, 8)]  # 1 s unseen
        + [frame(t / 5, [a]) for t in range(8, 11)]  # team 0 again: same possession
        + [frame(t / 5, [b]) for t in range(11, 14)]  # team 1 takes over
    )
    chains = derive_metrics(frames, 5, 3)["possessions"]
    assert [c["team"] for c in chains["chains"]] == [0, 1]
    assert chains["teams"][0]["count"] == 1 and chains["teams"][1]["count"] == 1
    assert chains["teams"][0]["longestSeconds"] >= 2


def test_long_gap_or_scene_cut_starts_a_new_possession():
    a = player(1, 0)
    for gap_frames in (
        [frame(t / 5, [a], ball=False) for t in range(3, 25)],  # 4.4 s unseen
        [frame(0.6, [a], scene=1)],
    ):
        frames = [frame(t / 5, [a]) for t in range(0, 3)] + gap_frames
        frames += [frame(5 + t / 5, [a], scene=gap_frames[-1]["scene"]) for t in range(0, 3)]
        assert derive_metrics(frames, 5, 6)["possessions"]["teams"][0]["count"] == 2


def test_pressing_metric_is_withheld_without_regains():
    out = derive_metrics([frame(t / 5) for t in range(5)], 5, 1)["possessions"]["teams"]
    assert out[0]["passesAllowedPerRegain"] is None
    assert out[1]["count"] == 0 and out[1]["averageSeconds"] is None


def test_gpu_can_copy_installed_weights_but_nothing_else(service, monkeypatch, tmp_path):
    server, client = service
    weights = tmp_path / "football-ball.onnx"
    weights.write_bytes(b"weights")
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(weights))
    assert client.get("/models/football-ball.onnx").content == b"weights"
    assert (
        client.get("/models/football-ball.onnx", headers={"Authorization": ""}).status_code == 401
    )
    assert client.get("/models/status.json").status_code == 404
    assert client.get("/models/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_track_vote_fixes_unknown_and_noisy_team_labels():
    from app.vision.engine import vote_teams

    def obs(team, role="player"):
        return {"id": 7, "team": team, "role": role, "box": [0, 0, 10, 20], "confidence": 0.9}

    frames = [{"players": [obs(t)]} for t in (0, 0, 0, -1, 1)]
    vote_teams(frames)
    assert [f["players"][0]["team"] for f in frames] == [0, 0, 0, 0, 0]
    split = [{"players": [obs(t)]} for t in (0, 1, 0, 1)]
    vote_teams(split)  # no 60% majority: left as observed
    assert [f["players"][0]["team"] for f in split] == [0, 1, 0, 1]


def test_pan_is_not_a_cut_but_a_new_shot_is():
    import cv2
    import numpy as np

    from app.vision.engine import colour_signature

    rng = np.random.default_rng(1)
    pitch = np.zeros((360, 640, 3), np.uint8)
    pitch[:] = (40, 140, 60)
    for _ in range(12):  # players
        x, y = rng.integers(20, 600), rng.integers(40, 300)
        pitch[y : y + 40, x : x + 15] = (40, 40, 200)
    panned = np.roll(pitch, 120, axis=1)
    crowd = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    compare = lambda a, b: cv2.compareHist(
        colour_signature(a), colour_signature(b), cv2.HISTCMP_BHATTACHARYYA
    )
    assert compare(pitch, panned) < 0.2
    assert compare(pitch, crowd) > 0.5


# ── Faint-object ball recovery (track-before-detect) ─────────────────────
def test_weak_candidates_on_a_smooth_path_are_confirmed_but_blips_are_not():
    from app.vision.faint import confirm_chains

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = []
    for i in range(8):
        cands = [{"x": 100 + 12 * i, "y": 200 - 3 * i, "box": None, "confidence": 0.12}]
        if i == 4:
            cands.append({"x": 500, "y": 50, "box": None, "confidence": 0.14})  # isolated blip
        frames.append({"scene": 0, "ball": None, "ballCandidates": cands})
    promoted = confirm_chains(frames, [identity] * 8, diagonal=734)
    assert sorted(promoted) == list(range(8))
    assert all(promoted[i]["x"] == 100 + 12 * i for i in range(8))
    assert promoted[4]["y"] != 50


def test_chain_never_crosses_a_scene_cut():
    from app.vision.faint import confirm_chains

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = [
        {
            "scene": 0 if i < 4 else 1,
            "ball": None,
            "ballCandidates": [{"x": 100 + 10 * i, "y": 100, "box": None, "confidence": 0.1}],
        }
        for i in range(6)
    ]
    promoted = confirm_chains(frames, [identity] * 6, diagonal=734, min_length=3, min_evidence=0.25)
    assert set(promoted) == {0, 1, 2, 3}


def test_recovery_bridges_short_gaps_as_inferred_not_observed():
    from app.vision.faint import recover_ball
    from app.vision.metrics import derive_metrics

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = []
    for i in range(6):
        ball = {"x": 100 + 20 * i, "y": 100, "box": [0, 0, 4, 4], "confidence": 0.7, "trackId": 1}
        frames.append(
            {
                "t": i * 0.2,
                "scene": 0,
                "players": [],
                "ball": None if i in (2, 3) else ball,
                "ballCandidates": [],
            }
        )
    counts = recover_ball(frames, [identity] * 6, diagonal=734, sample_fps=5, max_bridge=0.5)
    assert counts["inferred"] == 2
    assert frames[2]["ball"]["inferred"] and frames[2]["ball"]["x"] == 140
    metrics = derive_metrics(frames, 5, 1.2)
    assert metrics["ballFrames"] == 4 and metrics["ballFramesInferred"] == 2


def test_difference_candidates_find_a_small_moving_blob_and_ignore_players():
    from app.vision.faint import difference_candidates

    prev = np.full((360, 640, 3), 60, np.uint8)
    cur = prev.copy()
    cur[180:186, 300:306] = 255  # ball-sized moving blob
    cur[50:110, 100:130] = 255  # a player-sized change, inside a player box
    players = [{"box": [100, 50, 130, 110]}]
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    found = difference_candidates(prev, cur, identity, players, diameter=6)
    assert len(found) == 1
    assert abs(found[0]["x"] - 302.5) < 1.5 and found[0]["source"] == "motion"
    assert found[0]["confidence"] <= 0.3


def test_motion_only_chains_never_become_observed_balls_without_neural_evidence():
    from app.vision.faint import confirm_chains

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])

    def run(n, source):
        frames = [
            {
                "scene": 0,
                "ball": None,
                "ballCandidates": [
                    {"x": 100 + 10 * i, "y": 100, "box": None, "confidence": 0.2, "source": source}
                ],
            }
            for i in range(n)
        ]
        return confirm_chains(frames, [identity] * n, diagonal=734)

    assert run(4, "motion") == {}  # four socks in a row are not a ball
    promoted = run(7, "motion")
    assert promoted == {}  # an arbitrarily long sequence of socks is still not a ball


def test_implausibly_fast_jumps_do_not_join_a_chain():
    from app.vision.faint import confirm_chains

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = [
        {
            "scene": 0,
            "ball": None,
            "ballCandidates": [
                {"x": 100 + (300 if i % 2 else 0), "y": 100, "box": None, "confidence": 0.2}
            ],
        }
        for i in range(6)
    ]
    assert confirm_chains(frames, [identity] * 6, diagonal=734) == {}


def test_stationary_ball_survives_an_accelerating_pan():
    """Camera compensation must cancel motion, not double it: an accelerating pan
    used to break every chain that a constant pan happened to keep."""
    from app.vision.faint import confirm_chains

    pans = [0, 0, 0, 150, 200, 200, 200, 200, 200]
    x, frames, matrices = 1400.0, [], []
    for pan in pans:
        x -= pan  # the pitch (and the ball on it) shifts left as the camera pans right
        frames.append(
            {
                "scene": 0,
                "ball": None,
                "ballCandidates": [{"x": x, "y": 500.0, "box": None, "confidence": 0.2}],
            }
        )
        matrices.append(np.array([[1.0, 0, -pan], [0, 1.0, 0]]))
    promoted = confirm_chains(frames, matrices, diagonal=2203)
    assert sorted(promoted) == list(range(len(pans)))


def test_gates_stay_in_current_pixels_after_a_long_zoom():
    """A minute of zooming must not change what 'plausible speed' means after the next cut."""
    from app.vision.faint import confirm_chains

    zoom = np.array([[1.2, 0, 0], [0, 1.2, 0]])
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames, matrices = [], []
    for i in range(10):  # scene 0: static noise candidate while the camera zooms in
        frames.append(
            {
                "scene": 0,
                "ball": None,
                "ballCandidates": [
                    {"x": 5.0 * 1.2**i, "y": 5.0 * 1.2**i, "box": None, "confidence": 0.05}
                ],
            }
        )
        matrices.append(zoom if i else identity)
    for i in range(8):  # scene 1: a clean 30 px/frame ball path
        frames.append(
            {
                "scene": 1,
                "ball": None,
                "ballCandidates": [
                    {"x": 100.0 + 30 * i, "y": 200.0, "box": None, "confidence": 0.2}
                ],
            }
        )
        matrices.append(None if i == 0 else identity)
    promoted = confirm_chains(frames, matrices, diagonal=734)
    assert sorted(promoted) == list(range(10, 18))


def test_a_weaker_parallel_chain_cannot_overwrite_a_stronger_one():
    from app.vision.faint import confirm_chains

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = [
        {
            "scene": 0,
            "ball": None,
            "ballCandidates": [
                {"x": 100.0 + 12 * i, "y": 100.0, "box": None, "confidence": 0.5},
                {
                    "x": 400.0 + 12 * i,
                    "y": 300.0,
                    "box": None,
                    "confidence": 0.12,
                    "source": "motion",
                },
            ],
        }
        for i in range(8)
    ]
    promoted = confirm_chains(frames, [identity] * 8, diagonal=734)
    assert len(promoted) == 8
    assert all(promoted[i]["x"] == 100.0 + 12 * i for i in range(8))
    assert all(promoted[i].get("source") != "motion" for i in range(8))


def test_motion_blobs_never_drive_the_online_tracker():
    from app.vision.faint import strong_candidates

    candidates = [
        {"x": 1, "y": 1, "confidence": 0.4, "source": "motion"},
        {"x": 2, "y": 2, "confidence": 0.2},
        {"x": 3, "y": 3, "confidence": 0.1},
    ]
    assert strong_candidates(candidates) == [{"x": 2, "y": 2, "confidence": 0.2}]


def test_speed_gates_scale_with_sparser_sampling():
    """A 30 px/frame path at 6 fps is a 60 px/frame path at 3 fps; both are the same ball."""
    from app.vision.faint import confirm_chains

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    fast = [
        {
            "scene": 0,
            "ball": None,
            "ballCandidates": [{"x": 50.0 + 130 * i, "y": 100.0, "box": None, "confidence": 0.2}],
        }
        for i in range(5)
    ]
    assert confirm_chains(fast, [identity] * 5, diagonal=734, sample_fps=6) == {}
    assert sorted(confirm_chains(fast, [identity] * 5, diagonal=734, sample_fps=3)) == [
        0,
        1,
        2,
        3,
        4,
    ]


def test_possession_allows_pixel_slack_for_small_players_only():
    from app.vision.metrics import possession_owner

    small = {"id": 1, "team": 0, "box": [100, 100, 110, 123]}  # 23 px: whole pitch at 360p
    large = {"id": 2, "team": 1, "box": [100, 100, 140, 300]}  # 200 px: close-up
    # 20 px from a small player's feet is within detection error of control...
    assert possession_owner([small], {"x": 105, "y": 143}) is small
    # ...but 20 px is well inside 0.55 heights on a large player anyway, and
    # 1.2 heights away on a large player is still a loose ball.
    assert possession_owner([large], {"x": 120, "y": 540}) is None


def test_only_weak_unattended_static_balls_are_dropped_as_markings():
    from app.vision.faint import drop_static_balls

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    weak = {
        "x": 50.0,
        "y": 50.0,
        "box": [48, 48, 52, 52],
        "confidence": 0.3,
        "trackId": -1,
        "recovered": True,
    }
    strong = {**weak, "confidence": 0.5, "trackId": 3, "recovered": False}
    marking = [{"scene": 0, "players": [], "ball": dict(weak)} for _ in range(20)]
    assert drop_static_balls(marking, [identity] * 20, 734, sample_fps=5) == 20
    assert all(f["ball"] is None for f in marking)
    # A confident detection of a dead ball (kick-off, corner) is an observation...
    set_piece = [{"scene": 0, "players": [], "ball": dict(strong)} for _ in range(20)]
    assert drop_static_balls(set_piece, [identity] * 20, 734, sample_fps=5) == 0
    # ...but a confident "ball" nobody touches for half a minute is a marking or a logo.
    logo = [{"scene": 0, "players": [], "ball": dict(strong)} for _ in range(150)]
    assert drop_static_balls(logo, [identity] * 150, 734, sample_fps=5) == 150
    # A weak but attended ball is a player standing over it, not a logo.
    attended = [
        {"scene": 0, "players": [{"team": 0, "box": [40, 10, 60, 52]}], "ball": dict(weak)}
        for _ in range(20)
    ]
    assert drop_static_balls(attended, [identity] * 20, 734, sample_fps=5) == 0
    # Slow drift (2 px/frame) is a rolling ball: measured from the run's origin, not the last frame.
    rolling = [{"scene": 0, "players": [], "ball": {**weak, "x": 50.0 + 2 * i}} for i in range(20)]
    assert drop_static_balls(rolling, [identity] * 20, 734, sample_fps=5) == 0


def test_confident_scoreboard_mark_above_the_players_is_dropped_on_a_short_clip():
    """A 20s test used to keep a 0.75 logo at (77, 52) because it lasted under 20s."""
    from app.vision.faint import drop_static_balls, reject_static_candidates

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = []
    for _ in range(90):
        frames.append({
            "scene": 0,
            "t": 0,
            "players": [{"team": 1, "role": "player", "box": [200, 150, 230, 185]}],
            "ball": {
                "x": 77.0, "y": 52.0, "box": [74, 49, 80, 55],
                "confidence": 0.75, "trackId": 2, "recovered": False,
            },
            "ballCandidates": [{"x": 77.0, "y": 52.0, "confidence": 0.75, "outsidePitch": False}],
        })
    for i, frame in enumerate(frames):
        frame["t"] = i / 5
    assert drop_static_balls([{"scene": f["scene"], "players": f["players"], "ball": dict(f["ball"])} for f in frames], [identity] * 90, 734, sample_fps=5) == 90
    assert reject_static_candidates(frames, [identity] * 90, 734, 5) == 90
    assert frames[10]["ballCandidates"] == []


def test_bridging_uses_the_online_trackers_gate():
    """Two balls the tracker refused to link (too far for the gap) are never joined."""
    from app.vision.faint import recover_ball

    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = []
    for i in range(6):
        ball = {
            "x": 100.0 if i < 2 else 1500.0,
            "y": 100,
            "box": [0, 0, 4, 4],
            "confidence": 0.7,
            "trackId": 1 if i < 2 else 9,
        }
        frames.append(
            {
                "t": i * 0.1,
                "scene": 0,
                "players": [],
                "ball": None if i in (2, 3) else ball,
                "ballCandidates": [],
            }
        )
    counts = recover_ball(frames, [identity] * 6, diagonal=2203, sample_fps=10, max_bridge=0.5)
    assert counts["inferred"] == 0 and frames[2]["ball"] is None


def test_pass_candidates_need_an_observed_ball_throughout_the_transfer():
    from app.vision.metrics import derive_metrics

    a = {"id": 1, "team": 0, "box": [0, 0, 20, 100], "confidence": 0.9}
    b = {"id": 2, "team": 0, "box": [200, 0, 220, 100], "confidence": 0.9}

    def frame(i, ball):
        return {"t": i * 0.2, "scene": 0, "players": [a, b], "ball": ball}

    at = lambda x, **extra: {
        "x": x,
        "y": 100.0,
        "box": [x - 2, 98, x + 2, 102],
        "confidence": 0.7,
        "trackId": 1,
        **extra,
    }
    observed = [
        frame(0, at(10)),
        frame(1, at(10)),
        frame(2, at(105)),
        frame(3, at(210)),
        frame(4, at(210)),
    ]
    bridged = [
        frame(0, at(10)),
        frame(1, at(10)),
        frame(2, at(105, inferred=True, observed=False)),
        frame(3, at(210)),
        frame(4, at(210)),
    ]
    with_observed = derive_metrics(observed, 5, 1.0)["events"]
    with_bridged = derive_metrics(bridged, 5, 1.0)["events"]
    assert with_bridged == []
    assert len(with_observed) >= len(with_bridged)


def test_motion_blobs_never_displace_detector_candidates():
    from app.vision.engine import merge_candidates

    detector = [{"x": i, "confidence": 0.31 - 0.03 * i} for i in range(8)]
    motion = [{"x": 100 + i, "confidence": 0.15, "source": "motion"} for i in range(8)]
    merged = merge_candidates(detector, motion)
    assert len(merged) == 12
    assert all(d in merged for d in detector)
    assert sum(1 for c in merged if c.get("source") == "motion") == 4
    # A frame with few detector hits leaves room for motion evidence.
    assert len(merge_candidates(detector[:2], motion)) == 10


def test_tiles_scale_with_resolution_but_stay_bounded():
    from types import SimpleNamespace

    from app.vision.ball import TiledBallDetector

    detector = TiledBallDetector.__new__(TiledBallDetector)
    calls = []

    class Model:
        def predict(self, images, **kwargs):
            calls.append(len(images))
            return [
                SimpleNamespace(boxes=SimpleNamespace(xyxy=np.empty((0, 4)), conf=np.empty(0)))
                for _ in images
            ]

    detector.model, detector.classes, detector.device = Model(), [0], "cpu"
    assert len(detector.tiles(np.zeros((360, 640, 3), np.uint8))) == 2
    assert len(detector.tiles(np.zeros((1080, 1920, 3), np.uint8))) == 12
    # 4K is reduced to 1080p before tiling: same cost, ball still twice the 480p size.
    tiles = detector.tiles(np.zeros((2160, 3840, 3), np.uint8))
    assert len(tiles) == 12 and tiles[0][2] == 0.5
    detector.detect_batch([np.zeros((2160, 3840, 3), np.uint8)] * 8)
    assert sum(calls) == 96 and max(calls) <= TiledBallDetector.MAX_CROPS


def test_4k_detections_map_back_to_source_pixels():
    from types import SimpleNamespace

    from app.vision.ball import TiledBallDetector

    detector = TiledBallDetector.__new__(TiledBallDetector)

    class Model:
        def predict(self, images, **kwargs):
            results = []
            for image in images:
                ys, xs = np.where(image[:, :, 0] > 0)
                boxes = (
                    np.array([[xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]])
                    if len(xs)
                    else np.empty((0, 4))
                )
                results.append(
                    SimpleNamespace(
                        boxes=SimpleNamespace(xyxy=boxes, conf=np.full(len(boxes), 0.9))
                    )
                )
            return results

    detector.model, detector.classes, detector.device = Model(), [0], "cpu"
    frame = np.zeros((2160, 3840, 3), np.uint8)
    frame[1000:1040, 3000:3040] = 255
    found = detector.detect(frame)
    assert len(found) == 1
    assert abs(found[0]["x"] - 3020) <= 3 and abs(found[0]["y"] - 1020) <= 3


def test_diagnostic_metrics_use_source_timestamp_offset():
    frames = [frame(120), frame(120.2), frame(120.4), frame(120.6)]
    out = derive_metrics(frames, 5, 0.8, start_seconds=120)
    assert out["teamSeconds"] == [0.8, 0]
    assert out["unknownSeconds"] == 0


def test_invalid_diagnostic_and_search_options_rejected(service, monkeypatch, tmp_path):
    _, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    for query in ("diagnostic=yes", "start=-1", "start=nan", "start=120", "search=magic"):
        response = client.post(
            f"/jobs?{query}", content=b"x", headers={"Content-Type": "video/mp4"}
        )
        assert response.status_code == 400


def test_focused_ball_search_falls_back_after_miss_and_restores_coordinates():
    from types import SimpleNamespace

    from app.vision.ball import TiledBallDetector

    class Model:
        calls = 0

        def predict(self, images, **kwargs):
            self.calls += 1
            results = []
            for image in images:
                ys, xs = np.where(image[:, :, 0] > 0)
                boxes = (
                    np.array([[xs.min(), ys.min(), xs.max(), ys.max()]])
                    if len(xs)
                    else np.empty((0, 4))
                )
                results.append(
                    SimpleNamespace(
                        boxes=SimpleNamespace(xyxy=boxes, conf=np.full(len(boxes), 0.8))
                    )
                )
            return results

    detector = TiledBallDetector.__new__(TiledBallDetector)
    detector.model, detector.classes, detector.device = Model(), [0], "cpu"
    image = np.zeros((360, 640, 3), np.uint8)
    image[76:85, 96:105] = 255
    found = detector.detect(image, focus=[100, 80])
    assert len(found) == 1 and found[0]["x"] == 100
    assert detector.model.calls == 1
    image[:] = 0
    image[276:285, 536:545] = 255
    found = detector.detect(image, focus=[100, 80])
    assert len(found) == 1 and (found[0]["x"], found[0]["y"]) == (540, 280)
    # A miss sweeps the remaining tiles in one batched call; no tile is repeated.
    assert detector.model.calls == 3


def test_player_reacquisition_velocity_uses_elapsed_gap():
    from app.vision.tracking import MotionTracker

    tracker = MotionTracker()
    identity = np.float32([[1, 0, 0], [0, 1, 0]])
    tracker.update([player()], 0, identity)
    tracker.update([], 0.2, identity)
    tracker.update([], 0.4, identity)
    tracker.update([player(x=6)], 0.6, identity)
    assert len(tracker.tracks) == 1
    assert tracker.tracks[0].velocity[0] == pytest.approx(5)


def test_short_diagnostic_reuses_detection_and_survives_unknown_kits(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import cv2
    import sys

    from app.vision import engine

    video = tmp_path / "fixture.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (80, 60))
    for _ in range(25):
        writer.write(np.full((60, 80, 3), (40, 150, 40), np.uint8))
    writer.release()
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    calls = []

    class Tensor:
        def __init__(self, values):
            self.values = np.asarray(values)

        def cpu(self):
            return self

        def numpy(self):
            return self.values

    class Model:
        names = {0: "referee"}

        def predict(self, image, **options):
            calls.append(options)
            return [
                SimpleNamespace(
                    boxes=SimpleNamespace(
                        xyxy=Tensor([[10, 10, 30, 50]]),
                        conf=Tensor([0.9]),
                        cls=Tensor([0]),
                    )
                )
            ]

    # This checks orchestration with mocked detections, not a real model. Keep
    # it runnable in the lean Poetry/CI environment without GPU dependencies.
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None))
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: Model(), settings={}))
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    monkeypatch.setattr(
        engine,
        "create_ball_detector",
        lambda *args: SimpleNamespace(
            detect=lambda *a, **k: [], detect_batch=lambda frames, **k: [[] for _ in frames]
        ),
    )
    result = engine.run_video(
        video, tmp_path / "result.json", start_seconds=1, max_seconds=1, sample_fps=6
    )
    assert len(calls) == len(result["frames"]) == 5
    assert all(c["agnostic_nms"] for c in calls)
    assert result["teams"] == []
    assert result["metrics"]["unknownSeconds"] == 1
    assert result["frames"][0]["t"] == 1
    assert result["frames"][-1]["t"] < 2
    assert "Team assignments" in result["limitations"][0]


def test_gpu_receives_bounded_diagnostic_options(tmp_path, monkeypatch):
    import gzip
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.vision import gpu

    calls = []

    def remote_gen(*args):
        calls.append(args)
        return iter([{"result_gz": gzip.compress(b'{"frames":[]}')}])

    monkeypatch.setattr(gpu, "public_base", lambda: "https://worker.example")
    monkeypatch.setitem(
        sys.modules,
        "app.vision.modal_app",
        SimpleNamespace(
            app=SimpleNamespace(run=nullcontext), analyse=SimpleNamespace(remote_gen=remote_gen)
        ),
    )
    gpu.run_on_modal(
        "a" * 32,
        tmp_path / "result.json",
        "fixture-token",
        lambda **kw: None,
        lambda: False,
        "small-ball",
        6,
        max_seconds=20,
        start_seconds=300,
        ball_search="adaptive",
    )
    assert calls[0][2:7] == ("small-ball", 6, 20, 300, "adaptive")
    from app.vision.access import valid_video_grant
    assert valid_video_grant("fixture-token", "a" * 32, calls[0][7])
    assert (tmp_path / "result.json").read_text() == '{"frames":[]}'
    # A rerun's kit order reaches the GPU engine too.
    gpu.run_on_modal("a" * 32, tmp_path / "result.json", "fixture-token", lambda **kw: None, lambda: False, "general", 6,
                     reference_colours=["#dc2626", "#22c55e"], reference_job="e" * 32)
    assert calls[1][9:] == (["#dc2626", "#22c55e"], "e" * 32)


def test_gap_does_not_join_nearby_balls_with_different_identities():
    from app.vision.faint import recover_ball

    identity = np.float32([[1, 0, 0], [0, 1, 0]])
    frames = [
        {
            "t": i * 0.2,
            "scene": 0,
            "players": [],
            "ballCandidates": [],
            "ball": None
            if i == 1
            else {"x": 100 + i, "y": 100, "confidence": 0.8, "trackId": 1 if i == 0 else 2},
        }
        for i in range(3)
    ]
    assert recover_ball(frames, [identity] * 3, 734, 5)["inferred"] == 0


def test_gap_interpolation_compensates_for_nonuniform_camera_pan():
    from app.vision.faint import recover_ball

    identity = np.float32([[1, 0, 0], [0, 1, 0]])
    pan = np.float32([[1, 0, 40], [0, 1, 0]])
    frames = [
        {
            "t": i * 0.2,
            "scene": 0,
            "players": [],
            "ballCandidates": [],
            "ball": None
            if i == 1
            else {"x": 10 if i == 0 else 50, "y": 10, "confidence": 0.8, "trackId": 1},
        }
        for i in range(3)
    ]
    assert recover_ball(frames, [identity, pan, identity], 734, 5)["inferred"] == 1
    assert frames[1]["ball"]["x"] == 50
    assert frames[1]["ball"]["observed"] is False


def test_independent_recovery_chains_have_distinct_identities():
    from app.vision.faint import recover_ball

    identity = np.float32([[1, 0, 0], [0, 1, 0]])
    frames = [
        {
            "t": i * 0.2,
            "scene": i // 3,
            "players": [],
            "ball": None,
            "ballCandidates": [{"x": 100 + i * 10, "y": 50, "confidence": 0.3}],
        }
        for i in range(6)
    ]
    recover_ball(frames, [identity] * 6, 734, 5)
    assert frames[0]["ball"]["trackId"] != frames[3]["ball"]["trackId"]
    assert frames[0]["ball"]["trackId"] < -1


def test_auxiliary_ball_head_preserves_scores_and_merges_duplicate_views():
    from app.vision.ball import auxiliary_ball_candidates, fuse_ball_candidates

    boxes = np.array([[10, 10, 14, 14], [20, 20, 24, 24], [0, 0, 10, 30]])
    found = auxiliary_ball_candidates(boxes, [0.8, 0.4, 0.9], [0, 0, 2], [0])
    assert len(found) == 1 and found[0]["confidence"] == 0.8
    assert found[0]["source"] == "player-model"
    primary = [{**found[0], "confidence": 0.7}]
    assert len(fuse_ball_candidates(primary, found)) == 1
    assert fuse_ball_candidates(primary, found)[0]["confidence"] == 0.8
    dedicated = [{**found[0], "confidence": 0.22, "source": "detector"}]
    agreed = fuse_ball_candidates(dedicated, found)
    assert len(agreed) == 1 and agreed[0]["source"] == "detector" and agreed[0]["confidence"] == 0.8
    alone = fuse_ball_candidates([], found)
    assert alone[0]["source"] == "player-model"


def test_still_player_model_mark_cannot_hide_the_dedicated_ball():
    from app.vision.ball_tracking import BallTracker

    tracker = BallTracker()
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    logo = {"x": 77.0, "y": 52.0, "confidence": 0.8, "source": "player-model", "box": [74, 49, 80, 55]}
    shape = (360, 640)
    assert tracker.update([logo], 0.0, identity, shape) is None
    assert tracker.update([logo], 0.2, identity, shape) is None
    real = {"x": 280.0, "y": 170.0, "confidence": 0.28, "source": "detector", "box": [277, 167, 283, 173]}
    assert tracker.update([logo, real], 0.4, identity, shape) is None  # one weak hit cannot open a track
    chosen = tracker.update([logo, real], 0.6, identity, shape)
    assert chosen is not None and chosen["x"] == 280.0 and chosen["source"] == "detector"
    # A player-network ball that actually travels is still reported.
    moving = BallTracker()
    assert moving.update([{**logo, "x": 200.0}], 0.0, identity, shape) is None
    gone = moving.update([{**logo, "x": 230.0}], 0.2, identity, shape)
    assert gone is not None and gone["x"] == 230.0


def test_motion_recovery_requires_local_bracketing_neural_evidence():
    from app.vision.faint import confirm_chains

    identity = np.float32([[1, 0, 0], [0, 1, 0]])
    frames = [
        {
            "scene": 0,
            "ball": None,
            "ballCandidates": [
                {
                    "x": 100 + i * 5,
                    "y": 100,
                    "confidence": 0.2 if i in (0, 2) else 0.1,
                    "source": "detector" if i in (0, 2) else "motion",
                }
            ],
        }
        for i in range(20)
    ]
    promoted = confirm_chains(frames, [identity] * 20, 734, sample_fps=5)
    assert 1 in promoted  # one observed blob between two detector hits
    assert all(i <= 2 for i in promoted)  # no authentication of the entire later motion chain
    assert promoted[1]["confidence"] == 0.1  # trajectory length never invents detector confidence


def test_byte_tracker_keeps_identity_through_a_short_occlusion_and_confirms_before_counting():
    from app.vision.tracking import ByteTracker

    tracker = ByteTracker()
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    frames = []
    for k in range(20):
        x = 100 + 6 * k
        obs = [] if 8 <= k <= 14 else [{"team": 0, "role": "player", "box": [x, 100, x + 12, 130], "confidence": 0.9}]
        frames.append(tracker.update(obs, k * 0.2, identity))
    ids = {p["id"] for f in frames for p in f}
    assert len(ids) == 1  # 1.4 s unseen is within the lost-track buffer
    assert len(frames[0]) == 1  # the first sighting is restored once the track is confirmed


def test_byte_tracker_does_not_invent_tracks_from_one_off_false_positives():
    from app.vision.tracking import ByteTracker

    tracker = ByteTracker()
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    out = tracker.update([{"team": -1, "role": "player", "box": [300, 100, 312, 130], "confidence": 0.95}], 0.0, identity)
    for k in range(1, 6):
        out += tracker.update([], k * 0.2, identity)
    assert out == []


def test_byte_tracker_tolerates_one_wrong_kit_colour():
    from app.vision.tracking import ByteTracker

    tracker = ByteTracker()
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    ids = set()
    for k in range(10):
        team = 1 if k == 5 else 0  # a single frame of mis-read colour
        x = 100 + 5 * k
        for p in tracker.update([{"team": team, "role": "player", "box": [x, 100, x + 12, 130], "confidence": 0.9}], k * 0.2, identity):
            ids.add(p["id"])
    assert len(ids) == 1


def test_byte_tracker_confirms_tracks_at_one_sample_per_second():
    from app.vision.tracking import ByteTracker

    tracker = ByteTracker()
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    lists = []
    for k in range(6):
        x = 100 + 20 * k
        lists.append(tracker.update([{"team": 0, "role": "player", "box": [x, 100, x + 12, 130], "confidence": 0.9}], float(k), identity))
    out = [p for frame in lists for p in frame]  # read after late additions landed
    assert len(out) == 6 and len({p["id"] for p in out}) == 1


def test_established_kit_never_takes_the_other_teams_player():
    from app.vision.tracking import ByteTracker

    tracker = ByteTracker()
    identity = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    for k in range(5):  # player A (kit 0) well established
        tracker.update([{"team": 0, "role": "player", "box": [100, 100, 112, 130], "confidence": 0.9}], k * 0.2, identity)
    # A is now only weakly detected while B (kit 1) appears right next to him.
    out = tracker.update(
        [
            {"team": 0, "role": "player", "box": [101, 100, 113, 130], "confidence": 0.2},
            {"team": 1, "role": "player", "box": [106, 100, 118, 130], "confidence": 0.9},
        ],
        1.0,
        identity,
    )
    a = [p for p in out if p["team"] == 0]
    assert a and all(p["team"] == 0 for p in out if p["id"] == a[0]["id"])


def test_upload_reservation_survives_lost_reply_without_duplicate_job(hosted):
    server, client, _ = hosted
    owner, request_id = 'c' * 32, 'd' * 32
    query = f'/jobs?size=100&owner={owner}&requestId={request_id}'
    headers = {'Content-Type': 'video/mp4', 'x-pitchlens-owner': owner}
    first = client.post(query, headers=headers)
    assert first.status_code == 200
    job = first.json()['id']
    assert client.put(f'/jobs/{job}/video?offset=0', content=b'first', headers=headers).status_code == 200
    retry = client.post(query, headers=headers)
    assert retry.status_code == 200
    assert retry.json()['id'] == job and retry.json()['receivedBytes'] == 5
    assert len(list(server.ROOT.glob('*/status.json'))) == 1
    assert client.post(query.replace('size=100', 'size=200'), headers=headers).status_code == 409
    assert client.post(query.replace(owner, 'e' * 32), headers={'Content-Type': 'video/mp4'}).status_code == 409
    assert client.post(f'/jobs/{job}/cancel', headers=headers).status_code == 200
    assert client.post(query, headers=headers).status_code == 409
    assert client.post(query.replace(request_id, 'f' * 32), headers=headers).status_code == 200


def test_upload_reservation_id_requires_an_owner_and_chunk_size(hosted):
    _, client, _ = hosted
    headers = {'Content-Type': 'video/mp4'}
    for query in ['size=100&requestId=bad', 'size=100&requestId=' + 'd' * 32,
                  'owner=' + 'c' * 32 + '&requestId=' + 'd' * 32]:
        assert client.post('/jobs?' + query, headers=headers).status_code == 400


def test_stationary_sky_distractor_is_removed_before_selecting_moving_ball():
    from app.vision.faint import reject_static_candidates, strong_candidates
    from app.vision.ball_tracking import BallTracker

    drift = np.array([[1., 0, .4], [0, 1., 0]])
    frames = [{"t": i / 5, "scene": 0, "players": [], "ballCandidates": [
        {"x": 77., "y": 52., "confidence": .8, "outsidePitch": True},
        {"x": 140. + 5 * i, "y": 180., "confidence": .4},
    ]} for i in range(25)]
    # A missed camera estimate must not protect a fixed image speck.
    matrices = [drift if i not in (0, 4) else None for i in range(25)]
    assert reject_static_candidates(frames, matrices, 734, 5) == 25
    tracker = BallTracker()
    for i, frame in enumerate(frames):
        assert len(frame["ballCandidates"]) == 1
        ball = tracker.update(strong_candidates(frame["ballCandidates"]), frame['t'], drift, (360, 640))
        if i:
            assert ball is not None and ball["x"] == 140 + 5 * i


def test_static_filter_preserves_dead_balls_unknown_kit_keepers_and_airborne_motion():
    from app.vision.faint import reject_static_candidates
    identity = np.eye(2, 3)
    for variant in ('confident-on-pitch', 'attended', 'moving', 'cut'):
        frames = []
        for i in range(25):
            candidate = {'x': 77. + (i * 4 if variant == 'moving' else 0), 'y': 52.,
                         'confidence': .8, 'outsidePitch': variant != 'confident-on-pitch'}
            players = [{'team': -1, 'role': 'goalkeeper', 'box': [70, 20, 85, 54]}] if variant == 'attended' else []
            frames.append({'t': i / 5, 'scene': int(i >= 12) if variant == 'cut' else 0,
                           'players': players, 'ballCandidates': [candidate]})
        assert reject_static_candidates(frames, [identity] * 25, 734, 5) == 0, variant


def test_static_filter_rejects_camera_fixed_weak_object_despite_single_confidence_spike():
    from app.vision.faint import reject_static_candidates
    pan = np.array([[1., 0, 5.], [0, 1., 0]])
    frames = [{'t': i / 5, 'scene': 0, 'players': [], 'ballCandidates': [
        {'x': 50 + 5 * i, 'y': 70, 'confidence': .7 if i == 4 else .15}
    ]} for i in range(25)]
    assert reject_static_candidates(frames, [pan] * 25, 734, 5) == 25


def test_auto_device_uses_available_acceleration_but_respects_override(monkeypatch):
    from types import SimpleNamespace
    from app.vision.runtime import inference_device
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
                           backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)))
    monkeypatch.delenv('VISION_DEVICE', raising=False)
    assert inference_device(fake) == 'mps'
    fake.cuda.is_available = lambda: True
    assert inference_device(fake) == 'cuda'
    monkeypatch.setenv('VISION_DEVICE', 'cpu')
    assert inference_device(fake) == 'cpu'
    monkeypatch.setenv('VISION_DEVICE', 'auto')
    fake.cuda.is_available = lambda: False
    fake.backends.mps.is_available = lambda: False
    assert inference_device(fake) == 'cpu'


def test_role_consensus_repairs_player_misclassification_but_preserves_referees_and_ambiguity():
    from app.vision.engine import stabilise_roles, vote_teams
    frames = []
    for i in range(10):
        frames.append({'scene': 0, 'players': [
            {'id': 1, 'role': 'referee' if i == 4 else 'player', 'team': -1 if i == 4 else 0, 'confidence': .9},
            {'id': 2, 'role': 'player' if i == 4 else 'referee', 'team': 0 if i == 4 else -1, 'confidence': .9},
            {'id': 3, 'role': 'player' if i % 2 else 'goalkeeper', 'team': 1 if i % 2 else -1, 'confidence': .9},
        ]})
    assert stabilise_roles(frames) == 2
    vote_teams(frames)
    assert all(f['players'][0]['team'] == 0 and f['players'][0]['role'] == 'player' for f in frames)
    assert frames[4]['players'][0]['detectedRole'] == 'referee'
    assert all(f['players'][1]['team'] == -1 and f['players'][1]['role'] == 'referee' for f in frames)
    assert len(set(f['players'][2]['role'] for f in frames)) == 2
    assert stabilise_roles(frames) == 0  # repeated processing is stable


def test_appearance_stops_unknown_role_from_stealing_an_opposing_player_id():
    from app.vision.tracking import ByteTracker
    tracker = ByteTracker()
    matrix = np.eye(2, 3)
    red = {'box': [50, 20, 70, 70], 'team': 0, 'confidence': .9, 'kitFeature': [130, 180, 150]}
    for i in range(4):
        tracked = tracker.update([red], i / 5, matrix)
    original = tracked[0]['id']
    # The detector calls a white-shirt opponent a referee at the same position.
    # Geometry and team=-1 alone used to attach them to the established red ID.
    white = {**red, 'team': -1, 'role': 'referee', 'kitFeature': [200, 120, 130]}
    assert tracker.update([white], .8, matrix) == []
    other = tracker.update([white], 1., matrix)
    assert other[0]['id'] != original
    # A shadow changes brightness, not the kit's chromaticity.
    shadow = {**red, 'kitFeature': [70, 179, 151]}
    assert tracker.update([shadow], 1.2, matrix)[0]['id'] == original


def test_saved_video_rerun_is_private_idempotent_and_preserves_the_report(hosted, monkeypatch):
    import json
    from types import SimpleNamespace
    server, client, _ = hosted
    owner, job = 'c' * 32, 'a' * 32
    source = server.ROOT / job
    source.mkdir()
    (source / 'video').write_bytes(b'complete saved video')
    (source / 'result.json').write_text('{"old":true}')
    (source / 'review.json').write_text('{"keep":true}')
    server.write_status(source, {'id': job, 'owner': owner, 'title': 'Saved match',
        'status': 'completed', 'createdAt': 0, 'profile': 'general', 'sampleFps': 6,
        'video': {'duration': 600}, 'maxSeconds': 20, 'startSeconds': 0})
    queued = []
    monkeypatch.setattr(server, 'pool', SimpleNamespace(submit=lambda *args: queued.append(args)))
    headers = {'x-pitchlens-owner': owner}
    query = f'/jobs/{job}/rerun?mode=section&start=300&requestId=' + 'd' * 32
    assert client.post(query).status_code == 404
    response = client.post(query, headers=headers)
    assert response.status_code == 200
    new = response.json()['id']
    assert new != job and response.json()['startSeconds'] == 300 and response.json()['maxSeconds'] == 20
    assert 'owner' not in response.json() and 'rerunRequestId' not in response.json()
    assert (server.ROOT / new / 'video').read_bytes() == b'complete saved video'
    assert json.loads((server.ROOT / new / 'status.json').read_text())['owner'] == owner
    assert client.post(query, headers=headers).json()['id'] == new
    assert len(queued) == 1
    assert client.post(query.replace('start=300', 'start=400'), headers=headers).status_code == 409
    assert (source / 'result.json').read_text() == '{"old":true}'
    assert (source / 'review.json').read_text() == '{"keep":true}'
    source.joinpath('video').unlink()
    assert (server.ROOT / new / 'video').read_bytes() == b'complete saved video'


def test_full_rerun_validates_saved_footage_and_releases_failed_reservations(hosted, monkeypatch):
    from types import SimpleNamespace
    server, client, _ = hosted
    owner, job = 'c' * 32, 'a' * 32
    source = server.ROOT / job
    source.mkdir()
    saved = {'id': job, 'owner': owner, 'title': 'Match', 'status': 'completed',
             'createdAt': 0, 'profile': 'general', 'video': {'duration': 60}}
    server.write_status(source, saved)
    headers = {'x-pitchlens-owner': owner}
    query = f'/jobs/{job}/rerun?mode=full&requestId=' + 'e' * 32
    assert client.post(query, headers=headers).status_code == 409
    (source / 'video').write_bytes(b'complete')
    assert client.post(query + '&start=1', headers=headers).status_code == 400
    assert client.post(query.replace('mode=full', 'mode=section') + '&start=60', headers=headers).status_code == 400
    def fail(*args): raise RuntimeError('queue stopped')
    monkeypatch.setattr(server, 'pool', SimpleNamespace(submit=fail))
    assert client.post(query, headers=headers).status_code == 500
    assert server.active is None and list(server.ROOT.glob('*/status.json')) == [source / 'status.json']
    assert not server.cancellations
    monkeypatch.setattr(server, 'pool', SimpleNamespace(submit=lambda *args: None))
    response = client.post(query, headers=headers)
    assert response.status_code == 200 and response.json()['maxSeconds'] is None
    assert response.json()['startSeconds'] == 0


def test_a_still_mark_above_the_players_is_not_the_ball_even_from_the_ball_model():
    """The opening of videoplayback locked onto a scoreboard at (77, 52).

    Heads on that tape sit near y=137. A confident ball-model hit on that mark
    must not be published, and must not hide a ball that is actually moving.
    """
    from app.vision.ball_tracking import BallTracker

    tracker = BallTracker()
    matrix = np.eye(2, 3)
    shape = (360, 640)
    published = []
    for i in range(12):
        graphic = {
            "x": 77.2,
            "y": 51.6,
            "confidence": 0.82,
            "source": "detector",
            "aboveHeads": True,
        }
        candidates = [graphic]
        if i >= 4:
            candidates.append(
                {
                    "x": 180.0 + 18 * (i - 4),
                    "y": 210.0,
                    "confidence": 0.28,
                    "source": "detector",
                    "aboveHeads": False,
                }
            )
        published.append(tracker.update(candidates, i * 0.2, matrix, shape, cut=False))
    assert all(ball is None or ball["y"] > 100 for ball in published)
    assert any(ball is not None and ball["x"] >= 180 for ball in published)


def test_a_cross_above_head_height_is_kept_once_it_moves():
    from app.vision.ball_tracking import BallTracker

    tracker = BallTracker()
    matrix = np.eye(2, 3)
    seen = []
    for i in range(6):
        seen.append(
            tracker.update(
                [{
                    "x": 100.0 + 20 * i,
                    "y": 40.0,
                    "confidence": 0.4,
                    "source": "detector",
                    "aboveHeads": True,
                }],
                i * 0.2,
                matrix,
                (360, 640),
                cut=False,
            )
        )
    assert seen[0] is None
    assert seen[-1] is not None and seen[-1]["x"] == 200.0


def test_open_site_limits_daily_analyses_and_reruns_per_match(hosted, monkeypatch):
    import json
    import time as clock
    from types import SimpleNamespace
    server, client, _ = hosted
    owner, job = 'c' * 32, 'a' * 32
    source = server.ROOT / job
    source.mkdir()
    (source / 'video').write_bytes(b'complete saved video')
    server.write_status(source, {'id': job, 'owner': owner, 'title': 'Saved match', 'status': 'completed',
        'createdAt': 0, 'profile': 'general', 'sampleFps': 6, 'video': {'duration': 600}})
    monkeypatch.setattr(server, 'pool', SimpleNamespace(submit=lambda *args: None))
    headers = {'x-pitchlens-owner': owner}

    def rerun(mode, n):
        global_query = f'/jobs/{job}/rerun?mode={mode}&start=0&requestId={n:032x}'
        response = client.post(global_query, headers=headers)
        if response.status_code == 200:
            # Let the next one start: this test does not run the analysis.
            server.active = None
            server.cancellations.clear()
            done = server.ROOT / response.json()['id']
            status = json.loads((done / 'status.json').read_text())
            server.write_status(done, {**status, 'status': 'completed'})
        return response.status_code

    monkeypatch.setenv('VISION_MAX_JOBS_PER_DAY', '0')
    assert [rerun('full', n) for n in range(1, 5)] == [200, 200, 200, 409]
    # The daily cap counts every start and refuses uploads before any bytes are sent.
    monkeypatch.setenv('VISION_MAX_JOBS_PER_DAY', '3')
    assert len(json.loads((server.ROOT / 'starts.json').read_text())) == 3
    assert rerun('section', 10) == 429
    upload = client.post('/jobs?profile=general', headers={**headers, 'content-type': 'video/mp4', 'x-upload-size': '100'})
    assert upload.status_code == 429
    # Starts older than a day no longer count.
    (server.ROOT / 'starts.json').write_text(json.dumps([clock.time() - 90000] * 3))
    assert rerun('section', 11) == 200

    def recent():
        return len(server._recent_starts(clock.time()))

    assert recent() == 1
    # A chunked upload counts when it starts (begin), and so does a retry.
    data = FIXTURE.read_bytes()
    created = client.post(f'/jobs?size={len(data)}', headers={**headers, 'content-type': 'video/mp4'})
    assert created.status_code == 200, created.text
    upload = created.json()['id']
    client.put(f'/jobs/{upload}/video?offset=0', content=data, headers=headers)
    assert client.post(f'/jobs/{upload}/start', headers=headers).status_code == 200
    assert recent() == 2
    server.active = None  # the analysis stopped: it can be retried
    server.cancellations.clear()
    assert client.post(f'/jobs/{upload}/retry', headers=headers).status_code == 200
    assert recent() == 3
    server.active = None
    server.cancellations.clear()
    assert rerun('section', 12) == 429


def test_ball_model_status_never_reveals_owner_keys(service, monkeypatch, tmp_path):
    import json
    server, client = service
    monkeypatch.setenv('VISION_DATA_DIR', str(tmp_path))
    models = tmp_path / 'models'
    models.mkdir()
    owner = 'f' * 32
    (models / 'ball-model.json').write_text(json.dumps({'active': None, 'runs': [{'weights': 'ball-0123456789abcdef.pt', 'owner': owner, 'ownerHash': 'abc', 'kept': False}]}))
    monkeypatch.setattr(server, 'training', {'state': 'done', 'run': {'owner': owner, 'kept': False}})
    body = client.get('/ball-model').text
    assert owner not in body and 'ownerHash' not in body


def test_youtube_download_steps_down_to_the_sharpest_version_that_fits(tmp_path, monkeypatch):
    import sys
    import types

    from app.vision import youtube

    asked = []

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            fmt = self.options["format"]
            asked.append(fmt.split("height<=")[1].split("]")[0])
            # Too large at 1080p (yt-dlp skips the file under max_filesize).
            if "height<=1080" not in fmt:
                (tmp_path / "download.mp4").write_bytes(b"video")
            return {"title": "Match", "duration": 2400}

    fake = types.SimpleNamespace(YoutubeDL=FakeYDL, utils=types.SimpleNamespace(DownloadError=RuntimeError))
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setattr(youtube, "remux", lambda p: p)
    stages = []
    path, info = youtube.download("https://youtu.be/dQw4w9WgXcQ", tmp_path, 100, progress=lambda **kw: stages.append(kw.get("stage")))
    assert asked == ["1080", "720"] and info["height"] == 720 and path.name == "download.mp4"
    assert any("trying a smaller version" in (s or "") for s in stages)
    # Nothing fits at any height: a clear message with the limit.
    (tmp_path / "download.mp4").unlink()
    monkeypatch.setattr(FakeYDL, "extract_info", lambda self, url, download: {"title": "Match", "duration": 2400})
    with pytest.raises(ValueError, match="even at 480p"):
        youtube.download("https://youtu.be/dQw4w9WgXcQ", tmp_path, 100)


# ── Retention, model availability and request limits ──────────────────────
def _saved_match(server, job='a' * 32, owner='c' * 32, created=0.0, video=b'video-bytes'):
    import json

    directory = server.ROOT / job
    directory.mkdir()
    if video is not None:
        (directory / 'video').write_bytes(video)
    (directory / 'result.json').write_text(json.dumps({'frames': [], 'video': {'width': 640, 'height': 360}}))
    server.write_status(directory, {'id': job, 'owner': owner, 'title': 'Match', 'status': 'completed',
        'createdAt': created, 'profile': 'general', 'sampleFps': 6, 'video': {'duration': 600}})
    return directory


def test_a_rerun_expires_with_the_original_upload(hosted, monkeypatch):
    import json
    import time as clock

    server, client, _ = hosted
    monkeypatch.setattr(server, 'RETENTION_HOURS', 168)
    monkeypatch.setenv('VISION_MAX_JOBS_PER_DAY', '0')
    headers = {'x-pitchlens-owner': 'c' * 32}
    source = _saved_match(server, created=clock.time() - 167 * 3600)  # an hour before expiry
    response = client.post(f"/jobs/{'a' * 32}/rerun?mode=section&start=0&requestId={'1' * 32}", headers=headers)
    assert response.status_code == 200, response.text
    rerun = response.json()['id']
    server.active = None
    server.cancellations.clear()
    status = json.loads((server.ROOT / rerun / 'status.json').read_text())
    server.write_status(server.ROOT / rerun, {**status, 'status': 'completed'})
    # Two hours later the upload is past its retention, and so is every link to it.
    real = clock.time
    monkeypatch.setattr(server.time, 'time', lambda: real() + 2 * 3600)
    server.delete_expired()
    assert not (source / 'video').exists() and not (server.ROOT / rerun / 'video').exists()
    assert client.get(f'/jobs/{rerun}/video', headers=headers).status_code == 404
    again = client.post(f"/jobs/{rerun}/rerun?mode=section&start=0&requestId={'2' * 32}", headers=headers)
    assert again.status_code == 409


def test_player_load_runs_without_the_ball_model(hosted, monkeypatch, tmp_path):
    from app.vision.profiles import missing_models

    server, client, submitted = hosted
    monkeypatch.setenv('VISION_BALL_MODEL_PATH', str(tmp_path / 'missing-ball.onnx'))
    health = client.get('/health').json()
    assert health['available'] is True and health['profiles'] == []
    # small-ball shares the general player model, so player load can run on it too.
    assert health['playerLoadProfiles'] == ['general', 'small-ball']
    assert client.post('/jobs?search=none&size=10', headers={'Content-Type': 'video/mp4'}).status_code == 200
    server.active = None
    assert client.post('/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ').status_code == 200
    server.active = None
    refused = client.post('/jobs?search=exhaustive&size=10', headers={'Content-Type': 'video/mp4'})
    assert refused.status_code == 503 and 'models are not installed' in refused.json()['detail']
    asked = client.post('/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ&search=adaptive')
    assert asked.status_code == 503 and server.active is None
    # The GPU copies in only what the job will load.
    assert missing_models('general', 'none') == []
    assert missing_models('general', 'exhaustive') == [tmp_path / 'missing-ball.onnx']
    monkeypatch.setenv('VISION_MODEL_PATH', str(tmp_path / 'missing-player.pt'))
    assert missing_models('general', 'none') == [tmp_path / 'missing-player.pt']


def test_retry_and_rerun_need_only_the_models_their_mode_loads(hosted, monkeypatch, tmp_path):
    import json

    server, client, _ = hosted
    monkeypatch.setenv('VISION_BALL_MODEL_PATH', str(tmp_path / 'missing-ball.onnx'))
    monkeypatch.setenv('VISION_MAX_JOBS_PER_DAY', '0')
    headers = {'x-pitchlens-owner': 'c' * 32}
    source = _saved_match(server)
    status = json.loads((source / 'status.json').read_text())
    server.write_status(source, {**status, 'status': 'failed', 'ballSearch': 'none'})
    assert client.post(f"/jobs/{'a' * 32}/retry", headers=headers).status_code == 200
    server.active = None
    server.cancellations.clear()
    server.write_status(source, {**status, 'status': 'completed', 'ballSearch': 'none'})
    assert client.post(f"/jobs/{'a' * 32}/rerun?mode=section&start=0&requestId={'1' * 32}", headers=headers).status_code == 200
    server.active = None
    server.cancellations.clear()
    server.write_status(source, {**status, 'status': 'failed', 'ballSearch': 'exhaustive'})
    assert client.post(f"/jobs/{'a' * 32}/retry", headers=headers).status_code == 503


def test_deeply_nested_json_is_a_bad_request_not_a_crash(hosted):
    server, client, _ = hosted
    job = 'a' * 32
    _saved_match(server, job=job)
    payload = '{"a":' + '[' * 50000 + ']' * 50000 + '}'
    headers = {'x-pitchlens-owner': 'c' * 32, 'content-type': 'application/json'}
    for path in ('/venues', f'/jobs/{job}/review', f'/jobs/{job}/calibration/preview',
                 f'/jobs/{job}/calibration', f'/jobs/{job}/ball-labels'):
        assert client.post(path, content=payload, headers=headers).status_code == 400, path


def test_a_large_upload_gets_longer_than_an_hour_but_not_forever(hosted, monkeypatch):
    server, client, _ = hosted
    monkeypatch.setattr(server, 'check_space', lambda size: None)
    clock = {'now': 1000.0}
    monkeypatch.setattr(server.time, 'monotonic', lambda: clock['now'])
    content = {'Content-Type': 'video/mp4'}

    def reserve(size):
        response = client.post(f'/jobs?size={size}', headers=content)
        assert response.status_code == 200, response.text
        return response.json()['id']

    def state(job):
        return client.get(f'/jobs/{job}').json()['status']

    def contend():
        """Another start wants the worker (the deadline applies only then): True if it got it."""
        response = client.post('/jobs?size=10', headers=content)
        if response.status_code != 200:
            assert response.status_code == 409, response.text
            return False
        client.post(f"/jobs/{response.json()['id']}/cancel")  # free the worker for the next step
        return True

    big = reserve(2 * 1024**3)  # 2 GB: about 2.4 h at 2 Mbit/s
    offset = 0
    for minute in range(10, 62, 3):  # still sending at 61 minutes
        clock['now'] = 1000.0 + minute * 60
        assert client.put(f'/jobs/{big}/video?offset={offset}', content=b'x' * 10).status_code == 200
        offset += 10
    assert not contend() and state(big) == 'uploading' and server.active == big
    clock['now'] = 1000.0 + 2.4 * 3600 - 60
    client.put(f'/jobs/{big}/video?offset={offset}', content=b'x' * 10)
    assert not contend() and state(big) == 'uploading'
    clock['now'] = 1000.0 + 2.5 * 3600
    client.put(f'/jobs/{big}/video?offset={offset + 10}', content=b'x' * 10)
    assert state(big) == 'uploading'  # alone, a slow upload may still finish
    assert contend() and state(big) == 'failed' and server.active is None

    small = reserve(100 * 1024**2)  # 100 MB keeps the one-hour limit
    start = clock['now']
    clock['now'] = start + 59 * 60
    client.put(f'/jobs/{small}/video?offset=0', content=b'x' * 10)
    assert not contend() and state(small) == 'uploading'
    clock['now'] = start + 60 * 60
    assert contend() and state(small) == 'failed'

    idle = reserve(2 * 1024**3)  # stalled: after 5 minutes another start may have the worker
    clock['now'] += 299
    assert not contend() and state(idle) == 'uploading'
    clock['now'] += 2
    assert state(idle) == 'uploading'  # polls alone keep a paused upload for its browser
    assert contend() and state(idle) == 'failed' and server.active is None
    abandoned = reserve(2 * 1024**3)  # nobody comes back: released once abandoned
    clock['now'] += server.UPLOAD_ABANDON_SECONDS - 1
    assert state(abandoned) == 'uploading'
    clock['now'] += 2
    assert state(abandoned) == 'failed' and server.active is None


# ── Engine: player load, adaptive search, decoder time, calibration reuse ──
FIXTURES = __import__("pathlib").Path(__file__).resolve().parent / "fixtures"


def _mock_yolo(monkeypatch, names, box=lambda image: [10, 10, 30, 50]):
    """Mocked ultralytics/torch (as in test_short_diagnostic_...): one box per image."""
    import sys
    from types import SimpleNamespace

    calls = []

    class Tensor:
        def __init__(self, values):
            self.values = np.asarray(values)

        def cpu(self):
            return self

        def numpy(self):
            return self.values

    class Model:
        def __init__(self):
            self.names = names

        def predict(self, images, **options):
            images = images if isinstance(images, list) else [images]
            calls.append(options)
            return [
                SimpleNamespace(boxes=SimpleNamespace(xyxy=Tensor([box(image)]), conf=Tensor([0.9]), cls=Tensor([0])))
                for image in images
            ]

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None))
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: Model(), settings={}))
    return calls


def _green_clip(tmp_path, frames=25, fps=10):
    import cv2

    video = tmp_path / "fixture.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), fps, (80, 60))
    for _ in range(frames):
        writer.write(np.full((60, 80, 3), (40, 150, 40), np.uint8))
    writer.release()
    return video


def _decoder_times(path, seek=0):
    """Frame number -> the decoder's own timestamp, counted as the engine counts
    (seek once to `seek`, then read on)."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    out, n = {}, 0
    if seek:
        cap.set(cv2.CAP_PROP_POS_FRAMES, seek)
        n = seek
    while cap.grab():
        out[n] = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        n += 1
    cap.release()
    return out


def test_player_load_never_needs_or_runs_a_ball_model(tmp_path, monkeypatch):
    from app.vision import engine

    calls = _mock_yolo(monkeypatch, {0: "person", 32: "sports ball"})
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, tmp_path / "missing-ball.onnx"))

    def no_ball_model(*args, **kwargs):
        raise AssertionError("a player-load job must not load the ball model")

    monkeypatch.setattr(engine, "create_ball_detector", no_ball_model)
    result = engine.run_video(_green_clip(tmp_path), tmp_path / "result.json", max_seconds=1, sample_fps=6, ball_search="none")
    assert result["ballSearch"] == "none" and result["ballInference"] == "skipped"
    assert result["ballModel"] is None and result["ballTracking"] == "skipped"
    assert calls and all(c["classes"] == [0] for c in calls)  # never the player model's ball class
    assert all(f["ball"] is None for f in result["frames"])
    assert "playerLoad" in result["metrics"]
    # Unmeasured, not missing (the report reads the key) and never 0.
    assert "ballInferenceSeconds" in result["performance"] and result["performance"]["ballInferenceSeconds"] is None
    m = result["metrics"]
    assert m["teamSeconds"] is None and m["possessionCoverage"] is None
    assert m["possessionShare"] == [None, None] and m["possessions"] is None


def test_adaptive_search_runs_the_focused_tile_path(tmp_path, monkeypatch):
    from app.vision import engine
    from app.vision.ball import TiledBallDetector

    _mock_yolo(monkeypatch, {0: "person"})
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    seen = []

    class Tiles(TiledBallDetector):
        def __init__(self):
            self.inference_calls = 0

        def detect(self, frame, threshold=0.05, focus=None):
            seen.append(focus)
            return []

        def detect_batch(self, frames, **kwargs):
            raise AssertionError("adaptive search goes frame by frame")

    monkeypatch.setattr(engine, "create_ball_detector", lambda *args: Tiles())
    result = engine.run_video(_green_clip(tmp_path), tmp_path / "result.json", max_seconds=1, sample_fps=6, ball_search="adaptive")
    assert result["ballSearch"] == "adaptive"
    assert len(seen) == len(result["frames"]) > 0
    # The ball model ran, so its time is a number and possession was measured.
    assert isinstance(result["performance"]["ballInferenceSeconds"], (int, float))
    assert isinstance(result["metrics"]["teamSeconds"], list)


def test_frame_times_follow_the_decoder_on_variable_frame_rate_video(tmp_path, monkeypatch):
    """vfr-30-15.mp4: 2 s at 30 fps then 2 s at 15 fps (OpenCV reports 23.08 fps,
    so the frame count drifts 0.6 s away from the real time). Made with the
    imageio-ffmpeg binary in the venv:

    ffmpeg -f lavfi -i "color=c=0x289628:size=160x96:rate=30:duration=4" \\
      -f lavfi -i "color=c=white:size=10x24:rate=30:duration=4" \\
      -f lavfi -i "color=c=0xc02020:size=10x24:rate=30:duration=4" \\
      -filter_complex "[0][1]overlay=x='10+30*t':y=30[a];[a][2]overlay=x='130-25*t':y=52,select='lt(t\\,2)+not(mod(n\\,2))'" \\
      -fps_mode vfr -c:v libx264 -preset veryslow -crf 30 -pix_fmt yuv420p -g 30 -movflags +faststart vfr-30-15.mp4
    """
    from app.vision import engine

    _mock_yolo(monkeypatch, {0: "person"})
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    path = FIXTURES / "vfr-30-15.mp4"
    pts = _decoder_times(path)
    for sample_fps in (6, 10):
        result = engine.run_video(path, tmp_path / "result.json", sample_fps=sample_fps, ball_search="none")
        frames = result["frames"]
        assert len(frames) > 10
        # Half a source-frame interval (30 fps): every overlay and seek lands on its picture.
        assert all(abs(f["t"] - pts[f["frame"]]) < 0.5 / 30 for f in frames)
        assert result["analysedStart"] == frames[0]["t"]
        assert abs(frames[-1]["t"] - pts[frames[-1]["frame"]]) < 0.017 and frames[-1]["t"] > 3.5
        assert result["analysedDuration"] == pytest.approx(result["video"]["duration"], abs=0.2)
    # A diagnostic window starts where the seek really landed: the seek to frame
    # 47 (2 s at the average rate) decodes the picture shown at 1.8 s.
    result = engine.run_video(path, tmp_path / "result.json", sample_fps=6, start_seconds=2, max_seconds=1, ball_search="none")
    frames = result["frames"]
    landed = _decoder_times(path, seek=frames[0]["frame"])
    assert result["analysedStart"] == frames[0]["t"] == pytest.approx(landed[frames[0]["frame"]], abs=0.001)
    assert all(abs(f["t"] - landed[f["frame"]]) < 0.5 / 30 for f in frames)
    assert frames[-1]["t"] < result["analysedStart"] + 1


def test_frame_times_are_unchanged_on_trimmed_and_constant_rate_video(tmp_path, monkeypatch):
    from app.vision import engine

    _mock_yolo(monkeypatch, {0: "person"})
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    monkeypatch.setattr(engine, "field_mask", lambda frame: np.full(frame.shape[:2], 255, np.uint8))
    for path in (FIXTURES / "trimmed-vfr.mp4", _green_clip(tmp_path, frames=40)):
        pts = _decoder_times(path)
        result = engine.run_video(path, tmp_path / "result.json", sample_fps=6, ball_search="none")
        # These decoder times agree with the frame count, so they were used before as well.
        assert [f["t"] for f in result["frames"]] == [round(pts[f["frame"]], 3) for f in result["frames"]]
        assert result["analysedStart"] == 0


def test_one_corrupt_timestamp_is_ignored_and_a_real_jump_re_anchors():
    from app.vision.engine import _frame_time

    fps, stride = 30.0, 5

    def run(positions):
        state, last, out = {}, -1.0, []
        for k, position in enumerate(positions):
            last = _frame_time(position, last, k * stride / fps, fps, stride, state)
            out.append(round(last, 3))
        return out

    step = stride / fps
    clean = [round(k * step, 3) for k in range(6)]
    assert run(clean) == clean
    # One corrupt forward jump: that frame falls back to its frame-count time, the rest follow the decoder.
    corrupt = list(clean)
    corrupt[3] = 1000.0
    assert run(corrupt) == clean
    # A real discontinuity (the stream jumps 30 s ahead and continues): re-anchored on the next reading.
    jumped = clean[:3] + [30.0 + k * step for k in range(3)]
    times = run(jumped)
    assert times[:3] == clean[:3] and times[3] < 30 and times[4:] == [round(30.0 + step, 3), round(30.0 + 2 * step, 3)]
    # The first frame takes any valid position (a seek may land anywhere).
    assert run([12.02, 12.19]) == [12.02, 12.19]
    # Without a usable position, frame-count time (when it moves forward); never backwards.
    assert run([float("nan"), 0.1, 0.05]) == [0.0, 0.1, round(2 * step, 3)]
    assert run([0.5, 0.4]) == [0.5, round(0.5 + 1 / fps, 3)]


def test_kit_calibration_detections_are_reused_only_for_the_same_picture(tmp_path, monkeypatch):
    """Seeking to a frame number lands one frame late on trimmed-vfr.mp4 (from
    frame 56 on); the main pass counts decoded frames. Each detection must come
    from the picture it is attached to."""
    import cv2

    from app.vision import engine

    def box(image):
        # The box encodes the picture: its x is the frame's mean brightness.
        x = round(float(image.mean()), 1)
        return [x, 10, x + 20, 60]

    _mock_yolo(monkeypatch, {0: "person"}, box=box)
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"test")
    monkeypatch.setattr(engine, "model_paths", lambda profile: (weights, weights))
    monkeypatch.setattr(engine, "field_mask", lambda frame: np.full(frame.shape[:2], 255, np.uint8))
    path = FIXTURES / "trimmed-vfr.mp4"
    cap = cv2.VideoCapture(str(path))
    pictures = []
    while True:
        ok, image = cap.read()
        if not ok:
            break
        pictures.append(image)
    cap.release()
    result = engine.run_video(path, tmp_path / "result.json", sample_fps=6, ball_search="none")
    checked = 0
    for f in result["frames"]:
        for p in f["players"]:
            assert p["box"][0] == round(float(pictures[f["frame"]].mean()), 1), f["frame"]
            checked += 1
    assert checked >= len(result["frames"]) - 2 and any(f["frame"] >= 56 for f in result["frames"])


def test_a_second_pitch_check_waits_its_turn(hosted, monkeypatch):
    import time

    from app.vision import calibrate

    server, client, _ = hosted
    _saved_match(server)
    entered, release = threading.Event(), threading.Event()

    def slow_preview(result, body):
        entered.set()
        release.wait(10)
        return {"fit": {"quality": "good"}, "template": None, "lines": []}

    monkeypatch.setattr(calibrate, "preview", slow_preview)
    headers = {"x-pitchlens-owner": "c" * 32}
    first = {}
    worker = threading.Thread(target=lambda: first.setdefault("response", client.post(f"/jobs/{'a' * 32}/calibration/preview", json={}, headers=headers)))
    worker.start()
    assert entered.wait(10)
    second = client.post(f"/jobs/{'a' * 32}/calibration/preview", json={}, headers=headers)
    assert second.status_code == 429 and "Another pitch check is running" in second.json()["detail"]
    release.set()
    worker.join(10)
    assert first["response"].status_code == 200
    # Free again afterwards.
    for _ in range(50):
        if server.pitch_slot.acquire(blocking=False):
            server.pitch_slot.release()
            break
        time.sleep(0.02)
    assert client.post(f"/jobs/{'a' * 32}/calibration/preview", json={}, headers=headers).status_code == 200


def _court_camera(template, size=(640, 360)):
    """A fixed camera behind the near touchline, in pitch axes."""
    import math

    L, W = template["length"], template["width"]
    position, target = np.array([L / 2, W + 6.0, 7.0]), np.array([L / 2, W * 0.42, 0.0])
    f = size[0] / 2 / math.tan(math.radians(95.0) / 2)
    K = np.array([[f, 0, size[0] / 2], [0, f, size[1] / 2], [0, 0, 1]])
    forward = (target - position) / np.linalg.norm(target - position)
    right = np.cross(forward, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    R = np.vstack([right, np.cross(forward, right), forward])
    P = K @ np.hstack([R, (-R @ position).reshape(3, 1)])
    return P[:, [0, 1, 3]] @ np.array([[-1, 0, L], [0, 1, 0], [0, 0, 1.0]])


def _venue_match(server, truth, template, venues, owner='a' * 32):
    """A finished match filmed through `truth`, and the owner's saved venues [(id, template, H, createdAt, extra)]."""
    import json

    import cv2

    from app.vision import pitch

    size = (640, 360)
    for vid, venue_template, H, created, extra in venues:
        (server._venue_dir() / f"{vid}.json").write_text(json.dumps({
            "id": vid, "name": f"Court {venue_template['length']:.0f}x{venue_template['width']:.0f} {vid[:2]}", "template": venue_template,
            "size": list(size), "k1": 0.0, "H": np.linalg.inv(H).reshape(-1).tolist(), "static": True, "rms": 0.1,
            "owner": owner, "createdAt": created, **extra}))
    frame = np.full((size[1], size[0], 3), (40, 140, 40), np.uint8)
    cal = {"H": np.linalg.inv(truth).tolist(), "k1": 0.0, "size": list(size)}
    for line in pitch.line_segments(template, step=0.05):
        pts = pitch.pitch_to_image(cal, line)
        for a, b in zip(pts[:-1], pts[1:]):
            if np.isfinite(a).all() and np.isfinite(b).all():
                cv2.line(frame, tuple(np.round(a).astype(int)), tuple(np.round(b).astype(int)), (235, 235, 235), 2)
    job = 'd' * 32
    directory = server.ROOT / job
    directory.mkdir()
    writer = cv2.VideoWriter(str(directory / "video.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 3.0, size)
    frames = []
    for i in range(60):
        writer.write(frame)
        frames.append({"t": round(i / 3, 3), "frame": i, "scene": 0, "ball": None, "camera": None if i == 0 else [1, 0, 0, 0, 1, 0],
                       "players": [{"id": k, "box": [100 + 90 * k, 200, 110 + 90 * k, 230], "team": k % 2} for k in range(5)]})
    writer.release()
    (directory / "video.avi").rename(directory / "video")
    (directory / "result.json").write_text(json.dumps({"frames": frames, "sampleFps": 3.0, "video": {"width": size[0], "height": size[1], "fps": 3.0}}))
    server.write_status(directory, {"id": job, "status": "completed", "owner": owner, "createdAt": 0})
    return directory


def test_automatic_venue_setup_picks_the_court_that_fits_best(service, monkeypatch):
    import json

    from app.vision import pitch

    server, _ = service
    monkeypatch.setattr(server, "compute_analysis", lambda d: None)
    five, futsal = pitch.normalise_template("five-a-side"), pitch.normalise_template("futsal")
    # Two courts at one facility, same video size; the newer venue is the futsal court.
    venues = [("b" * 32, five, _court_camera(five), 1000.0, {}), ("c" * 32, futsal, _court_camera(futsal), 2000.0, {})]
    directory = _venue_match(server, _court_camera(five), five, venues)  # filmed on the 36 x 24 court
    assert server._auto_venue(directory, {"owner": "a" * 32}) == "b" * 32
    cal = json.loads((directory / "calibration.json").read_text())
    assert cal["template"]["length"] == 36 and cal["auto"] is True and cal["venue"]["lineScore"] >= 0.9


def test_two_venues_that_fit_about_equally_are_left_to_the_reviewer(service, monkeypatch):
    import json

    from app.vision import pitch

    server, _ = service
    monkeypatch.setattr(server, "compute_analysis", lambda d: None)
    five = pitch.normalise_template("five-a-side")
    camera = _court_camera(five)
    venues = [("b" * 32, five, camera, 1000.0, {}), ("c" * 32, five, camera, 2000.0, {"selfScore": 1.0})]
    directory = _venue_match(server, camera, five, venues)
    assert server._auto_venue(directory, {"owner": "a" * 32}) is None
    assert not (directory / "calibration.json").exists()
    job = json.loads((directory / "calibration-job.json").read_text())
    assert job["state"] == "failed" and "both fit this match" in job["error"]


def test_ball_model_status_says_whether_the_site_may_train(service, monkeypatch, tmp_path):
    server, client = service
    monkeypatch.setenv('VISION_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('VISION_SITE_TRAINING', raising=False)
    assert client.get('/ball-model').json()['siteTraining'] is False
    # A refusal reaches a team owner: no server settings or operator documents in it.
    refused = client.post('/ball-model/train', json={}, headers={'x-pitchlens-require-owner': '1'})
    assert refused.status_code == 403
    assert 'VISION_' not in refused.json()['detail'] and 'docs/' not in refused.json()['detail']
    assert 'labels are saved' in refused.json()['detail']
    monkeypatch.setenv('VISION_SITE_TRAINING', '1')
    assert client.get('/ball-model').json()['siteTraining'] is True
    monkeypatch.setenv('VISION_SITE_TRAINING', 'yes')
    assert client.get('/ball-model').json()['siteTraining'] is False


# ── Reruns choose the ball model; kits keep the source order ─────────────
def _player_load_source(server, job, owner='c' * 32):
    import json
    import time as clock

    source = server.ROOT / job
    source.mkdir()
    (source / 'video').write_bytes(b'complete saved video')
    (source / 'result.json').write_text(json.dumps({'teams': [
        {'id': 0, 'label': 'Kit A', 'colour': '#dc2626'}, {'id': 1, 'label': 'Kit B', 'colour': '#22c55e'}]}))
    server.write_status(source, {'id': job, 'owner': owner, 'title': 'Player load', 'status': 'completed', 'createdAt': clock.time(),
                                 'profile': 'general', 'sampleFps': 6, 'video': {'duration': 600}, 'ballSearch': 'none'})
    return {'x-pitchlens-owner': owner}


def _free(server):
    # These tests do not run the analysis: let the next start in.
    server.active = None
    server.cancellations.clear()


def test_a_rerun_can_turn_the_ball_model_on_and_defaults_to_the_source(hosted):
    server, client, submitted = hosted
    job = 'e' * 32
    headers = _player_load_source(server, job)
    url = f'/jobs/{job}/rerun?mode=section&start=30&requestId={1:032x}'
    on = client.post(url + '&search=exhaustive', headers=headers)
    assert on.status_code == 200, on.text
    assert on.json()['ballSearch'] == 'exhaustive' and submitted[-1][2]['ballSearch'] == 'exhaustive'
    # The same request again is the same job; the same ID with other options is refused.
    assert client.post(url + '&search=exhaustive', headers=headers).json()['id'] == on.json()['id']
    assert client.post(url + '&search=none', headers=headers).status_code == 409
    assert client.post(url, headers=headers).status_code == 409  # omitted = the source's "none"
    _free(server)
    copied = client.post(f'/jobs/{job}/rerun?mode=section&start=30&requestId={2:032x}', headers=headers)
    assert copied.status_code == 200 and copied.json()['ballSearch'] == 'none'
    _free(server)
    assert client.post(f'/jobs/{job}/rerun?mode=section&start=30&requestId={3:032x}&search=bogus', headers=headers).status_code == 400
    # A full rerun with the ball model still counts against the full-rerun limit.
    full = client.post(f'/jobs/{job}/rerun?mode=full&start=0&requestId={4:032x}&search=exhaustive', headers=headers)
    assert full.status_code == 200
    import json
    assert json.loads((server.ROOT / full.json()['id'] / 'status.json').read_text())['rerunDigest'].startswith('full:')


def test_a_rerun_keeps_the_source_kit_order(hosted):
    import json

    server, client, submitted = hosted
    job = 'e' * 32
    headers = _player_load_source(server, job)
    response = client.post(f'/jobs/{job}/rerun?mode=section&start=30&requestId={5:032x}', headers=headers)
    saved = json.loads((server.ROOT / response.json()['id'] / 'status.json').read_text())
    assert saved['referenceColours'] == ['#dc2626', '#22c55e'] and saved['sourceJobId'] == job


def test_work_passes_the_reference_kits_to_the_engine(service, monkeypatch):
    server, _ = service
    job = '6' * 32
    directory = server.ROOT / job
    directory.mkdir()
    seen = {}
    monkeypatch.setattr(server.gpu, 'modal_enabled', lambda: False)
    monkeypatch.setattr(server, 'run_video', lambda *a, **kw: seen.update(kw))
    status = {'id': job, 'status': 'processing', 'createdAt': 0, 'sourceJobId': 'e' * 32, 'referenceColours': ['#dc2626', '#22c55e']}
    server.work(directory, status, threading.Event())
    assert seen['reference_colours'] == ['#dc2626', '#22c55e'] and seen['reference_job'] == 'e' * 32
    # An upload has no reference.
    seen.clear()
    server.work(directory, {'id': job, 'status': 'processing', 'createdAt': 0}, threading.Event())
    assert seen['reference_colours'] is None and seen['reference_job'] is None


def test_kits_are_swapped_back_to_the_reference_order():
    import cv2

    from app.vision.engine import align_to_reference

    def lab(h):
        rgb = np.uint8([int(h[i:i + 2], 16) for i in (1, 3, 5)]).reshape(1, 1, 3)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).reshape(3).astype(float)

    red, green = '#dc2626', '#d0ffd0'
    swapped = np.array([lab(green), lab(red)])
    centres, colours = align_to_reference(swapped, [green, red], [red, '#22c55e'])
    assert colours == [red, green] and np.allclose(centres[0], lab(red))
    aligned = np.array([lab(red), lab(green)])
    centres, colours = align_to_reference(aligned, [red, green], [red, '#22c55e'])
    assert colours == [red, green] and centres is aligned
    centres, colours = align_to_reference(swapped, [green, red], None)
    assert colours == [green, red] and centres is swapped


# ── This browser's own stopped upload ─────────────────────────────────────
def test_a_stopped_upload_from_this_browser_is_named_and_resumable(hosted):
    server, client, _ = hosted
    mine, theirs = 'c' * 32, 'd' * 32
    first = '1' * 32
    url = '/jobs?size=8&title=Match&owner={}&requestId={}'
    job = client.post(url.format(mine, first), headers={'Content-Type': 'video/mp4'}).json()
    assert client.put(f"/jobs/{job['id']}/video?offset=0", content=b'0123', headers={'x-pitchlens-owner': mine}).status_code == 200
    # The page was reloaded: a new request ID from the same browser learns it is its own upload.
    again = client.post(url.format(mine, '2' * 32), headers={'Content-Type': 'video/mp4'})
    assert again.status_code == 409
    body = again.json()
    assert body['code'] == 'own-upload' and body['jobId'] == job['id'] and body['receivedBytes'] == 4 and body['expectedBytes'] == 8
    assert '50%' in body['detail']
    # Another browser learns nothing about it.
    other = client.post(url.format(theirs, '3' * 32), headers={'Content-Type': 'video/mp4'})
    assert other.status_code == 409 and 'jobId' not in other.json() and 'code' not in other.json()
    # The same file and options under the first request ID resume the same job.
    resumed = client.post(url.format(mine, first), headers={'Content-Type': 'video/mp4'})
    assert resumed.status_code == 200 and resumed.json()['id'] == job['id'] and resumed.json()['receivedBytes'] == 4


# ── Upload size limit ─────────────────────────────────────────────────────
def test_the_size_limit_is_configurable_and_named_in_the_refusal(hosted, monkeypatch):
    server, client, _ = hosted
    monkeypatch.setattr(server, 'MAX_BYTES', 1024 * 1024)
    assert client.get('/health').json()['maxBytes'] == 1024 * 1024
    refused = client.post(f'/jobs?size={2 * 1024 * 1024}', headers={'Content-Type': 'video/mp4'})
    assert refused.status_code == 413 and refused.json()['detail'] == 'Maximum video size is 1 MB'
    monkeypatch.delenv('VISION_MAX_UPLOAD_MB', raising=False)
    monkeypatch.setenv('VISION_MAX_BYTES', str(3 * 1024 ** 3))
    assert server._max_bytes() == 3 * 1024 ** 3
    for bad in ('-5', '0', 'lots', '1.5e9'):
        monkeypatch.setenv('VISION_MAX_BYTES', bad)
        assert server._max_bytes() == server.DEFAULT_MAX_BYTES == 2048 * 1024 * 1024, bad
    monkeypatch.delenv('VISION_MAX_BYTES')
    assert server._max_bytes() == server.DEFAULT_MAX_BYTES
    # The same limit in MiB (at least 50); VISION_MAX_BYTES wins when both are set.
    monkeypatch.setenv('VISION_MAX_UPLOAD_MB', '500')
    assert server._max_bytes() == 500 * 1024 * 1024
    monkeypatch.setenv('VISION_MAX_UPLOAD_MB', '10')
    assert server._max_bytes() == 50 * 1024 * 1024
    monkeypatch.setenv('VISION_MAX_UPLOAD_MB', 'lots')
    assert server._max_bytes() == server.DEFAULT_MAX_BYTES
    monkeypatch.setenv('VISION_MAX_BYTES', str(1024 ** 3))
    assert server._max_bytes() == 1024 ** 3


# ── Retry is offered only when it can work ────────────────────────────────
def test_retry_is_offered_only_for_a_saved_complete_upload_with_retries_left(hosted, monkeypatch):
    server, client, _ = hosted
    cancelled = client.post('/jobs?size=8', headers={'Content-Type': 'video/mp4'}).json()
    client.put(f"/jobs/{cancelled['id']}/video?offset=0", content=b'0123')
    client.post(f"/jobs/{cancelled['id']}/cancel")
    assert client.get(f"/jobs/{cancelled['id']}").json()['retryable'] is False
    ready, spent, old = '4' * 32, '5' * 32, '6' * 32
    for job, attempts in ((ready, 1), (spent, 3), (old, 1)):
        _stopped_job(server, job, attempts, status='failed')
    server.write_status(server.ROOT / old, {**__import__('json').loads((server.ROOT / old / 'status.json').read_text()), 'createdAt': 0})
    monkeypatch.setattr(server, 'RETENTION_HOURS', 1)
    assert client.get(f'/jobs/{ready}').json()['retryable'] is True
    assert client.get(f'/jobs/{spent}').json()['retryable'] is False
    assert client.get(f'/jobs/{old}').json()['retryable'] is False
    listed = {j['id']: j['retryable'] for j in client.get('/jobs').json()}
    assert listed[ready] is True and listed[spent] is False and listed[cancelled['id']] is False
    # A redeploy did not use up a retry (see retry_job).
    _stopped_job(server, '7' * 32, 3, interruptedBy='shutdown')
    assert client.get(f"/jobs/{'7' * 32}").json()['retryable'] is True


def test_stage_texts_leave_the_action_to_the_buttons(service):
    server, client = service
    job = '8' * 32
    directory = server.ROOT / job
    directory.mkdir()
    server.write_status(directory, {'id': job, 'status': 'processing', 'createdAt': 0})
    assert client.get(f'/jobs/{job}').json()['stage'] == 'Worker stopped before the analysis finished.'
    server.recover_after_restart()
    assert 'Submit' not in client.get(f'/jobs/{job}').json()['stage']


# ── Daily cap ─────────────────────────────────────────────────────────────
def test_the_daily_cap_says_when_analysis_resumes_and_is_not_transient(hosted, monkeypatch):
    import json
    import time as clock

    server, client, _ = hosted
    job = 'e' * 32
    headers = _player_load_source(server, job)
    started = clock.time() - 3600
    (server.ROOT / 'starts.json').write_text(json.dumps([started]))
    monkeypatch.setenv('VISION_MAX_JOBS_PER_DAY', '1')
    for response in (
        client.post('/jobs?size=8', headers={'Content-Type': 'video/mp4', **headers}),
        client.post(f'/jobs/{job}/rerun?mode=section&start=0&requestId={9:032x}', headers=headers),
        client.post('/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ', headers=headers),
    ):
        assert response.status_code == 429
        body = response.json()
        assert body['code'] == 'daily-limit' and abs(body['resetsAt'] - (started + 86400)) <= 1
        assert 'limit of 1 analyses' in body['detail']
    stopped = '4' * 32
    _stopped_job(server, stopped, 1)
    retry = client.post(f'/jobs/{stopped}/retry')
    assert retry.status_code == 429 and retry.json()['code'] == 'daily-limit'


# ── Venues name the match they came from ──────────────────────────────────
def test_venues_list_their_source_match_only_to_their_owner(service):
    import json

    server, client = service
    mine, theirs, job = 'a' * 32, 'b' * 32, 'c' * 32
    (server._venue_dir() / f"{'f' * 32}.json").write_text(json.dumps(
        {'id': 'f' * 32, 'name': 'Tryzub indoor court', 'owner': mine, 'sourceJob': job, 'size': [640, 360], 'createdAt': 1}))
    assert [v['sourceJob'] for v in client.get(f'/venues?owner={mine}').json()] == [job]
    assert client.get(f'/venues?owner={theirs}').json() == []
    assert 'owner' not in client.get(f'/venues?owner={mine}').json()[0]


# ── An upload survives a worker restart ───────────────────────────────────
def test_the_newest_upload_survives_a_restart_and_resumes(hosted, monkeypatch):
    import json

    server, client, _ = hosted
    owner = {'x-pitchlens-owner': 'c' * 32}
    mib = 1024 * 1024
    newer, older = '1' * 32, '2' * 32
    for job, created in ((newer, 200), (older, 100)):
        directory = server.ROOT / job
        directory.mkdir()
        (directory / 'video').write_bytes(b'x' * (9 * mib))
        server.write_status(directory, {'id': job, 'owner': 'c' * 32, 'status': 'uploading', 'createdAt': created,
                                        'expectedBytes': 16 * mib, 'receivedBytes': 8 * mib})
    server.recover_after_restart()
    assert server.active == newer
    assert (server.ROOT / newer / 'video').stat().st_size == 8 * mib
    assert client.get(f'/jobs/{newer}', headers=owner).json()['status'] == 'uploading'
    # The browser resends from where it thinks it was: a chunk it already sent is acknowledged
    # with the server's offset, one that overlaps the end is told where to continue.
    assert client.put(f'/jobs/{newer}/video?offset=0', content=b'y' * 10, headers=owner).json() == {'received': 8 * mib}
    stale = client.put(f'/jobs/{newer}/video?offset={mib}', content=b'y' * (8 * mib), headers=owner)
    assert stale.status_code == 409 and stale.json()['detail'] == f'Expected offset {8 * mib}'
    assert client.put(f'/jobs/{newer}/video?offset={8 * mib}', content=b'y' * 10, headers=owner).json() == {'received': 8 * mib + 10}
    assert json.loads((server.ROOT / older / 'status.json').read_text())['status'] == 'interrupted'
    assert not (server.ROOT / older / 'video').exists()
    # Nobody came back: the abandon release frees the worker.
    monkeypatch.setattr(server, 'UPLOAD_ABANDON_SECONDS', 0)
    server.sweep_stale()
    assert server.active is None
    assert client.get(f'/jobs/{newer}', headers=owner).json()['status'] == 'failed'


# ── Paused phone uploads resume instead of starting again ─────────────────
def _paused_upload(hosted, monkeypatch, data, sent):
    """A resumable upload by owner 'c…', `sent` bytes in, then the phone locks for 6 minutes."""
    server, client, _ = hosted
    clock = {'now': 1000.0}
    monkeypatch.setattr(server.time, 'monotonic', lambda: clock['now'])
    mine = 'c' * 32
    url = f'/jobs?size={len(data)}&title=Match&owner={mine}&requestId={"1" * 32}'
    job = client.post(url, headers={'Content-Type': 'video/mp4'}).json()
    headers = {'x-pitchlens-owner': mine}
    assert client.put(f"/jobs/{job['id']}/video?offset=0", content=data[:sent], headers=headers).status_code == 200
    clock['now'] += 6 * 60
    return job, url, headers, clock


def test_a_paused_upload_survives_polls_and_resumes_where_it_stopped(hosted, monkeypatch):
    server, client, submitted = hosted
    data = FIXTURE.read_bytes()
    half = len(data) // 2
    job, url, headers, _ = _paused_upload(hosted, monkeypatch, data, half)
    # Every poll sweeps stale uploads: none of them is contention.
    client.get('/health')
    client.get(f"/jobs?owner={'c' * 32}")
    assert client.get(f"/jobs/{job['id']}", headers=headers).json()['status'] == 'uploading'
    # Back on the upload page: the same file and options under the same request ID.
    resumed = client.post(url, headers={'Content-Type': 'video/mp4'})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['id'] == job['id'] and resumed.json()['receivedBytes'] == half
    assert client.put(f"/jobs/{job['id']}/video?offset={half}", content=data[half:], headers=headers).json() == {'received': len(data)}
    started = client.post(f"/jobs/{job['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    assert started.json()['status'] == 'processing' and len(submitted) == 1


def test_a_paused_upload_gives_way_to_another_owners_start(hosted, monkeypatch):
    server, client, _ = hosted
    job, _, headers, _ = _paused_upload(hosted, monkeypatch, b'01234567', 4)
    other = client.post(f"/jobs?size=10&owner={'d' * 32}", headers={'Content-Type': 'video/mp4'})
    assert other.status_code == 200, other.text
    assert client.get(f"/jobs/{job['id']}", headers=headers).json()['stage'] == 'Upload stopped before the video was complete.'


def test_the_same_owner_with_other_options_is_told_about_its_paused_upload(hosted, monkeypatch):
    server, client, _ = hosted
    job, _, headers, _ = _paused_upload(hosted, monkeypatch, b'01234567', 4)
    again = client.post(f"/jobs?size=8&title=Other&owner={'c' * 32}&requestId={'2' * 32}", headers={'Content-Type': 'video/mp4'})
    assert again.status_code == 409 and again.json()['code'] == 'own-upload' and again.json()['receivedBytes'] == 4
    assert server.active == job['id']
    assert client.get(f"/jobs/{job['id']}", headers=headers).json()['status'] == 'uploading'


def test_an_upload_paused_past_the_abandon_limit_is_released_by_a_poll(hosted, monkeypatch):
    server, client, _ = hosted
    job, _, headers, clock = _paused_upload(hosted, monkeypatch, b'01234567', 4)
    clock['now'] += server.UPLOAD_ABANDON_SECONDS
    assert client.get(f"/jobs/{job['id']}", headers=headers).json()['stage'] == 'Upload stopped before the video was complete.'
    assert server.active is None


def test_a_retry_is_not_blocked_by_an_upload_paused_for_six_minutes(hosted, monkeypatch):
    server, client, submitted = hosted
    _paused_upload(hosted, monkeypatch, b'01234567', 4)
    stopped = '6' * 32
    _stopped_job(server, stopped, 1)
    response = client.post(f'/jobs/{stopped}/retry')
    assert response.status_code == 200, response.text
    assert server.active == stopped and len(submitted) == 1


def test_health_says_how_long_a_paused_upload_is_kept(service, monkeypatch):
    server, client = service
    monkeypatch.setattr(server, 'UPLOAD_ABANDON_SECONDS', 7200)
    body = client.get('/health').json()
    assert body['uploadIdleSeconds'] == server.UPLOAD_IDLE_SECONDS == 300
    assert body['uploadAbandonSeconds'] == 7200


# ── Team names, title and match date saved for the owner ──────────────────
def _owned_match(server, job='7' * 32, owner='c' * 32):
    directory = server.ROOT / job
    directory.mkdir()
    server.write_status(directory, {'id': job, 'owner': owner, 'status': 'completed', 'stage': 'done',
                                    'progress': 100, 'createdAt': 100.0, 'title': 'IMG_4821'})
    return job, directory, {'x-pitchlens-owner': owner}


def test_team_names_title_and_date_are_saved_for_the_owner(hosted):
    server, client, _ = hosted
    job, directory, mine = _owned_match(server)
    saved = client.post(f'/jobs/{job}/details', headers=mine,
                        json={'names': ['Tekkerz FC', 'PUSHIT Legends'], 'title': 'Tekkerz v PUSHIT', 'matchDate': '2026-10-11'})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {'teamNames': ['Tekkerz FC', 'PUSHIT Legends'], 'title': 'Tekkerz v PUSHIT', 'matchDate': '2026-10-11'}
    one = client.get(f'/jobs/{job}', headers=mine).json()
    assert one['teamNames'] == ['Tekkerz FC', 'PUSHIT Legends'] and one['title'] == 'Tekkerz v PUSHIT' and one['matchDate'] == '2026-10-11'
    listed = client.get(f"/jobs?owner={'c' * 32}").json()
    assert listed[0]['teamNames'] == ['Tekkerz FC', 'PUSHIT Legends'] and listed[0]['title'] == 'Tekkerz v PUSHIT'
    # Keys left out keep their saved value; a date can be cleared.
    client.post(f'/jobs/{job}/details', headers=mine, json={'matchDate': None})
    one = client.get(f'/jobs/{job}', headers=mine).json()
    assert one['matchDate'] is None and one['teamNames'] == ['Tekkerz FC', 'PUSHIT Legends'] and one['title'] == 'Tekkerz v PUSHIT'
    assert 'owner' not in one


def test_another_owner_can_neither_read_nor_change_the_details(hosted):
    server, client, _ = hosted
    job, directory, mine = _owned_match(server)
    client.post(f'/jobs/{job}/details', headers=mine, json={'names': ['A', 'B']})
    theirs = {'x-pitchlens-owner': 'd' * 32}
    assert client.post(f'/jobs/{job}/details', headers=theirs, json={'names': ['X', 'Y']}).status_code == 404
    assert client.get(f'/jobs/{job}', headers=theirs).status_code == 404
    assert client.get(f"/jobs?owner={'d' * 32}").json() == []
    assert client.get(f'/jobs/{job}', headers=mine).json()['teamNames'] == ['A', 'B']


def test_invalid_details_are_refused_and_control_characters_stripped(hosted):
    server, client, _ = hosted
    job, directory, mine = _owned_match(server)

    def post(body):
        return client.post(f'/jobs/{job}/details', headers=mine, json=body)

    for body in ({'names': ['A', 'B', 'C']}, {'names': ['x' * 81, 'B']}, {'names': ['A', '​ ']},
                 {'matchDate': '2026-13-01'}, {'matchDate': '1999-12-31'}, {'matchDate': '2099-01-01'},
                 {'matchDate': '20261011'}, {'title': ''}, {'venue': 'Hall 2'}, {'names': 'A v B'}):
        assert post(body).status_code == 400, body
    big = client.post(f'/jobs/{job}/details', headers={**mine, 'content-type': 'application/json'},
                      content=('{"title": "' + 'x' * 5000 + '"}').encode())
    assert big.status_code == 400
    assert not (directory / 'details.json').exists()
    ok = post({'names': ['Tekkerz\u0000 FC\n', '‮PUSHIT'], 'title': ' Final\t'})
    assert ok.status_code == 200 and ok.json()['teamNames'] == ['Tekkerz FC', 'PUSHIT'] and ok.json()['title'] == 'Final'


def test_a_status_rewrite_during_analysis_keeps_the_details(hosted):
    import json

    server, client, _ = hosted
    job, directory, mine = _owned_match(server)
    client.post(f'/jobs/{job}/details', headers=mine, json={'names': ['A', 'B'], 'title': 'Renamed'})
    # work() keeps the status in memory and rewrites status.json on every update.
    status = json.loads((directory / 'status.json').read_text())
    server.write_status(directory, {**status, 'stage': 'Tracking players', 'progress': 50})
    one = client.get(f'/jobs/{job}', headers=mine).json()
    assert one['teamNames'] == ['A', 'B'] and one['title'] == 'Renamed' and one['stage'] == 'Tracking players'


def test_deleting_a_match_deletes_its_details(hosted):
    server, client, _ = hosted
    job, directory, mine = _owned_match(server)
    client.post(f'/jobs/{job}/details', headers=mine, json={'names': ['A', 'B']})
    assert (directory / 'details.json').is_file()
    assert client.delete(f'/jobs/{job}', headers=mine).status_code == 200
    assert not directory.exists()
    assert client.post(f'/jobs/{job}/details', headers=mine, json={'names': ['A', 'B']}).status_code == 404


# ── Redeploys: a shutdown is recorded before the process can exit ─────────
import errno  # noqa: E402
import gzip  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_NO_HOSTING = (
    "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "VISION_USE_MODAL", "RAILWAY_PUBLIC_DOMAIN", "VISION_PUBLIC_URL",
    "RAILWAY_DEPLOYMENT_ID", "VISION_ALLOW_CPU_FALLBACK", "VISION_GPU_START_SECONDS",
)


@pytest.fixture
def worker(service, monkeypatch, tmp_path):
    """The service with fake weights and its real inference thread; not hosted, no GPU."""
    server, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    for name in _NO_HOSTING:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "gpu_errors", __import__("collections").deque(maxlen=5))
    return server, client


def _hosted_gpu(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "worker.example")


def _queued_job(server, job="6" * 32, owner=None, **extra):
    """A saved upload handed to the inference thread (what begin() writes)."""
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "video").write_bytes(b"saved video")
    status = {"id": job, "title": "Match", "status": "processing", "createdAt": time.time(), "profile": "general",
              "ballSearch": "none", "video": {"duration": 60}, **extra}
    if owner:
        status["owner"] = owner
    server.write_status(directory, status)
    return directory, status


def _start(server, directory, status):
    event = threading.Event()
    with server.lock:
        server.active = status["id"]
        server.cancellations[status["id"]] = event
    thread = threading.Thread(target=server.work, args=(directory, status, event), daemon=True)
    thread.start()
    return event, thread


def _until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def _disk(directory):
    return json.loads((directory / "status.json").read_text())


def _slow_to_notice(after=None):
    """run_video that sees the cancellation a second late (a long inference step)."""

    def run_video(path, output, progress=None, cancelled=lambda: False, **kwargs):
        while not cancelled():
            time.sleep(0.01)
        time.sleep(1)
        if after:
            after(progress)
        raise InterruptedError("Analysis cancelled")

    return run_video


def test_a_redeploy_is_on_disk_before_the_job_thread_reacts(worker, monkeypatch):
    server, client = worker
    monkeypatch.setenv("VISION_SHUTDOWN_WAIT_SECONDS", "0")
    monkeypatch.setattr(server, "run_video", _slow_to_notice())
    directory, status = _queued_job(server)
    _, thread = _start(server, directory, status)
    _until(lambda: status["id"] in server.running and _disk(directory).get("stage") == "Opening video")
    server.stop_jobs()
    # The process may exit (uvicorn re-raises SIGTERM) or be killed right now.
    saved = _disk(directory)
    assert saved["status"] == "interrupted" and saved["interruptedBy"] == "shutdown"
    thread.join(5)
    assert not thread.is_alive() and _disk(directory)["interruptedBy"] == "shutdown"
    monkeypatch.setattr(server, "pool", SimpleNamespace(submit=lambda *args: None))
    retried = client.post(f"/jobs/{status['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert _disk(directory).get("attempts", 1) == 1


def test_progress_after_a_shutdown_does_not_overwrite_its_record(worker, monkeypatch):
    server, _ = worker
    monkeypatch.setenv("VISION_SHUTDOWN_WAIT_SECONDS", "0")
    monkeypatch.setattr(server, "run_video", _slow_to_notice(after=lambda progress: progress(stage="x", progress=50)))
    directory, status = _queued_job(server)
    _, thread = _start(server, directory, status)
    _until(lambda: status["id"] in server.running)
    server.stop_jobs()
    thread.join(5)
    saved = _disk(directory)
    assert (saved["status"], saved["interruptedBy"], saved["stage"]) == ("interrupted", "shutdown", server.RESTARTED)


def test_an_error_caused_by_the_shutdown_is_not_a_failure(worker, monkeypatch):
    server, _ = worker

    def run_video(path, output, cancelled, **kwargs):
        while not cancelled():
            time.sleep(0.01)
        raise RuntimeError("Connection reset by peer")

    monkeypatch.setattr(server, "run_video", run_video)
    directory, status = _queued_job(server)
    event, thread = _start(server, directory, status)
    _until(lambda: status["id"] in server.running)
    monkeypatch.setattr(server, "stopping", True)  # stop_jobs has not recorded it yet
    event.set()
    thread.join(5)
    saved = _disk(directory)
    assert saved["status"] == "interrupted" and saved["interruptedBy"] == "shutdown"


def test_a_gpu_error_during_a_redeploy_waits_for_the_cancellation(worker, monkeypatch):
    server, _ = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setattr(server, "GPU_ERROR_GRACE", 2)

    def cut(*args, **kwargs):  # uvicorn closed the GPU's video download at its graceful timeout
        raise server.gpu.GPUUnavailable("Connection broken: IncompleteRead")

    monkeypatch.setattr(server.gpu, "run_on_modal", cut)
    directory, status = _queued_job(server)
    event = threading.Event()

    def shutdown():  # the lifespan shutdown, a moment later
        server.stopping = True
        event.set()

    threading.Timer(0.5, shutdown).start()
    server.work(directory, status, event)
    saved = _disk(directory)
    assert saved["status"] == "interrupted" and saved["interruptedBy"] == "shutdown"
    # Without a shutdown it is the GPU's error, after the grace.
    monkeypatch.setattr(server, "stopping", False)
    directory, status = _queued_job(server, "7" * 32)
    began = time.monotonic()
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert saved["status"] == "failed" and saved["stage"].startswith("GPU is unavailable")
    assert time.monotonic() - began >= 2


def test_a_job_left_by_an_earlier_deployment_was_a_redeploy(worker, monkeypatch):
    server, client = worker

    def left(job, deployment):
        directory = server.ROOT / job
        directory.mkdir()
        server.write_status(directory, {"id": job, "status": "processing", "createdAt": 0,
                                        **({"deployment": deployment} if deployment else {})})
        return directory

    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "new")
    old, same, unknown = left("1" * 32, "old"), left("2" * 32, "new"), left("3" * 32, None)
    server.recover_after_restart()
    assert _disk(old)["interruptedBy"] == "shutdown"  # killed before it could record it
    assert "interruptedBy" not in _disk(same)  # a crash restart of the same deployment counts
    assert "interruptedBy" not in _disk(unknown)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID")
    unset = left("4" * 32, "old")
    server.recover_after_restart()
    assert _disk(unset)["status"] == "interrupted" and "interruptedBy" not in _disk(unset)
    assert "deployment" not in client.get(f"/jobs/{'1' * 32}").json()
    # Every start records the deployment running it.
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "next")
    monkeypatch.setattr(server, "pool", SimpleNamespace(submit=lambda *args: None))
    (old / "video").write_bytes(b"saved")
    server.write_status(old, {**_disk(old), "video": {"duration": 60}, "ballSearch": "none"})
    assert client.post(f"/jobs/{'1' * 32}/retry").status_code == 200
    assert _disk(old)["deployment"] == "next"
    assert "deployment" not in client.get(f"/jobs/{'1' * 32}").json()


@pytest.mark.skipif(sys.platform != "linux", reason="signal handling is checked on Linux")
def test_sigterm_to_a_worker_that_is_not_pid_1_records_the_shutdown(tmp_path):
    import os
    import signal
    import socket
    import subprocess
    import urllib.request

    backend = Path(__file__).resolve().parents[1]
    with socket.socket() as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        port = probe_socket.getsockname()[1]
    launcher = tmp_path / "launch.py"
    launcher.write_text(
        "import time\n"
        "import uvicorn\n"
        "from app.vision import server\n\n"
        "def run_video(path, output, progress=None, cancelled=lambda: False, **kwargs):\n"
        "    while not cancelled():\n"
        "        time.sleep(0.01)\n"
        "    time.sleep(1)\n"
        "    raise InterruptedError('Analysis cancelled')\n\n"
        "server.run_video = run_video\n"
        f"uvicorn.run(server.app, host='127.0.0.1', port={port}, timeout_graceful_shutdown=20, log_level='warning')\n"
    )
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    env = {k: v for k, v in os.environ.items() if k not in _NO_HOSTING}
    env.update(VISION_DATA_DIR=str(tmp_path / "data"), VISION_SERVICE_TOKEN="test-token", VISION_MODEL_PATH=str(model),
               VISION_BALL_MODEL_PATH=str(model), VISION_USE_MODAL="0", PYTHONPATH=str(backend), YOLO_OFFLINE="1")
    with (tmp_path / "worker.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, str(launcher)], cwd=backend, env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    auth = {"Authorization": "Bearer test-token"}

    def call(path, data=None, headers=None):
        request = urllib.request.Request(base + path, data=data, headers={**auth, **(headers or {})})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    try:
        for _ in range(600):
            try:
                call("/healthz")
                break
            except OSError:
                assert process.poll() is None, (tmp_path / "worker.log").read_text()
                time.sleep(0.1)
        video = (backend / "tests" / "fixtures" / "vfr-30-15.mp4").read_bytes()
        job = call("/jobs?search=none", data=video, headers={"Content-Type": "video/mp4"})["id"]
        _until(lambda: call(f"/jobs/{job}")["stage"] == "Opening video", 30)
        process.send_signal(signal.SIGTERM)
        process.wait(60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)
    saved = json.loads((tmp_path / "data" / job / "status.json").read_text())
    assert saved["status"] == "interrupted" and saved["interruptedBy"] == "shutdown", saved


# ── A full volume never keeps the worker from starting ────────────────────
def test_a_full_volume_does_not_stop_the_worker_from_starting(service, monkeypatch):
    server, _ = service
    done, stuck, upload = "1" * 32, "2" * 32, "3" * 32
    for job, state in ((done, "completed"), (stuck, "processing"), (upload, "uploading")):
        directory = server.ROOT / job
        directory.mkdir()
        (directory / "video").write_bytes(b"partial video")
        server.write_status(directory, {"id": job, "status": state, "createdAt": 0})
    (server.ROOT / done / "calibration-job.json").write_text(json.dumps({"state": "processing"}))
    (server.ROOT / stuck / "status.tmp").write_text("{")
    resumable = server.ROOT / ("5" * 32)  # a chunked upload a phone was sending
    resumable.mkdir()
    (resumable / "video").write_bytes(b"x" * 100)
    server.write_status(resumable, {"id": "5" * 32, "status": "uploading", "createdAt": 0,
                                    "expectedBytes": 1000, "receivedBytes": 90})

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(server, "write_status", full)
    monkeypatch.setattr(server, "_write_json", full)
    server.recover_after_restart()
    server.recover_calibrations()
    # Space is freed even though nothing could be recorded.
    assert not (server.ROOT / stuck / "status.tmp").exists()
    assert not (server.ROOT / upload / "video").exists()
    # An upload whose progress could not be saved is never resumed (nor the worker reserved).
    assert server.active is None and not (resumable / "video").exists()
    with TestClient(server.app) as client:  # the startup handlers run again
        assert client.get("/healthz").status_code == 200
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get(f"/jobs/{stuck}").json()["status"] == "interrupted"


def test_a_restart_during_a_youtube_download_deletes_the_partial_download(service):
    server, _ = service
    job = "4" * 32
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "download.f137.mp4.part").write_bytes(b"x" * 1024)
    server.write_status(directory, {"id": job, "status": "uploading", "createdAt": 0})
    server.recover_after_restart()
    assert not list(directory.glob("download.*"))
    assert _disk(directory)["status"] == "interrupted"
    # Retention deletes one left by an older worker too.
    (directory / "download.f137.mp4.part").write_bytes(b"x" * 1024)
    server.expire_footage(directory, _disk(directory))
    assert not list(directory.glob("download.*"))


# ── GPU runs: Cancel, shutdown and start deadline while the GPU is silent ──
class _FakeModal:
    """app.vision.modal_app without Modal. app.run() records enter and exit and,
    like Modal, refuses a second app.run() while one is running; the GPU sends
    nothing until `gate` is set."""

    def __init__(self, items=(), gate=False, error=None):
        self.items, self.error = list(items), error
        self.gate = threading.Event()
        if not gate:
            self.gate.set()
        self.running, self.entered, self.exited = False, 0, 0
        self.app = SimpleNamespace(run=self.run)
        self.analyse = SimpleNamespace(remote_gen=self.remote_gen)

    @contextmanager
    def run(self):
        if self.running:
            raise RuntimeError("App is already running")
        self.running = True
        self.entered += 1
        try:
            yield
        finally:
            self.running = False
            self.exited += 1

    def remote_gen(self, *args):
        self.gate.wait()
        for item in self.items:
            yield dict(item)
        if self.error:
            raise self.error


RESULT = {"result_gz": gzip.compress(b'{"frames": []}')}


def test_cancel_frees_the_worker_while_the_gpu_is_still_starting(worker, monkeypatch, tmp_path):
    server, client = worker
    _hosted_gpu(monkeypatch)
    fake = _FakeModal([RESULT], gate=True)  # scheduling, cold start: nothing arrives
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    owner = "c" * 32
    directory, status = _queued_job(server, owner=owner)
    _, thread = _start(server, directory, status)
    _until(lambda: fake.running and _disk(directory).get("stage") == "Starting a GPU")
    began = time.monotonic()
    assert client.post(f"/jobs/{status['id']}/cancel", headers={"x-pitchlens-owner": owner}).json() == {"requested": True}
    thread.join(2)
    assert not thread.is_alive() and time.monotonic() - began < 2
    assert _disk(directory)["status"] == "cancelled" and server.active is None
    assert fake.exited == 1 and not fake.running  # the ephemeral app (and so the GPU) stopped
    other = "d" * 32
    reserved = client.post("/jobs?size=10", headers={"Content-Type": "video/mp4", "x-pitchlens-owner": other})
    assert reserved.status_code == 200, reserved.text
    fake.gate.set()
    server.gpu.run_on_modal("e" * 32, tmp_path / "result.json", "tok", lambda **kw: None, lambda: False, "general", 3)
    assert fake.entered == 2 and (tmp_path / "result.json").read_text() == '{"frames": []}'


def test_a_gpu_that_never_arrives_gives_way(worker, monkeypatch):
    server, _ = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_GPU_START_SECONDS", "1")
    fake = _FakeModal([RESULT], gate=True)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    cpu = []
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    directory, status = _queued_job(server, "1" * 32)
    _, thread = _start(server, directory, status)
    thread.join(10)
    assert not thread.is_alive(), "the worker is still waiting for a GPU"
    saved = _disk(directory)
    assert saved["status"] == "failed" and saved["stage"].startswith("GPU is unavailable")
    assert "No GPU became available in time" in saved["error"] and not cpu and not fake.running
    assert server.active is None
    # A section (a short test) runs on the CPU instead.
    directory, status = _queued_job(server, "2" * 32, maxSeconds=20)
    server.work(directory, status, threading.Event())
    assert cpu == [1] and _disk(directory)["status"] == "completed" and _disk(directory)["engine"] == "cpu"
    fake.gate.set()


def test_an_engine_crash_on_the_gpu_is_not_rerun_on_the_cpu(worker, monkeypatch):
    server, _ = worker
    _hosted_gpu(monkeypatch)
    crash = {"error": "KeyError: 'ballCandidates'", "user": False, "engine": True}
    fake = _FakeModal([{"stage": "Detecting players", "progress": p} for p in (10, 50, 93)] + [crash])
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    cpu = []
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    for job, extra in (("1" * 32, {"maxSeconds": 20}), ("2" * 32, {})):
        directory, status = _queued_job(server, job, **extra)
        server.work(directory, status, threading.Event())
        saved = _disk(directory)
        assert (saved["status"], saved["stage"]) == ("failed", "Vision processing failed. Check the worker log.")
        assert "refusedBy" not in saved
    assert not cpu


def test_weights_the_gpu_cannot_copy_still_fall_back_to_the_cpu(worker, monkeypatch):
    server, _ = worker
    _hosted_gpu(monkeypatch)
    fake = _FakeModal([{"stage": "GPU: preparing football models", "progress": 1},
                       {"error": "Model unavailable on GPU: x", "user": False}])
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", fake)
    cpu = []
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    directory, status = _queued_job(server, maxSeconds=20)
    server.work(directory, status, threading.Event())
    assert cpu == [1] and _disk(directory)["status"] == "completed"


def _full_volume_for(monkeypatch, name):
    """Writing `name` fails halfway with ENOSPC."""
    real = Path.write_bytes

    def write_bytes(self, data):
        if self.name == name:
            real(self, data[: len(data) // 2])
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)


def test_a_full_volume_after_a_gpu_run_is_not_the_gpus_fault(worker, monkeypatch, tmp_path):
    server, _ = worker
    _hosted_gpu(monkeypatch)
    monkeypatch.setenv("VISION_ALLOW_CPU_FALLBACK", "1")
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", _FakeModal([RESULT]))
    _full_volume_for(monkeypatch, "result.tmp")
    with pytest.raises(OSError) as raised:
        server.gpu.run_on_modal("e" * 32, tmp_path / "result.json", "tok", lambda **kw: None, lambda: False, "general", 3)
    assert raised.value.errno == errno.ENOSPC and not isinstance(raised.value, server.gpu.GPUUnavailable)
    assert not (tmp_path / "result.tmp").exists()
    cpu = []
    monkeypatch.setattr(server, "run_video", lambda *a, **k: cpu.append(1))
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert (saved["status"], saved["stage"]) == ("failed", server.OUT_OF_STORAGE)
    assert not cpu and not (directory / "result.tmp").exists()


def test_a_full_volume_during_a_cpu_analysis_leaves_no_partial_result(worker, monkeypatch):
    server, client = worker

    def run_video(path, output, **kwargs):
        Path(output).with_suffix(".tmp").write_text('{"frames": [')
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(server, "run_video", run_video)
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert (saved["status"], saved["stage"]) == ("failed", server.OUT_OF_STORAGE)
    assert not (directory / "result.tmp").exists() and server.active is None
    body = client.get(f"/jobs/{status['id']}").json()
    assert body["retryable"] and "error" not in body


def test_a_cut_gpu_stream_is_still_gpu_unavailable_and_a_cancel_still_cancels(worker, monkeypatch, tmp_path):
    requests = pytest.importorskip("requests")
    server, _ = worker
    _hosted_gpu(monkeypatch)
    cut = requests.ConnectionError("Connection aborted.")
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", _FakeModal([{"stage": "fetching", "progress": 1}], error=cut))
    with pytest.raises(server.gpu.GPUUnavailable, match="Connection aborted"):
        server.gpu.run_on_modal("e" * 32, tmp_path / "result.json", "tok", lambda **kw: None, lambda: False, "general", 3)

    def cancelled_by_user(*args, **kwargs):
        raise InterruptedError("Analysis cancelled")

    monkeypatch.setattr(server, "run_video", cancelled_by_user)
    monkeypatch.delenv("MODAL_TOKEN_ID")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN")
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    assert _disk(directory)["status"] == "cancelled"


def test_progress_errors_and_leaving_the_app_keep_their_own_meaning(tmp_path, monkeypatch):
    from app.vision import gpu

    monkeypatch.setattr(gpu, "public_base", lambda: "https://worker.example")
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", _FakeModal([{"stage": "x", "progress": 5}, RESULT]))

    def broken_progress(**kw):
        raise KeyError("stage")

    with pytest.raises(KeyError):
        gpu.run_on_modal("e" * 32, tmp_path / "r.json", "tok", broken_progress, lambda: False, "general", 3)
    refused = _FakeModal([RESULT])

    @contextmanager
    def no_auth():
        raise RuntimeError("Token missing or invalid")
        yield  # pragma: no cover

    refused.app = SimpleNamespace(run=no_auth)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", refused)
    with pytest.raises(gpu.GPUUnavailable, match="Token missing"):
        gpu.run_on_modal("e" * 32, tmp_path / "r.json", "tok", lambda **kw: None, lambda: False, "general", 3)
    # A Cancel stays a Cancel even when stopping the app fails.
    stuck = _FakeModal([RESULT], gate=True)

    @contextmanager
    def failing_exit():
        try:
            yield
        finally:
            raise RuntimeError("Could not stop the app")

    stuck.app = SimpleNamespace(run=failing_exit)
    monkeypatch.setitem(sys.modules, "app.vision.modal_app", stuck)
    with pytest.raises(InterruptedError):
        gpu.run_on_modal("e" * 32, tmp_path / "r.json", "tok", lambda **kw: None, lambda: True, "general", 3)
    stuck.gate.set()


def test_the_gpu_reports_progress_while_it_fetches_a_large_video(tmp_path, monkeypatch):
    import http.server

    pytest.importorskip("modal")
    from app.vision import modal_app

    chunk = 4 * 1024 * 1024

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(3 * chunk))
            self.end_headers()
            for n in range(3):
                if n:
                    time.sleep(1.1)
                self.wfile.write(b"\0" * chunk)
                self.wfile.flush()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_GPU_WORKDIR", str(tmp_path / "job"))

    def run_video(video, output, **kwargs):
        Path(output).write_text('{"frames": []}')

    monkeypatch.setattr("app.vision.engine.run_video", run_video)
    try:
        items = []
        for item in modal_app.analyse.local(f"http://127.0.0.1:{httpd.server_port}/jobs/x/video", "tok", "general", 3,
                                            ball_search="none"):
            items.append(item)
            if "result_gz" in item:
                break
    finally:
        httpd.shutdown()
    fetching = [i for i in items if str(i.get("stage", "")).startswith("GPU: fetching the video (")]
    assert len(fetching) >= 2 and "result_gz" in items[-1]
    assert (tmp_path / "job" / "video").stat().st_size == 3 * chunk


# ── A GPU refusal costs neither the daily cap nor a retry ──────────────────
def _uploaded(server, client, submitted, owner):
    data = FIXTURE.read_bytes()
    headers = {"x-pitchlens-owner": owner}
    job = client.post(f"/jobs?size={len(data)}&search=none", headers={"Content-Type": "video/mp4", **headers}).json()["id"]
    assert client.put(f"/jobs/{job}/video?offset=0", content=data, headers=headers).status_code == 200
    assert client.post(f"/jobs/{job}/start", headers=headers).status_code == 200
    _drain(submitted)
    return job, headers


def _drain(submitted):
    while submitted:  # what the inference thread does
        fn, *args = submitted.pop(0)
        fn(*args)


def test_gpu_refusals_use_neither_the_daily_cap_nor_the_retries(hosted, monkeypatch):
    server, client, submitted = hosted
    _hosted_gpu(monkeypatch)
    monkeypatch.setattr(server, "GPU_ERROR_GRACE", 0)
    monkeypatch.setenv("VISION_MAX_JOBS_PER_DAY", "3")

    def outage(*args, **kwargs):  # a bad token or a spend limit: no GPU second used
        raise server.gpu.GPUUnavailable("Workspace billing cycle spend limit reached")

    monkeypatch.setattr(server.gpu, "run_on_modal", outage)
    ran = []
    monkeypatch.setattr(server, "run_video", lambda *a, **k: ran.append(1))
    job, headers = _uploaded(server, client, submitted, "c" * 32)
    for attempt in range(4):
        body = client.get(f"/jobs/{job}", headers=headers).json()
        assert body["status"] == "failed" and body["stage"].startswith("GPU is unavailable")
        assert body["retryable"] and body.get("attempts", 1) == 1 and "countedAt" not in body
        assert server._recent_starts(time.time()) == []
        if attempt < 3:
            assert client.post(f"/jobs/{job}/retry", headers=headers).status_code == 200
            _drain(submitted)
    assert not ran
    monkeypatch.setattr(server.gpu, "run_on_modal", lambda *a, **k: None)
    other = {"x-pitchlens-owner": "d" * 32}
    assert client.post("/jobs?size=10", headers={"Content-Type": "video/mp4", **other}).status_code == 200


def test_a_gpu_that_sent_progress_keeps_its_start_and_counts_the_attempt(hosted, monkeypatch):
    server, client, submitted = hosted
    _hosted_gpu(monkeypatch)
    monkeypatch.setattr(server, "GPU_ERROR_GRACE", 0)

    def dropped(job_id, output, token, progress, **kwargs):
        progress(stage="GPU · fetching the video", progress=1)
        raise server.gpu.GPUUnavailable("Connection lost")

    monkeypatch.setattr(server.gpu, "run_on_modal", dropped)
    job, headers = _uploaded(server, client, submitted, "c" * 32)
    saved = _disk(server.ROOT / job)
    assert saved["status"] == "failed" and "refusedBy" not in saved
    assert len(server._recent_starts(time.time())) == 1
    assert client.post(f"/jobs/{job}/retry", headers=headers).status_code == 200
    assert _disk(server.ROOT / job)["attempts"] == 2


def test_a_worker_without_a_gpu_gives_the_start_back(hosted, monkeypatch):
    server, client, submitted = hosted
    for name in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "worker.example")
    # Accepted before the Modal tokens were removed (a redeploy keeps an upload in
    # progress); POST /jobs refuses a new full match here (tested below).
    monkeypatch.setattr(server, "full_match_ready", lambda: True)
    job, headers = _uploaded(server, client, submitted, "c" * 32)
    saved = _disk(server.ROOT / job)
    assert saved["stage"].startswith("GPU processing is not configured") and saved["refusedBy"] == "config"
    assert server._recent_starts(time.time()) == []
    # A retry after a redeploy is a new run: it is counted once.
    stopped = {k: v for k, v in saved.items() if k != "refusedBy"}
    server.write_status(server.ROOT / job, {**stopped, "status": "interrupted", "interruptedBy": "shutdown"})
    assert client.post(f"/jobs/{job}/retry", headers=headers).status_code == 200
    assert len(server._recent_starts(time.time())) == 1


# ── A worker that cannot run full matches says so before the upload ───────
FULL_MATCH_UNAVAILABLE = {
    "detail": "Full-match analysis is not set up on this server yet. A 20-second test still works.",
    "code": "full-match-unavailable",
}


def _cpu_only_host(monkeypatch):
    """A Railway worker set up as DEPLOY-VISION.md section 1 alone: no Modal tokens."""
    for name in (*_NO_HOSTING, "VISION_DEVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "worker.example")


def test_a_worker_that_cannot_run_full_matches_refuses_them_before_the_upload(hosted, monkeypatch):
    import re

    server, client, submitted = hosted
    _cpu_only_host(monkeypatch)
    assert client.get("/health").json()["fullMatch"] is False
    owner = "c" * 32
    headers = {"Content-Type": "video/mp4", "x-pitchlens-owner": owner}
    for url in (f"/jobs?size=14932048&diagnostic=false&requestId={'1' * 32}&owner={owner}", "/jobs?diagnostic=false"):
        refused = client.post(url, headers=headers, content=b"video bytes")
        assert (refused.status_code, refused.json()) == (503, FULL_MATCH_UNAVAILABLE)
    youtube = client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ", headers=headers)
    assert (youtube.status_code, youtube.json()) == (503, FULL_MATCH_UNAVAILABLE)
    # No match, no reservation, no start counted.
    assert not [p for p in server.ROOT.iterdir() if re.fullmatch(r"[a-f0-9]{32}", p.name)]
    starts = server.ROOT / server.STARTS
    assert not starts.exists() or json.loads(starts.read_text()) == []
    assert server.active is None and not submitted
    # A stopped full match is not retried, nor a saved match re-analysed in full.
    full = "4" * 32
    _stopped_job(server, full, 1, owner=owner, status="failed")
    retried = client.post(f"/jobs/{full}/retry", headers=headers)
    assert (retried.status_code, retried.json()) == (503, FULL_MATCH_UNAVAILABLE)
    assert _disk(server.ROOT / full)["status"] == "failed"
    source = "a" * 32
    _rerun_chain_job(server, source, time.time())
    rerun = client.post(f"/jobs/{source}/rerun?mode=full&start=0&requestId={'2' * 32}", headers=headers)
    assert (rerun.status_code, rerun.json()) == (503, FULL_MATCH_UNAVAILABLE)
    assert sorted(p.name for p in server.ROOT.iterdir() if re.fullmatch(r"[a-f0-9]{32}", p.name)) == [full, source]
    assert server._recent_starts(time.time()) == [] and server.active is None and not submitted
    # A 20-second test still runs here (on the CPU).
    test = client.post(f"/jobs?size=10&diagnostic=true&owner={owner}", headers=headers)
    assert test.status_code == 200 and server.active == test.json()["id"]


def test_full_matches_are_ready_with_a_gpu_or_the_operators_cpu_choice(worker, monkeypatch):
    server, client = worker
    monkeypatch.delenv("VISION_DEVICE", raising=False)
    assert client.get("/health").json()["fullMatch"] is True  # a local worker
    for setting in (_hosted_gpu, lambda m: m.setenv("VISION_ALLOW_CPU_FALLBACK", "1"), lambda m: m.setenv("VISION_DEVICE", "cuda")):
        with monkeypatch.context() as m:
            _cpu_only_host(m)
            setting(m)
            assert client.get("/health").json()["fullMatch"] is True


def test_a_self_hosted_cuda_worker_with_a_public_url_runs_full_matches(worker, monkeypatch):
    server, client = worker
    monkeypatch.setenv("VISION_DEVICE", "cuda")
    monkeypatch.setenv("VISION_PUBLIC_URL", "https://gpu-box.example")

    def run_video(path, output, **kwargs):
        Path(output).write_text('{"frames": []}')

    monkeypatch.setattr(server, "run_video", run_video)
    directory, status = _queued_job(server)
    server.work(directory, status, threading.Event())
    saved = _disk(directory)
    assert saved["status"] == "completed" and "refusedBy" not in saved
    assert client.get("/health").json()["fullMatch"] is True


# ── Out of storage: refused before a match is created ─────────────────────
def test_out_of_storage_is_refused_before_a_match_is_created(hosted, monkeypatch):
    import re

    server, client, _ = hosted
    free = {"bytes": 100 * 2**20}
    monkeypatch.setattr(server.shutil, "disk_usage",
                        lambda path: SimpleNamespace(total=5 * 2**30, used=5 * 2**30 - free["bytes"], free=free["bytes"]))
    owner = "c" * 32
    headers = {"Content-Type": "video/mp4", "x-pitchlens-owner": owner}
    url = f"/jobs?size=1000&requestId={'1' * 32}&owner={owner}"
    for _ in range(2):  # the browser resends with the same request ID
        refused = client.post(url, headers=headers)
        assert refused.status_code == 507
        assert refused.json() == {"detail": "The analysis server is out of storage. Try again later.", "code": "out-of-storage"}
    assert not [p for p in server.ROOT.iterdir() if re.fullmatch(r"[a-f0-9]{32}", p.name)]
    assert client.get("/jobs").json() == [] and server.active is None
    assert client.post("/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ").json()["code"] == "out-of-storage"
    # The browser resuming its own reservation is never refused for the space its upload uses.
    free["bytes"] = 5 * 2**30
    resumable = f"/jobs?size=1000&requestId={'2' * 32}&owner={owner}"
    job = client.post(resumable, headers=headers).json()["id"]
    assert client.put(f"/jobs/{job}/video?offset=0", content=b"x" * 500, headers=headers).status_code == 200
    free["bytes"] = 2**20
    resumed = client.post(resumable, headers=headers)
    assert resumed.status_code == 200 and resumed.json()["id"] == job


# ── DELETE never stalls the event loop behind another match's analytics ───
def test_delete_waits_for_analytics_without_holding_the_job_lock(hosted):
    server, client, _ = hosted
    other = "a" * 32
    directory = server.ROOT / other
    directory.mkdir()
    server.write_status(directory, {"id": other, "status": "completed", "createdAt": 0})
    upload = client.post("/jobs?size=20", headers={"Content-Type": "video/mp4"}).json()["id"]
    held, release = threading.Event(), threading.Event()

    def analytics():  # the first report view of a 90-minute match
        with server.post_lock:
            held.set()
            release.wait(10)

    threading.Thread(target=analytics, daemon=True).start()
    held.wait(5)
    answer = {}
    deleting = threading.Thread(target=lambda: answer.update(r=client.delete(f"/jobs/{other}")), daemon=True)
    deleting.start()
    time.sleep(0.3)
    try:
        assert deleting.is_alive()
        assert server.lock.acquire(timeout=0.5)
        server.lock.release()
        began = time.monotonic()
        assert client.put(f"/jobs/{upload}/video?offset=0", content=b"0123456789").status_code == 200
        assert time.monotonic() - began < 1
    finally:
        release.set()
    deleting.join(5)
    assert answer["r"].status_code == 200 and not directory.exists()
    assert client.delete(f"/jobs/{upload}").status_code == 409


def test_the_analysis_opens_when_only_its_cache_cannot_be_saved(service, monkeypatch):
    server, client = service
    job = "a" * 32
    directory = server.ROOT / job
    directory.mkdir()
    (directory / "result.json").write_text('{"frames": []}')
    server.write_status(directory, {"id": job, "status": "completed", "createdAt": 0})
    monkeypatch.setattr("app.vision.analytics.prepare", lambda result, calibration, direction: {"base": True})
    monkeypatch.setattr("app.vision.analytics.finish", lambda base, review: {"stats": {"possession": None}})
    real = server._write_json

    def full(path, data):
        if path.name == "analysis.json":
            path.with_suffix(".tmp").write_text("{")
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, data)

    monkeypatch.setattr(server, "_write_json", full)
    response = client.get(f"/jobs/{job}/analysis")
    assert response.status_code == 200 and response.json()["stats"] == {"possession": None}
    assert not (directory / "analysis.tmp").exists()


# ── Operator view and logs ─────────────────────────────────────────────────
@contextmanager
def _ops_records(caplog):
    from app.vision import server

    server.ops_log.addHandler(caplog.handler)
    try:
        yield lambda: [r for r in caplog.records if r.name == "pitchlens"]
    finally:
        server.ops_log.removeHandler(caplog.handler)


def test_the_operator_view_needs_the_token_and_reveals_no_owner_keys(worker, monkeypatch):
    server, client = worker
    _hosted_gpu(monkeypatch)
    assert client.get("/ops", headers={"Authorization": ""}).status_code == 401

    def outage(*args, **kwargs):
        raise server.gpu.GPUUnavailable("Workspace spend limit reached")

    monkeypatch.setattr(server.gpu, "run_on_modal", outage)
    owner = "c" * 32
    directory, status = _queued_job(server, owner=owner, uploadRequestId="1" * 32, uploadRequestDigest="f" * 64)
    server.work(directory, status, threading.Event())
    body = client.get("/ops").json()
    assert set(body) == {"active", "dailyStarts", "trainingStarts", "training", "storage", "jobs", "gpu",
                         "recentProblems", "release"}
    assert body["active"] is None and body["jobs"] == 1 and body["dailyStarts"]["cap"] == 30
    assert set(body["storage"]) == {"freeBytes", "totalBytes", "low"}
    assert body["gpu"]["enabled"] and body["gpu"]["publicUrl"]
    assert body["gpu"]["recentErrors"][-1]["job"] == status["id"]
    assert "spend limit" in body["gpu"]["recentErrors"][-1]["error"]
    problem = body["recentProblems"][0]
    assert problem["id"] == status["id"] and problem["refusedBy"] == "gpu"
    assert "GPUUnavailable: Workspace spend limit reached" == problem["error"]
    text = json.dumps(body)
    assert owner not in text and "1" * 32 not in text and "f" * 64 not in text
    mine = {"x-pitchlens-owner": owner}
    for entry in (client.get(f"/jobs/{status['id']}", headers=mine).json(), *client.get("/jobs", headers=mine).json()):
        assert not {"error", "owner", "uploadRequestId", "uploadRequestDigest", "countedAt"} & set(entry)


def test_a_low_volume_is_shown_and_logged(worker, monkeypatch, caplog):
    server, client = worker
    monkeypatch.setattr(server.shutil, "disk_usage", lambda path: SimpleNamespace(total=5 * 2**30, used=5 * 2**30, free=8 * 2**20))
    assert client.get("/ops").json()["storage"]["low"] is True
    with _ops_records(caplog) as records:
        server.start_volume_watch()
        low = [r for r in records() if r.levelno == logging.WARNING]
    assert len(low) == 1 and low[0].getMessage() == "Volume low: 8 MB free of 5120 MB"


def test_each_job_logs_one_line_when_it_ends_without_owner_keys(worker, monkeypatch, caplog):
    server, _ = worker
    monkeypatch.setattr(server, "run_video", lambda path, output, **kw: Path(output).write_text("{}"))
    owner = "c" * 32
    directory, status = _queued_job(server, owner=owner)
    with _ops_records(caplog) as records:
        server.work(directory, status, threading.Event())
        lines = [r.getMessage() for r in records()]
    done = [line for line in lines if "event=completed" in line]
    assert len(done) == 1 and f"job={status['id']}" in done[0] and "engine=cpu" in done[0]
    assert any("event=start" in line for line in lines) and not any(owner in line for line in lines)


def test_owner_keys_never_reach_the_access_log():
    from app.vision import server

    access = logging.getLogger("uvicorn.access")
    redact = next(f for f in access.filters if isinstance(f, server.RedactOwnerKeys))
    key = "0123456789abcdef0123456789abcdef"
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", f"/jobs?owner={key}&x=1", "1.1", 200), None)
    assert redact.filter(record)
    assert "owner=[redacted]&x=1" in record.getMessage() and key not in record.getMessage()
    odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", (f"owner={key}",), None)
    assert redact.filter(odd) and odd.args == (f"owner={key}",)
