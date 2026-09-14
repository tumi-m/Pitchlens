import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.models.match import ProcessMatchRequest, PassNetworkEdge


def test_health_without_cloud_credentials():
    response = TestClient(app).get('/api/v1/health')
    assert response.status_code == 200
    assert response.json()['experimentalAnalyticsEnabled'] is False


def test_pass_network_uses_frontend_edge_contract():
    edge = PassNetworkEdge(fromId='a', toId='b', count=3, accuracy=.8)
    assert edge.model_dump(by_alias=True) == {'from': 'a', 'to': 'b', 'count': 3, 'accuracy': .8}


@pytest.mark.parametrize('url', ['http://127.0.0.1/a.mp4', 'https://example.com/a.mp4', 'https://storage.googleapis.com/wrong/a.mp4', 'https://storage.googleapis.com.evil.test/bucket/a.mp4'])
def test_download_url_is_bucket_scoped(monkeypatch, url):
    monkeypatch.setenv('FIREBASE_STORAGE_BUCKET', 'bucket')
    with pytest.raises(ValidationError):
        ProcessMatchRequest(matchId='valid', videoUrl=url)


def test_valid_signed_url(monkeypatch):
    monkeypatch.setenv('FIREBASE_STORAGE_BUCKET', 'bucket')
    assert ProcessMatchRequest(matchId='valid', videoUrl='https://storage.googleapis.com/bucket/videos/a.mp4?signature=x').matchId == 'valid'


def test_invalid_id(monkeypatch):
    monkeypatch.setenv('FIREBASE_STORAGE_BUCKET', 'bucket')
    with pytest.raises(ValidationError):
        ProcessMatchRequest(matchId='../other', videoUrl='https://storage.googleapis.com/bucket/a.mp4')
