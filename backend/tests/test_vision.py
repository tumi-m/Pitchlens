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
