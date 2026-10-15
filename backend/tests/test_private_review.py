"""Customer data access, retry safety and complete deletion regressions."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.vision.access import model_grant, video_grant


@pytest.fixture
def private_job(tmp_path, monkeypatch):
    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    monkeypatch.setattr(server, "active", None)
    monkeypatch.setattr(server, "calibrating", set())
    monkeypatch.setattr(server, "_prepared", {})
    monkeypatch.setattr(server, "RETENTION_HOURS", 0)
    job, owner = "a" * 32, "b" * 32
    directory = tmp_path / job
    directory.mkdir()
    server.write_status(
        directory, {"id": job, "owner": owner, "status": "completed", "createdAt": time.time()}
    )
    (directory / "video").write_bytes(b"0123456789")
    (directory / "result.json").write_text("{}")

    def analysis(path):
        decisions = server._read_json(path / "review.json", {"decisions": []})["decisions"]
        return {"events": [], "review": {"decisions": len(decisions)}}

    monkeypatch.setattr(server, "compute_analysis", analysis)
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers.update({"Authorization": "Bearer test-token", "x-pitchlens-owner": owner})
    return server, client, job, directory


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", ""),
        ("GET", "/video"),
        ("GET", "/result"),
        ("GET", "/analysis"),
        ("GET", "/review"),
        ("GET", "/calibration"),
        ("POST", "/review"),
        ("POST", "/details"),
        ("POST", "/cancel"),
        ("POST", "/start"),
        ("POST", "/calibration"),
        ("POST", "/calibration/preview"),
        ("PUT", "/video"),
        ("DELETE", ""),
    ],
)
def test_other_owner_cannot_access_any_match_resource(private_job, method, suffix):
    _, client, job, _ = private_job
    response = client.request(
        method, f"/jobs/{job}{suffix}", headers={"x-pitchlens-owner": "c" * 32}
    )
    assert response.status_code == 404


def test_private_playback_requires_owner_and_preserves_ranges(private_job):
    _, client, job, _ = private_job
    response = client.get(f"/jobs/{job}/video", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    del client.headers["x-pitchlens-owner"]
    assert client.get(f"/jobs/{job}").status_code == 404


def test_worker_grant_only_reads_its_video_until_expiry(private_job, monkeypatch):
    server, client, job, _ = private_job
    monkeypatch.setattr(server, "engines", {job: "gpu"})
    del client.headers["x-pitchlens-owner"]
    del client.headers["Authorization"]  # a GPU run holds no service token
    grant = video_grant("test-token", job)
    headers = {"x-pitchlens-video-grant": grant}
    assert client.get(f"/jobs/{job}/video", headers=headers).status_code == 200
    assert client.get(f"/jobs/{job}/result", headers=headers).status_code == 401
    for wrong in (
        video_grant("test-token", "d" * 32),
        video_grant("test-token", job, time.time() - 1),
    ):
        assert (
            client.get(f"/jobs/{job}/video", headers={"x-pitchlens-video-grant": wrong}).status_code
            == 401
        )


def test_legacy_unowned_matches_never_leak_in_private_listing(private_job):
    server, client, job, directory = private_job
    status = server.load_status(directory)
    status.pop("owner")
    server.write_status(directory, status)
    assert client.get("/jobs").json() == []
    assert client.get(f"/jobs/{job}", headers={"x-pitchlens-require-owner": "1"}).status_code == 404
    del client.headers["x-pitchlens-owner"]
    assert client.get("/jobs", headers={"x-pitchlens-require-owner": "1"}).status_code == 400


def test_retried_add_is_exactly_once_and_conflicting_reuse_fails(private_job):
    _, client, job, directory = private_job
    body = {
        "requestId": "d" * 32,
        "expectedRevision": 0,
        "decisions": [{"action": "add", "type": "goal", "t": 1, "team": 0}],
    }
    first = client.post(f"/jobs/{job}/review", json=body)
    second = client.post(f"/jobs/{job}/review", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["added"] == second.json()["added"]
    assert second.json()["decisions"] == 1
    log = json.loads((directory / "review.json").read_text())
    assert len(log["decisions"]) == 1
    body["decisions"][0]["team"] = 1
    assert client.post(f"/jobs/{job}/review", json=body).status_code == 409


def test_stale_tab_cannot_overwrite_score(private_job):
    _, client, job, directory = private_job
    body = {
        "requestId": "d" * 32,
        "expectedRevision": 0,
        "decisions": [{"action": "score", "value": [3, 1]}],
    }
    assert client.post(f"/jobs/{job}/review", json=body).status_code == 200
    body.update(requestId="e" * 32, decisions=[{"action": "score", "value": [0, 0]}])
    assert client.post(f"/jobs/{job}/review", json=body).status_code == 409
    assert len(json.loads((directory / "review.json").read_text())["decisions"]) == 1


def test_delete_removes_video_reviews_cache_and_derived_venues(private_job):
    server, client, job, directory = private_job
    (directory / "review.json").write_text('{"decisions":[]}')
    server._prepared[(job, 6)] = {"private": True}
    venue = server._venue_dir() / "venue.json"
    venue.write_text(json.dumps({"sourceJob": job}))
    assert client.delete(f"/jobs/{job}").status_code == 200
    assert not directory.exists() and not venue.exists() and not server._prepared
    assert client.get(f"/jobs/{job}").status_code == 404


def test_delete_and_venue_copy_cannot_bypass_ownership_or_active_work(private_job):
    server, client, job, directory = private_job
    assert (
        client.post(
            "/venues",
            json={"jobId": job, "name": "Stolen"},
            headers={"x-pitchlens-owner": "e" * 32},
        ).status_code
        == 404
    )
    server.active = job
    assert client.delete(f"/jobs/{job}").status_code == 409
    server.active = None
    server.calibrating.add(job)
    assert client.delete(f"/jobs/{job}").status_code == 409
    assert directory.exists()


def test_retry_reuses_complete_upload_once_and_enforces_budget(private_job, monkeypatch):
    from types import SimpleNamespace

    server, client, job, directory = private_job
    submitted = []
    monkeypatch.setattr(
        server, "pool", SimpleNamespace(submit=lambda *args: submitted.append(args))
    )
    monkeypatch.setattr(server, "available_profiles", lambda: ["general"])
    status = server.load_status(directory)
    status.update(status="interrupted", video={"duration": 60}, attempts=1)
    server.write_status(directory, status)
    first = client.post(f"/jobs/{job}/retry")
    assert first.status_code == 200 and first.json()["attempts"] == 2
    assert client.post(f"/jobs/{job}/retry").status_code == 200
    assert len(submitted) == 1 and (directory / "video").read_bytes() == b"0123456789"
    server.active = None
    status.update(status="failed", attempts=3)
    server.write_status(directory, status)
    assert client.post(f"/jobs/{job}/retry").status_code == 409
    assert len(submitted) == 1
    server.cancellations.pop(job, None)


def test_retry_rejects_other_owner_and_incomplete_upload(private_job):
    server, client, job, directory = private_job
    assert (
        client.post(f"/jobs/{job}/retry", headers={"x-pitchlens-owner": "e" * 32}).status_code
        == 404
    )
    status = server.load_status(directory)
    status.update(status="interrupted")
    server.write_status(directory, status)
    assert client.post(f"/jobs/{job}/retry").status_code == 409


def test_hosted_full_match_does_not_silently_fall_back_to_cpu(private_job, monkeypatch):
    import threading

    server, _, job, directory = private_job
    monkeypatch.setattr(server.gpu, "public_base", lambda: "https://worker.example")
    monkeypatch.setattr(server.gpu, "modal_enabled", lambda: True)
    monkeypatch.delenv("VISION_ALLOW_CPU_FALLBACK", raising=False)

    def unavailable(*args, **kwargs):
        raise server.gpu.GPUUnavailable("no GPU")

    monkeypatch.setattr(server.gpu, "run_on_modal", unavailable)

    def no_cpu(*args, **kwargs):
        pytest.fail("A hosted full match must not silently use CPU")

    monkeypatch.setattr(server, "run_video", no_cpu)
    server.work(directory, server.load_status(directory), threading.Event())
    status = server.load_status(directory)
    assert status["status"] == "failed" and "upload is saved" in status["stage"]
    assert (directory / "video").is_file()


def test_gpu_grants_open_only_their_own_job_video_and_weights_while_it_runs(private_job, monkeypatch, tmp_path):
    """What a Modal run holds (its grants) reads that job's video and the weights it was
    granted, nothing else: no other route, job or file, no Bearer use, not after expiry,
    not once the run stops using the GPU."""
    server, client, job, directory = private_job
    other = "d" * 32
    (tmp_path / other).mkdir()
    server.write_status(tmp_path / other, {"id": other, "owner": "e" * 32, "status": "processing", "createdAt": time.time()})
    (tmp_path / other / "video").write_bytes(b"someone else")
    weights = tmp_path / "football-ball.onnx"
    weights.write_bytes(b"weights")
    monkeypatch.setenv("VISION_BALL_MODEL_PATH", str(weights))
    monkeypatch.setenv("VISION_PUBLIC_URL", "https://worker.example")  # hosted: owners required
    engines = {job: "gpu", other: "gpu"}
    monkeypatch.setattr(server, "engines", engines)
    client.headers.clear()
    video = {"x-pitchlens-video-grant": video_grant("test-token", job)}
    model = {"x-pitchlens-model-grant": model_grant("test-token", job, "football-ball.onnx")}

    assert client.get(f"/jobs/{job}/video", headers=video).content == b"0123456789"
    assert client.get("/models/football-ball.onnx", headers=model).content == b"weights"
    # Every other route, and other jobs, still need the service token (and an owner).
    for method, path in (("GET", f"/jobs/{job}"), ("GET", f"/jobs/{job}/result"), ("GET", f"/jobs/{job}/analysis"),
                         ("GET", "/ops"), ("GET", "/jobs"), ("POST", "/jobs?size=10"), ("DELETE", f"/jobs/{job}"),
                         ("POST", f"/jobs/{job}/cancel"), ("GET", "/ball-model"),
                         ("GET", f"/jobs/{other}/video"), ("GET", "/models/yolo11s.pt")):
        for headers in (video, model, {"Authorization": f"Bearer {video['x-pitchlens-video-grant']}"}):
            assert client.request(method, path, headers=headers).status_code == 401, (method, path, headers)
    # A grant for one purpose never passes as the other; nor a forged or stale one.
    assert client.get(f"/jobs/{job}/video", headers={"x-pitchlens-video-grant": model["x-pitchlens-model-grant"]}).status_code == 401
    assert client.get("/models/football-ball.onnx", headers={"x-pitchlens-model-grant": video["x-pitchlens-video-grant"]}).status_code == 401
    for bad in (video_grant("wrong-token", job), video_grant("test-token", job, time.time() - 1),
                video_grant("test-token", job, time.time() + 5 * 3600), video["x-pitchlens-video-grant"].upper(), ""):
        assert client.get(f"/jobs/{job}/video", headers={"x-pitchlens-video-grant": bad}).status_code == 401
    for bad in (model_grant("test-token", other, "football-ball.onnx").replace(other, job),
                model_grant("test-token", job, "football-ball.onnx", time.time() - 1),
                model_grant("wrong-token", job, "football-ball.onnx")):
        assert client.get("/models/football-ball.onnx", headers={"x-pitchlens-model-grant": bad}).status_code == 401
    # Once the run left the GPU (finished, cancelled, CPU fallback) its grants are dead.
    engines[job] = "cpu"
    assert client.get(f"/jobs/{job}/video", headers=video).status_code == 401
    del engines[job]
    assert client.get("/models/football-ball.onnx", headers=model).status_code == 401
