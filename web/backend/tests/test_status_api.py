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
    data = res.json()
    assert 'total_turns' in data
    assert 'canonical_runtime' in data


def test_stream_shape():
    res = client.get('/api/stream?limit=5')
    assert res.status_code == 200
    data = res.json()
    assert 'items' in data
    assert 'total' in data


def test_backup_workflow_routes():
    created = create_snapshot()
    path = created['path']

    res = client.post('/api/backups/verify', json={'path': path})
    assert res.status_code == 200
    assert res.json()['ok'] is True

    res = client.post('/api/import/dry-run', json={'path': path})
    assert res.status_code == 200
    assert res.json()['ok'] is True

    res = client.post('/api/migrate/dry-run', json={'target_root': '/tmp/mycelium-target'})
    assert res.status_code == 200
    assert res.json()['ok'] is True
