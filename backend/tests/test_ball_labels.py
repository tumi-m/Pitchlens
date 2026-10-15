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
    assert m["measured"] is True


def test_a_player_load_job_has_no_ball_accuracy_to_report():
    data = result(60)
    for f in data["frames"]:
        f["ball"] = None
    data.update(ballSearch="none", ballInference="skipped")
    store = {"labels": {str(i): ({"visible": True, "x": 10.0, "y": 20.0} if i % 2 else {"visible": False}) for i in range(40)}}
    m = balllabels.metrics(data, store)
    # The ball model never ran: not a measured 0% recall and 0% false detections.
    for key in ("recall", "precision", "falseDetections", "inferredAccuracy"):
        assert m[key] == {"value": None, "n": 0, "interval95": None}, key
    assert m["measured"] is False
    # The labels still count (and still train the ball finder).
    assert m["labelled"] == 40 and m["visible"] == 20


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


def _labelled_job(root, name, n=40, size=(640, 360), consent=True):
    """A labelled match; with consent, its owner ticked the training opt-in."""
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
    status = {"id": name, "status": "completed", "owner": "f" * 32 if name.startswith("a") else "e" * 32}
    if consent:
        status["trainingConsent"] = {"agreed": True, "at": 0}
    (directory / "status.json").write_text(json.dumps(status))
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


def _scores(dataset_zip, baseline_recall, candidate_recall, precision=(0.8, 0.85)):
    """Stub scores on the zipped dataset's real test frames; the candidate finds
    every ball the baseline finds, and more."""
    import io
    import zipfile

    test = json.loads(zipfile.ZipFile(io.BytesIO(dataset_zip)).read("test_frames.json"))
    visible = sum(1 for f in test if f["label"].get("visible"))

    def score(recall, p):
        k = round(recall * visible)
        return {"recall": round(k / visible, 3), "precision": p, "visible": visible, "hits": [1] * k + [0] * (visible - k)}

    return {"baseline": score(baseline_recall, precision[0]), "candidate": score(candidate_recall, precision[1])}


def test_new_weights_are_kept_only_when_they_validate_better(tmp_path):
    from app.vision import balltrain

    for name in ("a" * 32, "b" * 32):
        _labelled_job(tmp_path, name)
    base = tmp_path / "base.pt"
    base.write_bytes(b"base weights")
    calls = []

    def remote(dataset_zip, base_bytes, epochs, recall=0.9):
        calls.append((len(dataset_zip), base_bytes, epochs))
        return {"weights": b"new weights %d" % len(calls), **_scores(dataset_zip, 0.5, recall)}

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
    # The website offers its training button only when the worker allows it.
    monkeypatch.delenv("VISION_SITE_TRAINING", raising=False)
    assert client.get("/health").json()["siteTraining"] is False
    monkeypatch.setenv("VISION_SITE_TRAINING", "1")
    assert client.get("/health").json()["siteTraining"] is True
    monkeypatch.delenv("VISION_SITE_TRAINING")
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


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setattr(server, "active", None)
    monkeypatch.setenv("VISION_DATA_DIR", str(tmp_path))
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer test-token"
    return server, client


def _finished(tmp_path, job_id, created=0.0, video=True):
    directory = tmp_path / job_id
    directory.mkdir()
    (directory / "status.json").write_text(json.dumps({"id": job_id, "status": "completed", "createdAt": created, "title": "t"}))
    (directory / "result.json").write_text(json.dumps(result(30)))
    if video:
        counter_video(directory / "video.avi")
        (directory / "video.avi").rename(directory / "video")
    return directory


def test_label_stills_are_deleted_with_the_footage(tmp_path, monkeypatch):
    import time

    server, client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "RETENTION_HOURS", 168)
    old = _finished(tmp_path, "a" * 32, created=time.time() - 200 * 3600)
    (old / "labelframes").mkdir()
    (old / "labelframes" / "5.jpg").write_bytes(b"\xff\xd8 still of the footage")
    swept = _finished(tmp_path, "b" * 32, video=False)  # expired before stills were swept
    status = json.loads((swept / "status.json").read_text())
    (swept / "status.json").write_text(json.dumps({**status, "videoDeleted": True}))
    (swept / "labelframes").mkdir()
    (swept / "labelframes" / "3.jpg").write_bytes(b"\xff\xd8")
    fresh = _finished(tmp_path, "c" * 32, created=time.time())
    (fresh / "labelframes").mkdir()
    (fresh / "labelframes" / "5.jpg").write_bytes(b"\xff\xd8")
    server.delete_expired()
    assert not (old / "video").exists() and not (old / "labelframes").exists()
    assert not (swept / "labelframes").exists()
    assert (fresh / "labelframes" / "5.jpg").exists() and (fresh / "video").exists()
    assert client.get(f"/jobs/{'a' * 32}/frames/5").status_code == 404
    # A background extraction that wrote after the sweep is never served either.
    (old / "labelframes").mkdir()
    (old / "labelframes" / "5.jpg").write_bytes(b"\xff\xd8")
    assert client.get(f"/jobs/{'a' * 32}/frames/5").status_code == 404
    assert not (old / "labelframes").exists()
    assert client.get(f"/jobs/{'c' * 32}/frames/5").status_code == 200


def test_restart_removes_frame_copies_left_by_a_killed_training_run(tmp_path, monkeypatch):
    server, _ = _client(tmp_path, monkeypatch)
    for part in ("dataset/images/train", "runs/ball/weights"):
        (tmp_path / "training" / part).mkdir(parents=True)
    (tmp_path / "training" / "dataset" / "images" / "train" / "a-1-0.jpg").write_bytes(b"\xff\xd8")
    server.recover_after_restart()
    assert not (tmp_path / "training" / "dataset").exists()
    assert not (tmp_path / "training" / "runs").exists()


def _wait_for_training(server):
    import time

    for _ in range(400):
        if server.training.get("state") != "running":
            return server.training
        time.sleep(0.01)
    raise AssertionError("training never finished")


def _trainable(tmp_path, monkeypatch):
    from app.vision import balltrain

    server, client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "training", {"state": "idle"})
    # Never resolve "auto" (that imports torch, which the Poetry CI job does not install).
    monkeypatch.setenv("VISION_DEVICE", "cpu")
    for name in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "ball.pt").write_bytes(b"tiled ball model")
    monkeypatch.setattr("app.vision.profiles.model_paths", lambda profile="general": (tmp_path / "p.pt", tmp_path / "ball.pt"))
    runs = []

    def fake_run_training(root, models, base, epochs=60, owner=None, on_start=None, **kwargs):
        if kwargs.get("too_few") or getattr(fake_run_training, "too_few", False):
            raise ValueError("Too few labels to train")
        on_start()
        runs.append({"epochs": epochs, "owner": owner})
        return {"kept": False}

    monkeypatch.setattr(balltrain, "run_training", fake_run_training)
    return server, client, runs, fake_run_training


