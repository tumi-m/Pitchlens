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


def test_background_is_not_a_pitch_and_offscreen_is_rejected():
    mask = field_mask(np.zeros((100, 100, 3), np.uint8))
    assert mask.sum() == 0
    assert not inside_field(mask, [-30, 0, -10, 30])


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
    assert client.get("/health").json()["available"] is False
    response = client.post("/jobs", content=b"data", headers={"Content-Type": "video/mp4"})
    assert response.status_code == 503
    assert server.active is None


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
        def predict(self, image, **kwargs):
            ys, xs = np.where(image[:, :, 0] > 0)
            boxes = np.array([[xs.min(), ys.min(), xs.max(), ys.max()]])
            return [SimpleNamespace(boxes=SimpleNamespace(xyxy=boxes, conf=np.array([0.8])))]

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
