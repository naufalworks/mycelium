import io
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from web.backend.app import app
from web.backend.services import backup_service as backup_module
from web.backend.services import status_service as status_module


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


def test_connections():
    res = client.get("/api/connections")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "nodes" in data
    assert "links" in data


def test_recall_api():
    res = client.get("/api/recall", params={"q": "mycelium", "limit": 5})
    assert res.status_code == 200
    data = res.json()
    assert "ok" in data
    assert data["query"] == "mycelium"


def test_frontend_fallback_route():
    res = client.get('/')
    assert res.status_code == 200
    assert 'ok' in res.text or '<!doctype html>' in res.text.lower()


def test_backups_list_and_create_isolated(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'log.jsonl').write_text('{"session":"s1"}\n')
    (runtime / 'index.db').write_text('db')
    backup_root = tmp_path / 'backups'

    monkeypatch.setattr(backup_module, 'BACKUP_ROOT', backup_root)
    monkeypatch.setattr(backup_module, 'resolve_canonical_root', lambda: runtime)
    monkeypatch.setattr(status_module, 'RUNTIME_ROOT', runtime)

    backup_module.create_snapshot()
    body = backup_module.list_backups()
    assert len(body['items']) >= 1


def test_verify_snapshot_rejects_traversal_archive(tmp_path, monkeypatch):
    backup_root = tmp_path / 'backups'
    monkeypatch.setattr(backup_module, 'BACKUP_ROOT', backup_root)

    tar_path = tmp_path / 'evil.tar.gz'
    with tarfile.open(tar_path, 'w:gz') as tf:
        payload = b'owned'
        info = tarfile.TarInfo('../evil.txt')
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    result = backup_module.verify_snapshot(str(tar_path))
    assert result['ok'] is False
    assert 'unsafe archive member' in result['message']


def test_dry_run_rejects_runtime_target(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setattr(backup_module, 'resolve_canonical_root', lambda: runtime)
    monkeypatch.setattr(backup_module, 'SOURCE_ROOT', source)

    result = backup_module.migrate_dry_run(str(runtime))
    assert result['ok'] is False
    assert 'canonical runtime root' in result['message']


def test_cors_restricted_localhost_origin():
    res = client.options(
        '/api/health',
        headers={
            'Origin': 'http://127.0.0.1:8420',
            'Access-Control-Request-Method': 'GET',
        },
    )
    assert res.status_code == 200
    assert res.headers['access-control-allow-origin'] == 'http://127.0.0.1:8420'