def test_an_operator_can_train_from_a_terminal_on_a_hosted_worker(tmp_path, monkeypatch):
    server, client, runs, _ = _trainable(tmp_path, monkeypatch)
    for variable, value in (("RAILWAY_PUBLIC_DOMAIN", "pitchlens.up.railway.app"), ("VISION_PUBLIC_URL", "https://worker.example")):
        monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
        monkeypatch.delenv("VISION_PUBLIC_URL", raising=False)
        monkeypatch.setenv(variable, value)
        assert server.require_owner(type("R", (), {"headers": {}})())  # a hosted worker
        response = client.post("/ball-model/train", json={"epochs": 80})
        assert response.status_code == 200 and response.json() == {"started": True}
        assert _wait_for_training(server)["state"] == "done"
    assert runs == [{"epochs": 80, "owner": None}] * 2
    # The website's proxy marks its requests: refused unless site training is on.
    site = {"x-pitchlens-require-owner": "1", "x-pitchlens-owner": "f" * 32}
    assert client.post("/ball-model/train", json={}, headers=site).status_code == 403


def test_site_training_has_a_daily_limit_counted_when_training_starts(tmp_path, monkeypatch):
    server, client, runs, fake = _trainable(tmp_path, monkeypatch)
    monkeypatch.setenv("VISION_SITE_TRAINING", "1")
    monkeypatch.setenv("VISION_MAX_TRAINING_PER_DAY", "2")
    site = {"x-pitchlens-require-owner": "1", "x-pitchlens-owner": "f" * 32}
    # Refused for too few labels: no GPU used, nothing counted.
    fake.too_few = True
    assert client.post("/ball-model/train", json={}, headers=site).status_code == 200
    assert _wait_for_training(server)["state"] == "failed"
    assert not (tmp_path / "training-starts.json").exists()
    fake.too_few = False
    for _ in range(2):
        assert client.post("/ball-model/train", json={"epochs": 300}, headers=site).status_code == 200
        assert _wait_for_training(server)["state"] == "done"
    refused = client.post("/ball-model/train", json={}, headers=site)
    assert refused.status_code == 429 and "today's limit" in refused.json()["detail"]
    # Site runs ask for at most what the website's button sends, on that owner's matches.
    assert runs == [{"epochs": 60, "owner": "f" * 32}] * 2
    # The operator's own runs are not capped.
    assert client.post("/ball-model/train", json={"epochs": 5}).status_code == 200
    assert _wait_for_training(server)["state"] == "done" and len(runs) == 3


def test_run_training_reports_the_start_only_after_the_label_checks(tmp_path):
    from app.vision import balltrain

    base = tmp_path / "base.pt"
    base.write_bytes(b"base weights")
    events = []

    def remote(*args):
        events.append("train")
        return {"weights": b"w", "baseline": {"recall": 0.5, "precision": 0.8, "visible": 30},
                "candidate": {"recall": 0.5, "precision": 0.8, "visible": 30}}

    _labelled_job(tmp_path, "a" * 32, n=12)  # too few labels to train on
    with pytest.raises(ValueError, match="Too few labels"):
        balltrain.run_training(tmp_path, tmp_path / "models", base, remote=remote, on_start=lambda: events.append("start"))
    assert events == []
    # (Once the labels pass, the start is reported right before training.)
    import shutil

    shutil.rmtree(tmp_path / ("a" * 32))
    for name in ("a" * 32, "b" * 32):
        _labelled_job(tmp_path, name, n=60)
    balltrain.run_training(tmp_path, tmp_path / "models", base, remote=remote, on_start=lambda: events.append("start"))
    assert events == ["start", "train"]


def test_labelling_reuses_one_parse_of_the_result_until_it_changes(tmp_path, monkeypatch):
    server, client = _client(tmp_path, monkeypatch)
    job = "d" * 32
    directory = _finished(tmp_path, job)
    text = (directory / "result.json").read_text()
    expected = server._ball_labels_response(job, directory, json.loads(text), prepare=False)
    parses = []
    real = server.json.loads

    def counting(value, *args, **kwargs):
        if isinstance(value, str) and value == (directory / "result.json").read_text():
            parses.append(1)
        return real(value, *args, **kwargs)

    monkeypatch.setattr(server.json, "loads", counting)
    first = client.get(f"/jobs/{job}/ball-labels").json()
    second = client.get(f"/jobs/{job}/ball-labels").json()
    assert len(parses) == 1
    for response in (first, second):
        assert {k: v for k, v in response.items() if k != "framesReady"} == {k: v for k, v in expected.items() if k != "framesReady"}
    assert client.post(f"/jobs/{job}/ball-labels", json={"index": 1, "x": 11.0, "y": 20.0}).status_code == 200
    assert len(parses) == 1
    # A rewritten result (re-analysis) is parsed again.
    changed = result(30)
    changed["frames"][1]["ball"] = None
    (directory / "result.json").write_text(json.dumps(changed))
    assert client.get(f"/jobs/{job}/ball-labels").json()["metrics"]["recall"]["value"] == 0.0
    assert len(parses) == 2
    assert any(key[0] == job for key in server._slim)
    import time

    for _ in range(200):  # let the background frame extraction finish first
        if client.get(f"/jobs/{job}/ball-labels").json()["framesReady"]:
            break
        time.sleep(0.05)
    assert client.delete(f"/jobs/{job}").status_code == 200
    assert not any(key[0] == job for key in server._slim)


