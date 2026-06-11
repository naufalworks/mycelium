from pathlib import Path

from web.backend.services.backup_service import (
    create_snapshot,
    dry_run_import,
    export_snapshot,
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


def test_migrate_dry_run_shape(tmp_path):
    preview = migrate_dry_run(str(tmp_path / 'new-runtime'))
    assert preview['ok'] is True
    assert 'mappings' in preview['data']
    assert preview['data']['target_root'].endswith('new-runtime')
