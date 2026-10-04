import json
import uuid

import cv2
import numpy as np
import pytest

from app.vision import balllabels


def counter_video(path, frames=60, size=(64, 48), fps=10):
    """Each frame's brightness encodes its number (2 * n)."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    for n in range(frames):
        writer.write(np.full((size[1], size[0], 3), 2 * n, np.uint8))
    writer.release()


def result(n=30, every=2):
    frames = []
    for i in range(n):
        ball = {"x": 10.0 + i, "y": 20.0, "box": [8, 18, 12, 22], "confidence": 0.6} if i % 3 else None
        if i == 4:
            ball = {"x": 30.0, "y": 30.0, "box": [28, 28, 32, 32], "confidence": 0.1, "inferred": True}
        frames.append({"t": round(i * every / 10, 3), "frame": i * every, "scene": 0, "players": [], "ball": ball})
    return {"frames": frames, "sampleFps": 5, "video": {"width": 64, "height": 48, "fps": 10}, "analysedStart": 0, "analysedDuration": n * every / 10}


def test_sampled_frames_are_spread_over_the_match_and_repeatable():
    data = result(300)
    picks = balllabels.sample_frames(data, count=30, seed=7)
    assert len(picks) == 30 and picks == balllabels.sample_frames(data, count=30, seed=7)
    # One pick in every tenth of the match.
    assert all(sum(1 for p in picks if k * 30 <= p < (k + 1) * 30) == 3 for k in range(10))


def test_metrics_score_the_engine_against_the_reviewer():
    data = result(30)
    store = {"labels": {}}
    # Frame 1: engine at (11, 20); reviewer saw the ball there -> hit.
    store["labels"]["1"] = {"visible": True, "x": 11.5, "y": 20.0}
    # Frame 2: engine at (12, 20); reviewer saw it 10 px away -> miss (a false position).
    store["labels"]["2"] = {"visible": True, "x": 22.0, "y": 20.0}
    # Frame 3: no detection; reviewer saw the ball -> miss.
    store["labels"]["3"] = {"visible": True, "x": 5.0, "y": 5.0}
    # Frame 5: detection, but the reviewer saw no ball -> false detection.
    store["labels"]["5"] = {"visible": False}
    # Frame 4: only an inferred position, scored separately.
    store["labels"]["4"] = {"visible": True, "x": 30.5, "y": 30.0}
    m = balllabels.metrics(data, store)
    assert m["labelled"] == 5 and m["visible"] == 4
    assert m["recall"]["value"] == 0.25 and m["recall"]["n"] == 4
    assert m["precision"]["value"] == pytest.approx(1 / 3, abs=1e-3)
    assert m["falseDetections"]["value"] == 1.0
    assert m["inferredAccuracy"]["value"] == 1.0
    assert m["tolerancePixels"] == 3.0


def test_labels_are_validated():
    with pytest.raises(ValueError):
        balllabels.clean_label({"index": -1, "x": 1, "y": 1}, (64, 48))
    with pytest.raises(ValueError):
        balllabels.clean_label({"index": 1, "x": 100, "y": 1}, (64, 48))
    with pytest.raises(ValueError):
        balllabels.clean_label({"index": 1, "x": float("nan"), "y": 1}, (64, 48))
    with pytest.raises(ValueError):
        balllabels.clean_label({"index": True, "visible": False}, (64, 48))
    assert balllabels.clean_label({"index": 3, "visible": False}, (64, 48)) == {"index": 3, "visible": False}


def test_worker_serves_frames_and_records_labels(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    job_id = uuid.uuid4().hex
    directory = tmp_path / job_id
    directory.mkdir()
    (directory / "status.json").write_text(json.dumps({"id": job_id, "status": "completed", "stage": "done", "progress": 100, "createdAt": 0, "title": "t"}))
    (directory / "result.json").write_text(json.dumps(result(30)))
    counter_video(directory / "video.avi")
    (directory / "video.avi").rename(directory / "video")

    listing = client.get(f"/jobs/{job_id}/ball-labels").json()
    assert listing["videoAvailable"] and listing["frames"] and listing["metrics"]["labelled"] == 0
    # Frames are prepared in one background pass; the image is the analysed frame
    # itself (sampled frame 7 = source frame 14).
    import time

    pick = listing["frames"][0]["index"]
    for _ in range(200):
        if client.get(f"/jobs/{job_id}/ball-labels").json()["framesReady"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("label frames were never prepared")
    assert client.get(f"/jobs/{job_id}/frames/{pick}").status_code == 200
    (directory / "labelframes" / "7.jpg").write_bytes(
        cv2.imencode(".jpg", balllabels.extract_frames(directory / "video", {14: 14})[14])[1].tobytes()
    )
    image = client.get(f"/jobs/{job_id}/frames/7")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    pixels = cv2.imdecode(np.frombuffer(image.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert abs(int(np.median(pixels)) - 28) <= 3
    assert client.get(f"/jobs/{job_id}/frames/99").status_code == 409  # not a prepared frame

    assert client.post(f"/jobs/{job_id}/ball-labels", json={"index": 1, "x": 11.0, "y": 20.0}).status_code == 200
    out = client.post(f"/jobs/{job_id}/ball-labels", json={"index": 5, "visible": False}).json()
    assert out["metrics"]["labelled"] == 2 and out["metrics"]["recall"]["value"] == 1.0
    # A later answer for the same frame replaces the earlier one (history kept).
    out = client.post(f"/jobs/{job_id}/ball-labels", json={"index": 1, "visible": False}).json()
    assert out["metrics"]["labelled"] == 2 and out["metrics"]["visible"] == 0
    assert len(json.loads((directory / "balllabels.json").read_text())["history"]) == 3
    for bad in ({"index": 1, "x": "a", "y": 2}, {"index": 500, "visible": False}, [1, 2]):
        assert client.post(f"/jobs/{job_id}/ball-labels", json=bad).status_code == 400
    # Without the footage, labelling is not offered and frames are unavailable.
    (directory / "video").unlink()
    assert client.get(f"/jobs/{job_id}/ball-labels").json()["videoAvailable"] is False
    assert client.get(f"/jobs/{job_id}/frames/99").status_code == 404


def _labelled_job(root, name, n=40, size=(640, 360)):
    directory = root / name
    directory.mkdir()
    writer = cv2.VideoWriter(str(directory / "v.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 10, size)
    frames, labels = [], {}
    for i in range(n):
        image = np.full((size[1], size[0], 3), (40, 130, 40), np.uint8)
        x, y = 40 + 13 * i, 100
        cv2.circle(image, (x, y), 4, (245, 245, 245), -1)
        writer.write(image)
        frames.append({"t": i / 10, "frame": i, "scene": 0, "players": [], "ball": None})
        labels[str(i)] = {"visible": True, "x": float(x), "y": float(y)} if i % 5 else {"visible": False}
    writer.release()
    (directory / "v.avi").rename(directory / "video")
    (directory / "result.json").write_text(json.dumps({"frames": frames, "video": {"width": size[0], "height": size[1], "fps": 10}}))
    (directory / "balllabels.json").write_text(json.dumps({"labels": labels}))
    (directory / "status.json").write_text(json.dumps({"id": name, "status": "completed", "owner": "f" * 32 if name.startswith("a") else "e" * 32}))
    return directory


def test_training_set_uses_the_detector_tiles_and_splits_by_match(tmp_path):
    from app.vision import balltrain
    from app.vision.ball import tile_frame

    for name in ("a" * 32, "b" * 32, "c" * 32):
        _labelled_job(tmp_path, name)
    jobs = balltrain.labelled_jobs(tmp_path)
    train, val, how = balltrain.split(jobs)
    assert how == "by-match" and len(train) == 2 and len(val) == 1
    counts = balltrain.build_dataset(train, val, tmp_path / "ds")
    assert counts["testFrames"] == 40 and counts["train"]["positive"] >= 40 and counts["val"]["positive"] >= 5
    # Negatives are kept at about one per positive (at least a few).
    assert counts["train"]["negative"] <= counts["train"]["positive"] + 1
    # The test match never appears in the training or epoch-choice tiles.
    test_stem = val[0][0].name[:8]
    assert not any(p.name.startswith(test_stem) for p in (tmp_path / "ds" / "images").rglob("*.jpg"))
    # Every positive label sits on the ball inside its tile, at the tile's own scale.
    for txt in (tmp_path / "ds" / "labels" / "train").glob("*.txt"):
        line = txt.read_text().split()
        if not line:
            continue
        crop = cv2.imread(str(tmp_path / "ds" / "images" / "train" / (txt.stem + ".jpg")))
        h, w = crop.shape[:2]
        cx, cy = float(line[1]) * w, float(line[2]) * h
        assert crop[int(cy), int(cx)].mean() > 200
    assert len(tile_frame(np.zeros((360, 640, 3), np.uint8))) == 2
    # One match only: split by time, the last 30% for checking.
    train, val, how = balltrain.split(jobs[:1])
    assert how == "by-time-within-one-match" and max(train[0][1]) < min(val[0][1])


def test_new_weights_are_kept_only_when_they_validate_better(tmp_path):
    from app.vision import balltrain

    for name in ("a" * 32, "b" * 32):
        _labelled_job(tmp_path, name)
    base = tmp_path / "base.pt"
    base.write_bytes(b"base weights")
    calls = []

    def remote(dataset_zip, base_bytes, epochs, recall=0.9):
        calls.append((len(dataset_zip), base_bytes, epochs))
        return {"weights": b"new weights %d" % len(calls), "baseline": {"recall": 0.5, "precision": 0.8, "visible": 30},
                "candidate": {"recall": recall, "precision": 0.85, "visible": 30}}

    run = balltrain.run_training(tmp_path, tmp_path / "models", base, epochs=5, remote=remote, now=1.0)
    assert run["kept"] and calls[0][1] == b"base weights" and calls[0][2] == 5
    registry = balltrain.registry(tmp_path / "models")
    assert registry["active"] == run["weights"] and (tmp_path / "models" / run["weights"]).is_file()
    # A worse candidate is recorded but never switched on.
    worse = balltrain.run_training(tmp_path, tmp_path / "models", base, epochs=5, remote=lambda *a: remote(*a, recall=0.4), now=2.0)
    assert not worse["kept"] and balltrain.registry(tmp_path / "models")["active"] == run["weights"]
    # Training on one customer's matches only.
    with pytest.raises(ValueError):
        balltrain.run_training(tmp_path, tmp_path / "models", base, owner="d" * 32, remote=remote)


def test_profiles_use_the_validated_ball_model(tmp_path, monkeypatch):
    from app.vision import profiles

    monkeypatch.setenv("VISION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VISION_BALL_WEIGHTS", raising=False)
    assert profiles.active_ball_weights() is None
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "ball-0123456789abcdef.pt").write_bytes(b"x")
    (tmp_path / "models" / "ball-model.json").write_text(json.dumps({"active": "ball-0123456789abcdef.pt"}))
    assert profiles.model_paths("broadcast")[1].name == "ball-0123456789abcdef.pt"
    assert profiles.expected_digest("ball-0123456789abcdef.pt") == "0123456789abcdef"
    # A tampered registry cannot point outside the models folder.
    (tmp_path / "models" / "ball-model.json").write_text(json.dumps({"active": "../../etc/passwd"}))
    assert profiles.active_ball_weights() is None


def test_training_endpoint_validates_and_needs_a_tiled_ball_model(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setenv("VISION_DATA_DIR", str(tmp_path))
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    assert client.get("/ball-model").json()["active"] is None
    assert client.post("/ball-model/train", json={"epochs": 0}).status_code == 400
    assert client.post("/ball-model/train", json={"epochs": True}).status_code == 400
    # From the hosted site an owner key is required (only that browser's matches train).
    assert client.post("/ball-model/train", json={}, headers={"x-pitchlens-require-owner": "1"}).status_code == 403
    monkeypatch.setenv("VISION_SITE_TRAINING", "1")
    assert client.post("/ball-model/train", json={}, headers={"x-pitchlens-require-owner": "1"}).status_code == 400
    monkeypatch.delenv("VISION_SITE_TRAINING")
    monkeypatch.setattr("app.vision.profiles.model_paths", lambda profile="general": (tmp_path / "p.pt", tmp_path / "missing.pt"))
    assert client.post("/ball-model/train", json={}).status_code == 409
    assert client.get("/ball-model", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_label_frames_match_the_engine_numbering_on_trimmed_variable_frame_rate_video():
    """Seeking to a frame number lands one frame late on this clip (an edit list
    over variable-frame-rate video); one sequential pass does not."""
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "trimmed-vfr.mp4"
    cap = cv2.VideoCapture(str(path))
    sequential = []
    while True:
        ok, image = cap.read()
        if not ok:
            break
        sequential.append(image)
    cap.release()
    wanted = {n: n for n in range(0, len(sequential), 4)}
    got = balllabels.extract_frames(path, wanted)
    assert sorted(got) == sorted(wanted)
    assert all(np.array_equal(got[n], sequential[n]) for n in wanted)