def _cached_job(root, name, n=20, size=(1280, 720), visible=lambda i: i % 4, source=None, sha=None, frame_step=10, offset=0,
                consent=True):
    """A labelled job whose label frames are already in the cache (no decoding)."""
    directory = root / name
    (directory / "labelframes").mkdir(parents=True)
    (directory / "video").write_bytes(b"")
    frames, labels = [], {}
    for i in range(n):
        image = np.full((size[1], size[0], 3), (40, 130, 40), np.uint8)
        x = int(60 + (size[0] - 120) * i / n)
        y = int(size[1] * (0.15 + 0.7 * ((i * 7) % n) / n))
        cv2.circle(image, (x, y), 5, (245, 245, 245), -1)
        cv2.imwrite(str(directory / "labelframes" / f"{i}.jpg"), image)
        frames.append({"t": round(i / 3, 3), "frame": offset + i * frame_step, "scene": 0, "players": [], "ball": None})
        labels[str(i)] = {"visible": True, "x": float(x), "y": float(y)} if visible(i) else {"visible": False}
    result = {"frames": frames, "video": {"width": size[0], "height": size[1], "fps": 30}}
    if sha:
        result["videoSha256"] = sha
    (directory / "result.json").write_text(json.dumps(result))
    (directory / "balllabels.json").write_text(json.dumps({"labels": labels}))
    status = {"id": name, "status": "completed"}
    if source:
        status["sourceJobId"] = source
    if consent:
        status["trainingConsent"] = {"agreed": True, "at": 0}
    (directory / "status.json").write_text(json.dumps(status))
    return directory


def test_training_set_is_unchanged_when_frames_are_streamed_from_the_cache(tmp_path):
    """Recorded from the previous implementation (every frame decoded into memory)."""
    from app.vision import balltrain

    a = _cached_job(tmp_path, "a" * 32)
    b = _cached_job(tmp_path, "b" * 32)
    counts = balltrain.build_dataset([(a, None), (b, set(range(14)))], [(b, set(range(14, 20)))], tmp_path / "ds")
    assert counts == {"train": {"positive": 34, "negative": 34}, "val": {"positive": 8, "negative": 8}, "testFrames": 6}
    expected = {
        ("train", "pos"): "a-1-0 a-10-1 a-10-4 a-11-4 a-13-1 a-13-2 a-13-4 a-13-5 a-14-4 a-14-5 a-15-2 a-2-3 a-3-0 a-5-3 a-6-0 a-6-1 a-7-0 "
        "a-7-1 a-7-3 a-7-4 a-9-1 b-1-0 b-10-1 b-10-4 b-2-3 b-3-0 b-5-3 b-6-0 b-6-1 b-7-0 b-7-1 b-7-3 b-7-4 b-9-1",
        ("train", "neg"): "a-1-4 a-1-5 a-12-3 a-13-0 a-14-0 a-14-2 a-15-5 a-16-2 a-16-4 a-2-1 a-3-2 a-3-3 a-5-0 a-6-2 a-7-2 a-8-1 a-8-4 "
        "a-9-2 b-1-1 b-1-4 b-2-1 b-2-5 b-3-2 b-4-0 b-4-1 b-4-5 b-6-3 b-6-5 b-7-5 b-8-3 b-8-4 b-8-5 b-9-3 b-9-4",
        ("val", "pos"): "a-17-5 a-18-2 a-19-5 b-11-4 b-13-1 b-13-2 b-13-4 b-13-5",
        ("val", "neg"): "a-17-3 a-19-0 a-19-1 a-19-3 b-11-0 b-11-2 b-11-5 b-12-0",
    }
    ds = tmp_path / "ds"

    def short(path):
        # Files are named after the full job id, so DELETE can find a run's copies.
        return path.stem.replace("a" * 32, "a").replace("b" * 32, "b")

    for (part, kind), names in expected.items():
        found = sorted(
            short(p) for p in (ds / "images" / part).glob("*.jpg")
            if ((ds / "labels" / part / f"{p.stem}.txt").read_text() == "") == (kind == "neg")
        )
        assert found == names.split(), (part, kind)
    assert sorted(short(p) for p in (ds / "test_frames").glob("*.jpg")) == "b-14 b-15 b-16 b-17 b-18 b-19".split()
    assert sorted(p.name for p in ds.iterdir()) == ["data.yaml", "images", "labels", "test_frames", "test_frames.json"]
    assert sum(1 for p in ds.rglob("*") if p.is_file()) == 176
    # Test frames are the cached JPEGs themselves.
    assert (ds / "test_frames" / f"{'b' * 32}-14.jpg").read_bytes() == (b / "labelframes" / "14.jpg").read_bytes()


def test_building_the_training_set_never_holds_a_match_of_frames_in_memory(tmp_path):
    import tracemalloc

    from app.vision import balltrain

    jobs = [_cached_job(tmp_path, f"{m:x}" * 32, n=40, size=(1920, 1080)) for m in range(4)]
    tracemalloc.start()
    try:
        counts = balltrain.build_dataset([(d, None) for d in jobs[:3]], [(jobs[3], None)], tmp_path / "ds")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # Decoding every frame first kept about 950 MB alive here (6 MB per 1080p frame).
    assert counts["testFrames"] == 40 and counts["train"]["positive"] > 0
    assert peak < 300 * 2**20


def test_one_more_ball_found_is_not_enough_to_switch_models():
    from app.vision import balltrain

    def score(found, n=30, precision=0.7):
        return {"recall": round(sum(found) / n, 3), "precision": precision, "visible": n, "hits": found}

    baseline = [1] * 18 + [0] * 12
    one_more = [1] * 19 + [0] * 11
    assert not balltrain.better(score(one_more, precision=0.70), score(baseline, precision=0.71))
    assert balltrain.paired_test(score(one_more), score(baseline)) == {"b": 1, "c": 0, "p": 0.5}
    strong_old = [1] * 10 + [0] * 20
    strong_new = [0] + [1] * 9 + [1] * 12 + [0] * 8  # 12 found only by the new model, 1 only by the old
    test = balltrain.paired_test(score(strong_new), score(strong_old))
    assert (test["b"], test["c"]) == (12, 1) and test["p"] < 0.01
    assert balltrain.better(score(strong_new), score(strong_old))
    # Without frame-level results there is no evidence beyond chance.
    assert not balltrain.better({"recall": 0.9, "precision": 0.9, "visible": 30}, {"recall": 0.5, "precision": 0.9, "visible": 30})


