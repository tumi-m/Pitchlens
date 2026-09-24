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


# ── Hosted worker: chunked uploads, ownership, retention ──────────────────
FIXTURE = __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend/tests/fixtures/review.mp4"


@pytest.fixture
def hosted(service, monkeypatch, tmp_path):
    server, client = service
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("VISION_MODEL_PATH", str(model))
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(model))
    monkeypatch.setattr(server, "upload_activity", {})
    monkeypatch.setattr(server, "upload_started", {})
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
    # Starting before every byte arrives is rejected.
    assert client.post(f"/jobs/{job['id']}/start").status_code == 409
    step = 100_000
    for offset in range(0, len(data), step):
        r = client.put(f"/jobs/{job['id']}/video?offset={offset}", content=data[offset : offset + step])
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
    event = threading.Event()
    server.cancellations[job] = event
    server.work(directory, status, event)
    assert status["status"] == "interrupted"


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
    monkeypatch.setattr(server, "UPLOAD_IDLE_SECONDS", 0)
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
    response = client.post(
        "/jobs/from-url?url=https://youtu.be/dQw4w9WgXcQ&owner=" + "c" * 32
    )
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
    assert client.post("/jobs?size=10", headers={"Content-Type": "video/mp4"}).status_code == 200


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
