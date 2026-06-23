from pathlib import Path

import web.backend.services.backup_service as backup_service
from web.backend.services.backup_service import (
    create_snapshot,
    dry_run_import,
    export_snapshot,
    list_backups,
    migrate_dry_run,
    verify_snapshot,
)


def test_create_and_verify_snapshot():
    out = create_snapshot()
    path = Path(out['path'])
    assert path.exists()
    assert (path / 'manifest.json').exists()
    verified = verify_snapshot(str(path))
    assert verified['ok'] is True


def test_export_and_dry_run_import_snapshot():
    out = create_snapshot()
    path = Path(out['path'])
    exported = export_snapshot(str(path))
    assert exported['ok'] is True
    bundle = Path(exported['data']['bundle_path'])
    assert bundle.exists()
    preview = dry_run_import(str(path))
    assert preview['ok'] is True
    assert preview['data']['actions']


def test_list_backups_classifies_export_bundles_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, 'BACKUP_ROOT', tmp_path)
    snap = tmp_path / 'mycelium-backup-20260101-000000-000000'
    snap.mkdir()
    (snap / 'manifest.json').write_text(
        '{"created_at":"2026-01-01T00:00:00Z","total_bytes":123}'
    )
    bundle = tmp_path / 'mycelium-backup-20260101-000000-000000.tar.gz'
    bundle.write_bytes(b'bundle')
    invalid_bundle = tmp_path / 'mycelium-backup-20260102-000000-000000.tar.gz'
    invalid_bundle.touch()

    listed = list_backups()

    assert [item['name'] for item in listed['items']] == [snap.name]
    assert [item['name'] for item in listed['bundles']] == [invalid_bundle.name, bundle.name]
    assert all(bundle['total_bytes'] >= 0 for bundle in listed['bundles'])
    assert all(item['name'].endswith('.tar.gz') for item in listed['bundles'])


def test_migrate_dry_run_shape(tmp_path):
    preview = migrate_dry_run(str(tmp_path / 'new-runtime'))
    assert preview['ok'] is True
    assert 'mappings' in preview['data']
    assert preview['data']['target_root'].endswith('new-runtime')