def test_a_test_part_with_few_visible_balls_never_switches_the_model(tmp_path):
    from app.vision import balltrain

    # One match, split by time: the last 12 of 40 frames hold 7 visible balls.
    _cached_job(tmp_path, "a" * 32, n=40, size=(640, 360), visible=lambda i: i < 28 or i in (29, 31, 33, 35, 37, 38, 39))
    base = tmp_path / "base.pt"
    base.write_bytes(b"base weights")
    models = tmp_path / "models"
    models.mkdir()
    before = {"active": "ball-0123456789abcdef.pt", "runs": []}
    (models / "ball-model.json").write_text(json.dumps(before))
    trained = []

    def remote(*args):
        trained.append(1)
        return {"weights": b"w", **_scores(args[0], 0.0, 1.0)}

    started = []
    run = balltrain.run_training(tmp_path, models, base, remote=remote, usable=lambda d: True, on_start=lambda: started.append(1))
    assert run["split"] == "by-time-within-one-match" and run["testVisible"] == 7
    assert run["kept"] is False and run["reason"] == "not enough held-out balls"
    assert not trained and not started
    assert json.loads((models / "ball-model.json").read_text()) == before
    assert not (tmp_path / "training" / "dataset").exists()


def test_reruns_of_the_same_footage_are_one_match_in_the_split(tmp_path):
    from app.vision import balllabels, balltrain

    def source_frames(part):
        out = set()
        for d, indices in part:
            result = json.loads((d / "result.json").read_text())
            out |= {balllabels.source_frame(result, i) for i in indices}
        return out

    # An original and its re-run (linked by the re-run chain; this result has no
    # hash), labelled separately: one match, so split by time, never one against the other.
    _cached_job(tmp_path, "a" * 32, n=150, size=(640, 360), sha="1" * 64)
    _cached_job(tmp_path, "b" * 32, n=60, size=(640, 360), source="a" * 32, frame_step=10, offset=1000)
    jobs = balltrain.labelled_jobs(tmp_path, usable=lambda d: True)
    train, test, how = balltrain.split(jobs)
    assert how == "by-time-within-one-match"
    assert not source_frames(train) & source_frames(test)
    # With another match to hold out, the original and the re-run train together,
    # and a picture labelled in both is used once.
    _cached_job(tmp_path, "c" * 32, n=45, size=(640, 360), sha="2" * 64)
    jobs = balltrain.labelled_jobs(tmp_path, usable=lambda d: True)
    train, test, how = balltrain.split(jobs)
    assert how == "by-match"
    assert {d.name for d, _ in train} == {"a" * 32, "b" * 32} and {d.name for d, _ in test} == {"c" * 32}
    numbers = [json.loads((d / "result.json").read_text())["frames"][i]["frame"] for d, idx in train for i in idx]
    assert len(numbers) == len(set(numbers)) == 150 + 10  # frames 1000-1490 overlap the original's 0-1490


def test_a_byte_identical_reupload_is_the_same_match_as_the_original(tmp_path):
    from app.vision import balltrain

    _cached_job(tmp_path, "a" * 32, n=60, size=(640, 360), sha="1" * 64)
    _cached_job(tmp_path, "b" * 32, n=40, size=(640, 360), sha="1" * 64)
    jobs = balltrain.labelled_jobs(tmp_path, usable=lambda d: True)
    train, test, how = balltrain.split(jobs)
    # One match only: split by time, never "by match" against itself.
    assert how == "by-time-within-one-match"


def test_a_later_run_never_tests_on_frames_the_model_in_use_trained_on(tmp_path):
    from app.vision import balllabels, balltrain

    models = tmp_path / "models"
    base = tmp_path / "ball-stock.pt"
    base.write_bytes(b"stock")

    def remote(dataset_zip, base_bytes, epochs):
        return {"weights": b"model %d" % len(base_bytes), **_scores(dataset_zip, 0.2, 0.9)}

    match_b = _cached_job(tmp_path, "b" * 32, n=150, size=(640, 360), sha="b" * 64, visible=lambda i: i % 5)
    first = balltrain.run_training(tmp_path, models, base, remote=remote, usable=lambda d: True)
    assert first["split"] == "by-time-within-one-match" and first["kept"]
    active = models / first["weights"]
    assert balltrain.registry(models)["active"] == active.name
    result_b = json.loads((match_b / "result.json").read_text())
    taught = balltrain.trained_on(models, active.name)["sha:" + "b" * 64]
    assert taught and len(taught) == int(150 * 0.7)
    # A bigger match is labelled later; run 2 fine-tunes from (and is compared with) the run-1 model.
    _cached_job(tmp_path, "c" * 32, n=200, size=(640, 360), sha="c" * 64)
    jobs = balltrain.labelled_jobs(tmp_path, usable=lambda d: True)
    plan = balltrain.plan(jobs, trained=balltrain.trained_on(models, active.name))
    tested_b = {balllabels.source_frame(result_b, i) for d, idx in plan["test"] if d == match_b for i in idx}
    assert not tested_b & taught
    # Without the lineage, B would have been the held-out set, run-1 training frames and all.
    naive = balltrain.plan(jobs)
    assert {balllabels.source_frame(result_b, i) for d, idx in naive["test"] if d == match_b for i in idx} & taught
    second = balltrain.run_training(tmp_path, models, active, remote=remote, usable=lambda d: True)
    assert second.get("reason") == "not enough held-out balls" or second["testVisible"] >= balltrain.MIN_TEST_VISIBLE


def test_lineage_stays_on_the_server(tmp_path, monkeypatch):
    from app.vision import balltrain

    server, client = _client(tmp_path, monkeypatch)
    models = tmp_path / "models"
    models.mkdir()
    (models / "ball-model.json").write_text(json.dumps({"active": "ball-0123456789abcdef.pt", "runs": []}))
    (models / balltrain.LINEAGE).write_text(json.dumps({"ball-0123456789abcdef.pt": {"base": "x.pt", "groups": [{"keys": ["sha:" + "9" * 64, "root:" + "a" * 32], "frames": None}]}}))
    body = client.get("/ball-model").text
    assert "9" * 64 not in body and "a" * 32 not in body and "groups" not in body
    assert client.get(f"/models/{balltrain.LINEAGE}").status_code == 404


def test_training_on_modal_never_resolves_a_local_device(tmp_path, monkeypatch):
    import sys

    from app.vision import balltrain

    server, client, _, _ = _trainable(tmp_path, monkeypatch)
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")
    monkeypatch.delenv("VISION_DEVICE")
    monkeypatch.setitem(sys.modules, "torch", None)  # the worker image's Poetry twin has no torch
    seen = {}

    def fake_run_training(root, models, base, **kwargs):
        seen.update(kwargs)
        return {"kept": False}

    monkeypatch.setattr(balltrain, "run_training", fake_run_training)
    assert client.post("/ball-model/train", json={}).status_code == 200
    assert _wait_for_training(server)["state"] == "done"
    assert seen["device"] is None and callable(seen["remote"])


