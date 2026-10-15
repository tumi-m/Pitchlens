"""The public worker must not describe its private API to anyone without the token."""
from fastapi.testclient import TestClient


def test_worker_schema_needs_the_service_token(tmp_path, monkeypatch):
    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "test-token")
    client = TestClient(server.app, raise_server_exceptions=False)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code in (401, 404), path
    assert client.get("/healthz").status_code == 200  # the platform check still answers
