from fastapi.testclient import TestClient

from web.backend.app import app
from web.backend.services.backup_service import create_snapshot

client = TestClient(app)


def test_health():
    res = client.get('/api/health')
    assert res.status_code == 200
    assert res.json()['ok'] is True


def test_status_shape():
    res = client.get('/api/status')
    assert res.status_code == 200
    body = res.json()
    assert 'total_turns' in body
    assert 'canonical_runtime' in body


def test_backups_list_and_create():
    create_snapshot()
    res = client.get('/api/backups')
    assert res.status_code == 200
    body = res.json()
    assert 'items' in body
    assert len(body['items']) >= 1


def test_connections_api():
    res = client.get('/api/connections')
    assert res.status_code == 200
    body = res.json()
    assert body['ok'] is True
    assert 'nodes' in body
    assert 'links' in body


def test_frontend_fallback_route():
    res = client.get('/')
    assert res.status_code == 200
    assert 'ok' in res.text or '<!doctype html>' in res.text.lower()
