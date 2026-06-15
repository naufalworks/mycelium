#!/usr/bin/env python3
"""Tests for object_store.py — content-addressed storage."""
import json
import os
import sys
import tempfile
from pathlib import Path

# Add scripts/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

# Use a temp dir for tests to avoid polluting real index
_tmp = tempfile.mkdtemp(prefix="mycelium_objtest_")
os.environ["MYCELIUM_TEST_DIR"] = _tmp

from mycelium_lib import INDEX, init_index
from object_store import ObjectStore, _content_hash


@pytest.fixture
def store(tmp_path):
    """Fresh ObjectStore in a temp dir, with a fresh index DB."""
    db_path = tmp_path / "test_index.db"
    # Patch INDEX for this test
    import mycelium_lib
    orig = mycelium_lib.INDEX
    mycelium_lib.INDEX = db_path
    init_index(db_path)

    s = ObjectStore(base_path=tmp_path / "objects")
    yield s
    mycelium_lib.INDEX = orig


class TestContentHash:
    def test_deterministic(self):
        e = {"type": "talk", "user": "hi"}
        assert _content_hash(e) == _content_hash(e)

    def test_sorted_keys(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert _content_hash(a) == _content_hash(b)

    def test_unicode(self):
        e = {"text": "日本語テスト"}
        h = _content_hash(e)
        assert len(h) == 16


class TestPutGet:
    def test_put_get_roundtrip(self, store):
        entry = {"type": "talk", "user": "hello", "assistant": "hi back"}
        h = store.put(entry, session="test-sess")
        assert len(h) == 16
        got = store.get(h)
        assert got is not None
        assert got["user"] == "hello"
        assert got["assistant"] == "hi back"

    def test_get_missing(self, store):
        assert store.get("0" * 16) is None


class TestDedup:
    def test_content_dedup(self, store):
        entry = {"type": "finding", "detail": "xss in /api"}
        h1 = store.put(entry, session="s1")
        h2 = store.put(entry, session="s2")
        assert h1 == h2
        # Only one file on disk
        files = list(store.base_path.glob("*.json"))
        assert len(files) == 1

    def test_different_content_different_hash(self, store):
        h1 = store.put({"a": 1}, session="s1")
        h2 = store.put({"a": 2}, session="s1")
        assert h1 != h2
        assert len(list(store.base_path.glob("*.json"))) == 2


class TestRefCount:
    def test_ref_counting(self, store):
        entry = {"type": "talk", "msg": "repeated"}
        for i in range(3):
            store.put(entry, session=f"s{i}")
        h = _content_hash(entry)
        assert store.ref_count(h) == 3

    def test_add_ref(self, store):
        h = store.put({"x": 1}, session="s1")
        new_rc = store.add_ref(h, session="s2")
        assert new_rc == 2


class TestDedupCandidates:
    def test_dedup_candidates(self, store):
        # Single ref
        h1 = store.put({"unique": True}, session="s1")
        # Triple ref
        dup = {"dup": "yes"}
        for i in range(3):
            store.put(dup, session=f"s{i}")

        candidates = store.dedup_candidates(min_refs=2)
        assert len(candidates) == 1
        assert candidates[0]["content_hash"] == _content_hash(dup)
        assert candidates[0]["ref_count"] == 3


class TestVerify:
    def test_verify_ok(self, store):
        store.put({"ok": True}, session="s1")
        result = store.verify()
        assert result["ok"] is True
        assert result["total_in_db"] == 1

    def test_verify_catches_missing(self, store):
        h = store.put({"will_delete": True}, session="s1")
        # Delete the file
        (store.base_path / f"{h}.json").unlink()
        result = store.verify()
        assert result["ok"] is False
        assert h in result["missing"]


class TestBuildFromLog:
    def test_build_from_log(self, store, tmp_path):
        # Create a small log.jsonl
        log_path = tmp_path / "log.jsonl"
        entry_a = {"type": "talk", "user": "hi", "assistant": "hello", "session": "test"}
        entry_b = {"type": "finding", "user": "found bug", "assistant": "ok", "session": "test"}
        with open(log_path, "w") as f:
            # Write 3 entries, 1 and 3 identical → 2 unique objects
            f.write(json.dumps(entry_a) + "\n")
            f.write(json.dumps(entry_b) + "\n")
            f.write(json.dumps(entry_a) + "\n")  # dup of first

        count = store.build_from_log(log_path)
        assert count == 3
        # 2 unique objects
        files = list(store.base_path.glob("*.json"))
        assert len(files) == 2


class TestStats:
    def test_stats(self, store):
        store.put({"a": 1}, session="s1")
        store.put({"b": 2}, session="s1")
        store.put({"b": 2}, session="s2")  # dup → ref_count=2

        s = store.stats()
        assert s["total_objects"] == 2
        assert s["total_refs"] == 3
        assert s["dedup_savings_bytes"] > 0  # one object saved space


class TestExists:
    def test_exists(self, store):
        h = store.put({"check": True}, session="s1")
        assert store.exists(h) is True
        assert store.exists("nonexistent123456") is False