def test_training_failures_are_logged_and_never_blank(tmp_path, monkeypatch, caplog):
    import logging

    from app.vision import balltrain

    server, client, _, _ = _trainable(tmp_path, monkeypatch)
    monkeypatch.setenv("VISION_SITE_TRAINING", "1")
    site = {"x-pitchlens-require-owner": "1", "x-pitchlens-owner": "f" * 32}
    raised = {"error": Exception("")}  # e.g. Modal's bare TimeoutError

    def failing(*args, **kwargs):
        raise raised["error"]

    monkeypatch.setattr(balltrain, "run_training", failing)
    caplog.set_level(logging.ERROR)
    assert client.post("/ball-model/train", json={}, headers=site).status_code == 200
    state = _wait_for_training(server)
    assert state["state"] == "failed" and state["stage"] == "Training failed on the GPU. Your labels are kept; try again later."
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == ["Ball-model training failed"]
    # The operator sees what failed, never an empty box.
    assert client.post("/ball-model/train", json={}).status_code == 200
    assert _wait_for_training(server)["stage"] == "Exception"
    # Label-count refusals are written for the team owner and shown as they are.
    raised["error"] = ValueError("Too few labels to train (3 balls to learn from, 2 frames to check against). Label more frames.")
    assert client.post("/ball-model/train", json={}, headers=site).status_code == 200
    assert _wait_for_training(server)["stage"] == str(raised["error"])


def test_a_restart_during_training_shows_the_run_failed(tmp_path, monkeypatch):
    server, client, _, _ = _trainable(tmp_path, monkeypatch)
    (tmp_path / "training-state.json").write_text(json.dumps({"state": "running", "stage": "Training on the GPU"}))
    server.recover_after_restart()
    state = client.get("/ball-model").json()["training"]
    assert state == {"state": "failed", "stage": "The analysis server restarted during training. Start it again."}
    assert json.loads((tmp_path / "training-state.json").read_text())["state"] == "failed"
    # A finished run is shown as it was.
    (tmp_path / "training-state.json").write_text(json.dumps({"state": "done", "stage": "Finished", "run": {"kept": True}}))
    server.recover_after_restart()
    assert client.get("/ball-model").json()["training"]["state"] == "done"


def test_training_progress_is_saved_for_a_restart(tmp_path, monkeypatch):
    import time

    server, client, _, _ = _trainable(tmp_path, monkeypatch)
    assert client.post("/ball-model/train", json={"epochs": 5}).status_code == 200
    assert json.loads((tmp_path / "training-state.json").read_text())["state"] in ("running", "done")
    assert _wait_for_training(server)["state"] == "done"
    for _ in range(200):  # saved right after the in-memory state
        if json.loads((tmp_path / "training-state.json").read_text())["state"] == "done":
            break
        time.sleep(0.01)
    assert json.loads((tmp_path / "training-state.json").read_text())["state"] == "done"


def test_a_modal_refusal_gives_the_site_start_back(tmp_path, monkeypatch):
    import sys
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.vision import balltrain

    server, client, _, _ = _trainable(tmp_path, monkeypatch)
    monkeypatch.setenv("VISION_SITE_TRAINING", "1")
    monkeypatch.setenv("VISION_MAX_TRAINING_PER_DAY", "2")
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")

    @contextmanager
    def refused():
        raise RuntimeError("AuthError: Token missing or invalid")
        yield  # pragma: no cover

    monkeypatch.setitem(sys.modules, "app.vision.modal_train",
                        SimpleNamespace(app=SimpleNamespace(run=refused), train_ball=SimpleNamespace(remote=None)))

    def run_training(root, models, base, remote=None, on_start=None, **kwargs):
        on_start()
        return remote(b"", b"", 1)

    monkeypatch.setattr(balltrain, "run_training", run_training)
    site = {"x-pitchlens-require-owner": "1", "x-pitchlens-owner": "f" * 32}
    for _ in range(2):
        assert client.post("/ball-model/train", json={}, headers=site).status_code == 200
        assert _wait_for_training(server)["state"] == "failed"
    assert server._recent_training_starts(time.time()) == []
    assert client.post("/ball-model/train", json={}, headers=site).status_code == 200
    _wait_for_training(server)


def test_a_failed_training_set_build_leaves_no_frame_copies(tmp_path, monkeypatch):
    import errno
    import pathlib

    from app.vision import balltrain

    for name in ("a" * 32, "b" * 32):
        _labelled_job(tmp_path, name)
    base = tmp_path / "base.pt"
    base.write_bytes(b"w")
    real, written = pathlib.Path.write_text, {"labels": 0}

    def write_text(self, data, *args, **kwargs):
        if self.suffix == ".txt" and "dataset" in self.parts:
            written["labels"] += 1
            if written["labels"] > 25:  # the volume fills while the set is being built
                raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", write_text)
    with pytest.raises(OSError):
        balltrain.run_training(tmp_path, tmp_path / "models", base, remote=lambda *a: None, on_start=lambda: None)
    assert written["labels"] > 25
    assert not (tmp_path / "training" / "dataset").exists()


def test_stills_written_while_a_match_is_deleted_never_outlive_it(tmp_path, monkeypatch):
    """A still landing between rmtree's listing of labelframes/ and its rmdir kept the
    folder (ignore_errors hid it); the decoder, reading the unlinked video through its
    open file, then filled it: frames of a deleted match that nothing lists or sweeps."""
    import os
    import threading
    import time

    from fastapi.testclient import TestClient

    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "t")
    monkeypatch.setattr(server, "active", None)
    monkeypatch.setattr(server, "calibrating", set())
    monkeypatch.setattr(server, "RETENTION_HOURS", 0)
    job, owner = "a" * 32, "b" * 32
    d = tmp_path / job
    d.mkdir()
    server.write_status(d, {"id": job, "owner": owner, "status": "completed", "createdAt": time.time()})
    frames = [{"t": i / 10, "frame": i, "scene": 0, "players": [], "ball": None} for i in range(300)]
    (d / "result.json").write_text(json.dumps({"frames": frames, "video": {"width": 64, "height": 48, "fps": 10}}))
    (d / "video").write_bytes(b"not decoded: extract_frames is replaced below")

    go = threading.Event()
    wrote = threading.Event()

    def fake_extract(video_path, wanted, start=None, write=None):
        # Decoding a real match: one wanted frame every so often.
        for k, (number, key) in enumerate(sorted(wanted.items())):
            if k == 1:
                go.wait(5)  # the next frame arrives while DELETE is running
            write(key, np.zeros((48, 64, 3), np.uint8))
            if k == 1:
                wrote.set()
        return {}

    monkeypatch.setattr(balllabels, "extract_frames", fake_extract)
    real_rmdir = os.rmdir

    def rmdir(path, *args, **kwargs):
        if os.fspath(path) == "labelframes" or os.fspath(path).endswith("/labelframes"):
            go.set()
            wrote.wait(0.5)  # a decoder frame landing between rmtree's listing and its rmdir
        return real_rmdir(path, *args, **kwargs)

    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers.update({"Authorization": "Bearer t", "x-pitchlens-owner": owner})
    assert client.get(f"/jobs/{job}/ball-labels").status_code == 200
    for _ in range(100):
        if (d / "labelframes").exists() and any((d / "labelframes").glob("*.jpg")):
            break
        time.sleep(0.01)
    monkeypatch.setattr(os, "rmdir", rmdir)
    assert client.delete(f"/jobs/{job}").status_code == 200
    monkeypatch.setattr(os, "rmdir", real_rmdir)
    for _ in range(500):
        if job not in server.preparing_frames:
            break
        time.sleep(0.01)
    assert job not in server.preparing_frames
    assert not d.exists()
    assert not list(tmp_path.rglob("*.jpg"))
    assert client.get("/jobs").json() == []


