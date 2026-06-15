#!/usr/bin/env python3
"""Tests for Mycelium Bloom Filter."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mycelium_bloom import MyceliumBloom


# ── Core properties ──────────────────────────────────────────

class TestFalseNegativeGuarantee:
    """Bloom filters MUST never produce false negatives."""

    def test_added_items_always_true(self):
        bloom = MyceliumBloom(capacity=1000, error_rate=0.01)
        items = [f"entity_{i}" for i in range(500)]
        for item in items:
            bloom.add_entity(item)
        # Every added item must return True
        for item in items:
            assert bloom.check(item) is True, f"False negative for {item}"

    def test_added_items_after_rebuild(self):
        """Test via build_from_log — no false negatives."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for i in range(200):
                entry = {"turn": i, "type": "talk", "entities": [f"ent_{i}", f"shared_{i % 50}"]}
                f.write(json.dumps(entry) + "\n")
            tmp_path = f.name

        try:
            bloom = MyceliumBloom(capacity=500, error_rate=0.01)
            bloom.build_from_log(tmp_path)

            # All entities from log must be found
            for i in range(200):
                assert bloom.check(f"ent_{i}") is True
                assert bloom.check(f"shared_{i % 50}") is True
        finally:
            os.unlink(tmp_path)

    def test_empty_filter(self):
        bloom = MyceliumBloom(capacity=100, error_rate=0.01)
        assert bloom.check("anything") is False


class TestFalsePositiveRate:
    """FP rate must stay below theoretical bound."""

    def test_fp_rate_below_5pct(self):
        bloom = MyceliumBloom(capacity=1000, error_rate=0.01)
        # Add 1000 distinct items
        added = set()
        for i in range(1000):
            item = f"added_{i}"
            bloom.add_entity(item)
            added.add(item)

        # Test against 10000 items NOT in the filter
        fp_count = 0
        test_count = 10000
        for i in range(test_count):
            item = f"not_added_{i}"
            if bloom.check(item):
                fp_count += 1

        fp_rate = fp_count / test_count
        assert fp_rate < 0.05, f"FP rate {fp_rate:.4f} exceeds 5% threshold"

    def test_fp_rate_reasonable_at_scale(self):
        """At 1000 items with 1% target, FP should be well under 5%."""
        bloom = MyceliumBloom(capacity=1000, error_rate=0.01)
        for i in range(1000):
            bloom.add_entity(f"item_{i}")

        fp = sum(1 for i in range(10000) if bloom.check(f"probe_{i}"))
        fp_rate = fp / 10000
        # With optimal params, should be well under 5%
        assert fp_rate < 0.05


# ── Persistence ──────────────────────────────────────────────

class TestSaveLoadRoundtrip:
    """File persistence must preserve exact state."""

    def test_save_load_file(self):
        bloom = MyceliumBloom(capacity=1000, error_rate=0.01, name="test_roundtrip")
        for i in range(100):
            bloom.add_entity(f"entity_{i}")

        with tempfile.NamedTemporaryFile(suffix='.bloom', delete=False) as f:
            tmp_path = f.name

        try:
            bloom.save(tmp_path)
            loaded = MyceliumBloom.load(tmp_path, name="test_roundtrip")

            # Same stats
            assert loaded.count() == bloom.count()
            assert loaded._m == bloom._m
            assert loaded._k == bloom._k

            # Same results
            for i in range(100):
                assert loaded.check(f"entity_{i}") is True
            assert loaded.check("not_in_filter") in (True, False)  # no crash

            # Same bits
            assert bytes(loaded._bits) == bytes(bloom._bits)
        finally:
            os.unlink(tmp_path)

    def test_save_load_via_index_db(self):
        """Save to SQLite bloom_states, load back."""
        bloom = MyceliumBloom(capacity=500, error_rate=0.01, name="db_test")
        for i in range(50):
            bloom.add_entity(f"db_entity_{i}")

        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            bloom.save_to_db(db_path)
            loaded = MyceliumBloom.load_from_db(name="db_test", db_path=db_path)

            assert loaded.count() == bloom.count()
            for i in range(50):
                assert loaded.check(f"db_entity_{i}") is True
        finally:
            os.unlink(db_path)


