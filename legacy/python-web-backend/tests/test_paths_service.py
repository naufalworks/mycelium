from pathlib import Path

from web.backend.services import paths_service


def test_env_overrides_paths(tmp_path, monkeypatch):
    source = tmp_path / "src"
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("MYCELIUM_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MYCELIUM_RUNTIME_ROOT", str(runtime))

    paths = paths_service.get_paths()

    assert paths.source_root == source
    assert paths.runtime_root == runtime
    assert paths.log == runtime / "log.jsonl"
    assert paths.index == runtime / "index.db"
    assert paths.archive == runtime / "archive"


def test_split_brain_detects_source_files_not_resolving_to_runtime(tmp_path, monkeypatch):
    source = tmp_path / "src"
    runtime = tmp_path / "runtime"
    source.mkdir()
    runtime.mkdir()
    (source / "log.jsonl").write_text("{}\n")
    (source / "index.db").write_text("db")
    (source / "archive").mkdir()
    monkeypatch.setenv("MYCELIUM_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MYCELIUM_RUNTIME_ROOT", str(runtime))

    warnings = paths_service.detect_split_brain_warnings()

    assert len(warnings) == 3
    assert {w["name"] for w in warnings} == {"log.jsonl", "index.db", "archive"}
    assert all(w["source_path"].startswith(str(source)) for w in warnings)
    assert all(w["runtime_path"].startswith(str(runtime)) for w in warnings)


def test_split_brain_allows_source_symlinks_resolving_to_runtime(tmp_path, monkeypatch):
    source = tmp_path / "src"
    runtime = tmp_path / "runtime"
    source.mkdir()
    runtime.mkdir()
    (runtime / "log.jsonl").write_text("{}\n")
    (runtime / "index.db").write_text("db")
    (runtime / "archive").mkdir()
    (source / "log.jsonl").symlink_to(runtime / "log.jsonl")
    (source / "index.db").symlink_to(runtime / "index.db")
    (source / "archive").symlink_to(runtime / "archive")
    monkeypatch.setenv("MYCELIUM_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MYCELIUM_RUNTIME_ROOT", str(runtime))

    assert paths_service.detect_split_brain_warnings() == []