def test_a_withdrawn_match_gets_no_more_stills(tmp_path):
    from contextlib import contextmanager

    directory = tmp_path / "m"
    directory.mkdir()
    counter_video(directory / "video.avi")
    (directory / "video.avi").rename(directory / "video")
    data = result(30)
    entered = []

    @contextmanager
    def allowed():
        entered.append(1)
        yield

    balllabels.prepare_frames(directory, data, [1, 2], directory / "video", guard=allowed)
    assert len(entered) == 2  # entered around each write
    assert sorted(p.name for p in (directory / "labelframes").iterdir()) == ["1.jpg", "2.jpg"]

    @contextmanager
    def withdrawn():
        raise balllabels.FramesWithdrawn("m")
        yield  # pragma: no cover

    with pytest.raises(balllabels.FramesWithdrawn):
        balllabels.prepare_frames(directory, data, [3, 4, 5], directory / "video", guard=withdrawn)
    assert sorted(p.name for p in (directory / "labelframes").iterdir()) == ["1.jpg", "2.jpg"]


def test_rejected_weights_trained_on_customer_frames_are_not_kept(tmp_path):
    import hashlib

    from app.vision import balltrain

    for name in ("a" * 32, "b" * 32):
        _labelled_job(tmp_path, name)
    base = tmp_path / "base.pt"
    base.write_bytes(b"base weights")

    def worse(dataset_zip, base_bytes, epochs):
        return {"weights": b"rejected weights", **_scores(dataset_zip, 0.5, 0.4)}

    run = balltrain.run_training(tmp_path, tmp_path / "models", base, remote=worse, now=1.0)
    assert run["kept"] is False
    # A model nobody loads, derived from customers' frames, is never written.
    assert sorted(p.name for p in (tmp_path / "models").glob("ball-*.pt")) == []
    last = balltrain.registry(tmp_path / "models")["runs"][-1]
    assert last["weights"] == f"ball-{hashlib.sha256(b'rejected weights').hexdigest()[:16]}.pt"
    assert last["baseline"]["recall"] == 0.5 and last["candidate"]["recall"] < 0.5
    assert balltrain.registry(tmp_path / "models")["active"] is None


def test_a_deleted_match_stays_named_in_ball_lineage(tmp_path, monkeypatch):
    """Deliberate: footage a kept model trained on never becomes a later test set,
    even when it is deleted and uploaded again."""
    from app.vision import balltrain

    server, client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "calibrating", set())
    monkeypatch.setattr(server, "RETENTION_HOURS", 0)
    for name, sha in (("a" * 32, "1" * 64), ("b" * 32, "2" * 64), ("c" * 32, "3" * 64)):
        _labelled_job(tmp_path, name)
        r = json.loads((tmp_path / name / "result.json").read_text())
        r["videoSha256"] = sha
        (tmp_path / name / "result.json").write_text(json.dumps(r))
    base = tmp_path / "base.pt"
    base.write_bytes(b"base")
    models = tmp_path / "models"
    run = balltrain.run_training(
        tmp_path, models, base, now=1.0, remote=lambda z, b, e: {"weights": b"new", **_scores(z, 0.5, 0.9)},
    )
    assert run["kept"]
    victim = next(n for n in ("a" * 32, "b" * 32, "c" * 32) if n in (models / balltrain.LINEAGE).read_text())
    owner = json.loads((tmp_path / victim / "status.json").read_text())["owner"]
    assert client.delete(f"/jobs/{victim}", headers={"x-pitchlens-owner": owner}).status_code == 200
    assert not (tmp_path / victim).exists()
    after = json.loads((models / balltrain.LINEAGE).read_text())
    assert victim in json.dumps(after)
    assert any(k.startswith("sha:") for rec in after.values() for g in rec["groups"] for k in g["keys"])


def _site_training(tmp_path, monkeypatch, remote):
    """The worker's own training wiring (guards included), with Modal's GPU stubbed by `remote`."""
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    server, client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "training", {"state": "idle"})
    monkeypatch.setattr(server, "calibrating", set())
    monkeypatch.setattr(server, "RETENTION_HOURS", 0)
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")
    monkeypatch.delenv("VISION_USE_MODAL", raising=False)
    (tmp_path / "ball.pt").write_bytes(b"tiled ball model")
    monkeypatch.setattr("app.vision.profiles.model_paths", lambda profile="general": (tmp_path / "p.pt", tmp_path / "ball.pt"))
    monkeypatch.setitem(sys.modules, "app.vision.modal_train",
                        SimpleNamespace(app=SimpleNamespace(run=nullcontext), train_ball=SimpleNamespace(remote=remote)))
    return server, client


def _finished_training(server):
    import time

    for _ in range(3000):
        if server.training.get("state") != "running":
            return server.training
        time.sleep(0.01)
    raise AssertionError("training never finished")