# ── Build from log ───────────────────────────────────────────

class TestBuildFromLog:
    """Build bloom filter from log.jsonl."""

    def test_build_from_log(self):
        log_path = Path.home() / "Documents" / "mycelium" / "log.jsonl"
        if not log_path.exists():
            pytest.skip("log.jsonl not found")

        bloom = MyceliumBloom(capacity=10000, error_rate=0.01, name="test_build")
        count = bloom.build_from_log(log_path)

        assert count > 0, "No entities extracted from log"
        assert bloom.count() > 0

        # Spot-check some known entities from the log
        # At minimum, common terms like "mycelium" or "hermes" should be present
        # (depending on actual log content)

    def test_build_from_synthetic_log(self):
        """Build from a synthetic log with known entities."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            expected = set()
            for i in range(50):
                ents = [f"entity_a_{i}", f"entity_b_{i}"]
                entry = {"turn": i, "type": "talk", "entities": ents}
                f.write(json.dumps(entry) + "\n")
                expected.update(ents)
            tmp_path = f.name

        try:
            bloom = MyceliumBloom(capacity=200, error_rate=0.01)
            bloom.build_from_log(tmp_path)

            for ent in expected:
                assert bloom.check(ent) is True
        finally:
            os.unlink(tmp_path)


# ── Incremental add ──────────────────────────────────────────

class TestIncrementalAdd:
    """Adding items incrementally must not break existing membership."""

    def test_incremental_add(self):
        bloom = MyceliumBloom(capacity=500, error_rate=0.01)
        batch1 = [f"batch1_{i}" for i in range(100)]
        batch2 = [f"batch2_{i}" for i in range(100)]
        batch3 = [f"batch3_{i}" for i in range(100)]

        for item in batch1:
            bloom.add_entity(item)

        # After batch1 — all batch1 items found
        for item in batch1:
            assert bloom.check(item) is True

        for item in batch2:
            bloom.add_entity(item)

        # After batch2 — both batches found
        for item in batch1 + batch2:
            assert bloom.check(item) is True

        for item in batch3:
            bloom.add_entity(item)

        # After batch3 — all three batches found
        for item in batch1 + batch2 + batch3:
            assert bloom.check(item) is True

        assert bloom.count() == 300

    def test_idempotent_add(self):
        """Adding same item multiple times doesn't change check results."""
        bloom = MyceliumBloom(capacity=100, error_rate=0.01)
        for _ in range(10):
            bloom.add_entity("repeated_item")
        assert bloom.check("repeated_item") is True
        assert bloom.count() == 10  # count includes dupes

    def test_add_after_save_load(self):
        """Can add items after loading a saved filter."""
        bloom = MyceliumBloom(capacity=100, error_rate=0.01)
        bloom.add_entity("original_item")

        with tempfile.NamedTemporaryFile(suffix='.bloom', delete=False) as f:
            tmp_path = f.name

        try:
            bloom.save(tmp_path)
            loaded = MyceliumBloom.load(tmp_path)

            loaded.add_entity("new_item")
            assert loaded.check("original_item") is True
            assert loaded.check("new_item") is True
            assert loaded.count() == 2
        finally:
            os.unlink(tmp_path)


# ── Edge cases ───────────────────────────────────────────────

class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_unicode_entities(self):
        bloom = MyceliumBloom(capacity=100, error_rate=0.01)
        items = ["日本語", "🔐", "Ñoño", "αβγ", "🔥fire"]
        for item in items:
            bloom.add_entity(item)
        for item in items:
            assert bloom.check(item) is True

    def test_empty_string(self):
        bloom = MyceliumBloom(capacity=100, error_rate=0.01)
        bloom.add_entity("")
        assert bloom.check("") is True

    def test_very_long_string(self):
        bloom = MyceliumBloom(capacity=100, error_rate=0.01)
        long = "x" * 10000
        bloom.add_entity(long)
        assert bloom.check(long) is True

    def test_stats_keys(self):
        bloom = MyceliumBloom(capacity=100, error_rate=0.05)
        s = bloom.stats()
        assert "elements" in s
        assert "bits" in s
        assert "error_rate" in s
        assert "memory_bytes" in s
        assert s["bits"] > 0