def test_a_match_deleted_during_training_is_never_trained_on_or_switched_on(tmp_path, monkeypatch):
    from app.vision import balltrain

    victim = "a" * 32
    _labelled_job(tmp_path, victim, n=80)  # the largest match: always in the training part
    for name in ("b" * 32, "c" * 32):
        _labelled_job(tmp_path, name)
    models = tmp_path / "models"
    models.mkdir()
    before = {"active": "ball-0123456789abcdef.pt", "runs": []}
    (models / "ball-model.json").write_text(json.dumps(before))
    seen = {}

    def remote(dataset_zip, base_bytes, epochs):
        # The owner of match a deletes it while the GPU trains (runs can take 2 h).
        training = tmp_path / "training"
        # (Matched on the short prefix that tiles were once named with, too.)
        seen["copies"] = sum(1 for p in training.rglob("*") if p.name.startswith(victim[:8]))
        owner = json.loads((tmp_path / victim / "status.json").read_text())["owner"]
        seen["status"] = client.delete(f"/jobs/{victim}", headers={"x-pitchlens-owner": owner}).status_code
        seen["left"] = sorted(p.name for p in training.rglob("*") if p.name.startswith(victim[:8]))
        return {"weights": b"trained with a", **_scores(dataset_zip, 0.5, 0.9)}

    server, client = _site_training(tmp_path, monkeypatch, remote)
    assert client.post("/ball-model/train", json={}).status_code == 200
    state = _finished_training(server)
    assert seen["status"] == 200 and not (tmp_path / victim).exists()
    assert seen["copies"] > 0 and seen["left"] == []  # its tiles went with the DELETE
    run = state["run"]
    assert state["state"] == "done" and run["kept"] is False and run["reason"] == "match-withdrawn"
    assert state["stage"] == (
        "A match used for training was deleted or withdrawn from training during the run; nothing was switched. "
        "Start training again."
    )
    assert "weights" not in run and list(models.glob("ball-*.pt")) == []
    lineage = models / balltrain.LINEAGE
    assert not lineage.exists() or victim not in lineage.read_text()
    registry = balltrain.registry(models)
    assert registry["active"] == before["active"] and registry["runs"][-1]["reason"] == "match-withdrawn"


def test_the_model_is_switched_under_the_lock_delete_holds(tmp_path, monkeypatch):
    from app.vision import balltrain

    for name in ("a" * 32, "b" * 32, "c" * 32):
        _labelled_job(tmp_path, name)
    server, client = _site_training(tmp_path, monkeypatch, lambda z, b, e: {"weights": b"better", **_scores(z, 0.5, 0.9)})
    real, held = balltrain.better, []

    def better(candidate, baseline, **kwargs):
        held.append(server.label_lock.locked())
        return real(candidate, baseline, **kwargs)

    monkeypatch.setattr(balltrain, "better", better)
    assert client.post("/ball-model/train", json={}).status_code == 200
    state = _finished_training(server)
    assert state["state"] == "done" and state["run"]["kept"], state
    assert held == [True]
    assert balltrain.registry(tmp_path / "models")["active"] == state["run"]["weights"]


def test_a_match_deleted_while_the_training_set_is_built_stops_the_run(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from app.vision import balltrain

    for name in ("a" * 32, "b" * 32):
        _labelled_job(tmp_path, name)
    base = tmp_path / "base.pt"
    base.write_bytes(b"w")
    trained = []

    @contextmanager
    def gone():
        raise balllabels.FramesWithdrawn("a" * 32)
        yield  # pragma: no cover

    withdrawn = "A match was deleted while the training set was being built; nothing was trained. Start training again."
    with pytest.raises(ValueError) as refused:
        balltrain.run_training(tmp_path, tmp_path / "models", base, remote=lambda *a: trained.append(1), frame_guard=lambda d: gone)
    assert str(refused.value) == withdrawn
    assert not trained and not (tmp_path / "training" / "dataset").exists()
    assert not list((tmp_path / ("a" * 32) / "labelframes").glob("*.jpg"))

    # DELETE removed a staged tile the build was about to move: the match is gone, so say so.
    def build(train, test, out, **kwargs):
        (tmp_path / ("a" * 32) / "status.json").unlink()
        raise FileNotFoundError(2, "No such file or directory", str(out / "staging" / f"{'a' * 32}-3-1.jpg"))

    monkeypatch.setattr(balltrain, "build_dataset", build)
    with pytest.raises(ValueError) as refused:
        balltrain.run_training(tmp_path, tmp_path / "models", base, remote=lambda *a: trained.append(1))
    assert str(refused.value) == withdrawn and not trained


def test_gpu_training_receives_bytes_never_credentials(tmp_path, monkeypatch):
    """train_ball gets the dataset and the base weights as bytes: no service token,
    no owner key, no worker address, so nothing it holds reaches the worker."""
    import io
    import sys
    import zipfile
    from contextlib import nullcontext
    from types import SimpleNamespace

    server, client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "training", {"state": "idle"})
    monkeypatch.setenv("VISION_DEVICE", "cpu")
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")
    monkeypatch.setenv("VISION_PUBLIC_URL", "https://worker.example")
    monkeypatch.setenv("VISION_SITE_TRAINING", "1")
    (tmp_path / "ball.pt").write_bytes(b"tiled ball model")
    monkeypatch.setattr("app.vision.profiles.model_paths", lambda profile="general": (tmp_path / "p.pt", tmp_path / "ball.pt"))
    owner = "f" * 32
    for name in ("a" * 32, "a" * 31 + "b"):
        _labelled_job(tmp_path, name)
    calls = []

    def remote(*args, **kwargs):
        calls.append((args, kwargs))
        return {"weights": b"new", **_scores(args[0], 0.5, 0.4)}

    monkeypatch.setitem(sys.modules, "app.vision.modal_train",
                        SimpleNamespace(app=SimpleNamespace(run=nullcontext), train_ball=SimpleNamespace(remote=remote)))
    site = {"x-pitchlens-require-owner": "1", "x-pitchlens-owner": owner}
    assert client.post("/ball-model/train", json={"epochs": 5}, headers=site).status_code == 200
    _wait_for_training(server)
    (dataset, base, epochs), kwargs = calls[0]
    assert isinstance(dataset, bytes) and base == b"tiled ball model" and epochs == 5 and kwargs == {}
    archive = zipfile.ZipFile(io.BytesIO(dataset))
    blob = b"".join(archive.read(n) for n in archive.namelist()) + "\n".join(archive.namelist()).encode()
    for secret in (b"test-token", owner.encode(), b"worker.example", b"as-test"):
        assert secret not in blob


def _zipped_jobs(seen):
    """A GPU stub that records which jobs' tiles and test frames were in the dataset zip."""
    import io
    import zipfile
    from pathlib import Path

    def remote(dataset_zip, base_bytes, epochs):
        for name in zipfile.ZipFile(io.BytesIO(dataset_zip)).namelist():
            if name.endswith(".jpg"):
                seen.add(Path(name).name.split("-")[0])  # files are named after the full job id
        return {"weights": b"new weights", **_scores(dataset_zip, 0.5, 0.9)}

    return remote


def test_training_uses_only_matches_whose_owner_agreed(tmp_path):
    from app.vision import balltrain

    # Owner f...f labelled match a and never agreed; owner e...e agreed for b and c.
    _labelled_job(tmp_path, "a" * 32, n=80, consent=False)
    for name in ("b" * 32, "c" * 32):
        _labelled_job(tmp_path, name)
    base = tmp_path / "base.pt"
    base.write_bytes(b"base weights")
    assert [d.name for d in balltrain.labelled_jobs(tmp_path)] == ["b" * 32, "c" * 32]
    for owner in (None, "e" * 32):  # an operator run, and the agreeing owner's own run
        seen = set()
        run = balltrain.run_training(tmp_path, tmp_path / f"models-{owner}", base, owner=owner, remote=_zipped_jobs(seen), now=1.0)
        # Neither a training tile nor a test frame of match a went to the GPU.
        assert seen == {"b" * 32, "c" * 32} and run["matches"] == 2 and run["kept"], owner
    with pytest.raises(ValueError, match="allow training"):
        balltrain.run_training(tmp_path, tmp_path / "models-f", base, owner="f" * 32, remote=_zipped_jobs(set()))


def test_a_withdrawn_match_leaves_the_training_set(tmp_path, monkeypatch):
    from app.vision import balltrain

    server, client = _client(tmp_path, monkeypatch)
    for name in ("a" * 32, "b" * 32, "c" * 32):
        _labelled_job(tmp_path, name)
    assert len(balltrain.labelled_jobs(tmp_path)) == 3
    owner = {"x-pitchlens-owner": "f" * 32}
    response = client.post(f"/jobs/{'a' * 32}/training-consent", json={"agreed": False}, headers=owner)
    assert response.status_code == 200 and response.json() == {"trainingConsent": False}
    status = json.loads((tmp_path / ("a" * 32) / "status.json").read_text())
    assert status["trainingConsent"]["agreed"] is False and status["trainingConsent"]["at"] > 0
    jobs = balltrain.labelled_jobs(tmp_path)
    assert [d.name for d in jobs] == ["b" * 32, "c" * 32]
    planned = balltrain.plan(jobs)
    assert {d.name for d, _ in planned["train"] + planned["test"]} == {"b" * 32, "c" * 32}
    # Its labels stay: they still measure the ball finder on that match.
    assert balllabels.load(tmp_path / ("a" * 32) / "balllabels.json")["labels"]


def test_a_withdrawal_during_training_discards_the_run(tmp_path, monkeypatch):
    from app.vision import balltrain

    victim = "a" * 32
    _labelled_job(tmp_path, victim, n=80)  # the largest match: always in the training part
    for name in ("b" * 32, "c" * 32):
        _labelled_job(tmp_path, name)
    models = tmp_path / "models"
    models.mkdir()
    before = {"active": "ball-0123456789abcdef.pt", "runs": []}
    (models / "ball-model.json").write_text(json.dumps(before))
    seen = {}

    def remote(dataset_zip, base_bytes, epochs):
        # The owner of match a withdraws consent while the GPU trains.
        seen["status"] = client.post(f"/jobs/{victim}/training-consent", json={"agreed": False},
                                     headers={"x-pitchlens-owner": "f" * 32}).status_code
        return {"weights": b"trained with a", **_scores(dataset_zip, 0.5, 0.9)}

    server, client = _site_training(tmp_path, monkeypatch, remote)
    assert client.post("/ball-model/train", json={}).status_code == 200
    state = _finished_training(server)
    assert seen["status"] == 200 and (tmp_path / victim / "status.json").is_file()
    run = state["run"]
    assert state["state"] == "done" and run["kept"] is False and run["reason"] == "match-withdrawn"
    assert "weights" not in run and list(models.glob("ball-*.pt")) == []
    lineage = models / balltrain.LINEAGE
    assert not lineage.exists() or victim not in lineage.read_text()
    registry = balltrain.registry(models)
    assert registry["active"] == before["active"] and registry["runs"][-1]["reason"] == "match-withdrawn"


def test_only_the_owner_sets_training_consent_with_a_boolean(tmp_path, monkeypatch):
    import time

    server, client = _client(tmp_path, monkeypatch)
    # The listing starts extracting label stills in the background: not needed here.
    monkeypatch.setattr(balllabels, "extract_frames", lambda *args, **kwargs: {})
    job = "a" * 32
    _labelled_job(tmp_path, job, consent=False)
    owner, stranger = {"x-pitchlens-owner": "f" * 32}, {"x-pitchlens-owner": "e" * 32}
    url = f"/jobs/{job}/training-consent"
    assert client.post(url, json={"agreed": True}, headers=stranger).status_code == 404
    assert client.post(url, json={"agreed": True}).status_code == 404  # no owner key
    for bad in ({}, {"agreed": 1}, {"agreed": "true"}, {"agreed": None}, {"agreed": True, "x": 1}, [True]):
        assert client.post(url, json=bad, headers=owner).status_code == 400, bad
    for raw in (b'{"agreed": NaN}', b"not json", b'{"agreed": true, "pad": "' + b"x" * 5000 + b'"}'):
        assert client.post(url, content=raw, headers={**owner, "content-type": "application/json"}).status_code == 400
    assert "trainingConsent" not in json.loads((tmp_path / job / "status.json").read_text())
    assert client.get(f"/jobs/{job}/ball-labels", headers=owner).json()["trainingConsent"] is False
    assert client.post(url, json={"agreed": True}, headers=owner).json() == {"trainingConsent": True}
    assert client.get(f"/jobs/{job}/ball-labels", headers=owner).json()["trainingConsent"] is True
    assert client.post(url, json={"agreed": False}, headers=owner).json() == {"trainingConsent": False}
    assert client.get(f"/jobs/{job}/ball-labels", headers=owner).json()["trainingConsent"] is False
    assert client.post(f"/jobs/{'d' * 32}/training-consent", json={"agreed": True}, headers=owner).status_code == 404
    for _ in range(500):
        if job not in server.preparing_frames:
            break
        time.sleep(0.01)
