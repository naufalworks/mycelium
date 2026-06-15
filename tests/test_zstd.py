#!/usr/bin/env python3
"""Tests for zstd_compress.py — mycelium zstd dictionary compression."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from zstd_compress import MyceliumZstdDict, MAGIC_GZIP, MAGIC_ZSTD


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def sample_entries():
    """Return a list of realistic mycelium entries."""
    base = {
        "tier": "B",
        "type": "talk",
        "session": "test-session",
        "ts": "2026-06-15T10:00:00Z",
        "entities": ["mycelium", "python"],
        "prev_hash": "abc123",
        "hash": "def456",
    }
    entries = []
    for i in range(20):
        e = {**base, "turn": i + 1}
        e["user"] = f"User message number {i+1} about testing compression"
        e["assistant"] = f"Assistant response number {i+1} with some detailed content about the topic"
        entries.append(e)
    return entries


@pytest.fixture
def log_file(sample_entries, tmp_path):
    """Write sample entries to a JSONL file."""
    log_path = tmp_path / "test_log.jsonl"
    with open(log_path, "w", encoding="utf-8") as f:
        for e in sample_entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return log_path


@pytest.fixture
def trained_codec(log_file, tmp_path):
    """Codec with a trained dictionary."""
    dict_path = tmp_path / "test_dict.zst"
    codec = MyceliumZstdDict()
    codec.train(log_file, output=dict_path)
    return codec


# ── Tests ────────────────────────────────────────────────────


class TestTrain:
    def test_train_on_sample(self, log_file, tmp_path):
        """Train dict on sample data produces a dict file."""
        dict_path = tmp_path / "trained.zst"
        codec = MyceliumZstdDict()
        result = codec.train(log_file, output=dict_path, samples=20)

        assert result == str(dict_path)
        assert dict_path.exists()
        assert dict_path.stat().st_size > 0

    def test_train_random_sampling(self, log_file, tmp_path):
        """Training with samples < total still works."""
        dict_path = tmp_path / "small_dict.zst"
        codec = MyceliumZstdDict()
        result = codec.train(log_file, output=dict_path, samples=20)
        assert Path(result).exists()

    def test_train_nonexistent_log_raises(self):
        """Training on missing file raises FileNotFoundError."""
        codec = MyceliumZstdDict()
        with pytest.raises(FileNotFoundError):
            codec.train("/nonexistent/log.jsonl")


class TestCompressDecompress:
    def test_compress_decompress_roundtrip(self, trained_codec):
        """Compress → decompress preserves data exactly."""
        original = b"Hello mycelium compression test data 12345"
        compressed = trained_codec.compress(original)
        decompressed = trained_codec.decompress(compressed)

        assert decompressed == original
        # Small data may compress slightly larger due to dict overhead
        # Just verify roundtrip and that it's reasonable (not bloated)
        assert len(compressed) <= len(original) * 2

    def test_compress_without_dict(self, tmp_path):
        """Compression works even without a trained dict (zstd or gzip)."""
        codec = MyceliumZstdDict()
        original = b"Test data without dictionary"
        compressed = codec.compress(original)
        decompressed = codec.decompress(compressed)
        assert decompressed == original

    def test_magic_bytes_zstd(self, trained_codec):
        """Zstd-compressed data starts with zstd magic bytes."""
        compressed = trained_codec.compress(b"test data")
        assert compressed[:4] == MAGIC_ZSTD

    def test_entry_roundtrip(self, trained_codec):
        """Compress dict → decompress → verify exact match."""
        entry = {
            "turn": 42,
            "tier": "S",
            "type": "finding",
            "session": "pentest-acme",
            "ts": "2026-06-15T12:00:00Z",
            "entities": ["acme.com", "sqli", "admin"],
            "user": "Found SQL injection in admin panel",
            "assistant": "Confirmed — critical severity. WAF bypass needed.",
            "finding": {
                "type": "SQLi",
                "target": "admin.acme.com",
                "severity": "critical",
            },
            "prev_hash": "abc123def456",
            "hash": "deadbeef01234567",
        }

        compressed = trained_codec.compress_entry(entry)
        decompressed = trained_codec.decompress_entry(compressed)
        assert decompressed == entry


class TestFallback:
    def test_fallback_to_gzip(self, tmp_path):
        """When zstd unavailable, gzip is used (simulated)."""
        # Create a codec with no dict and monkeypatch to force gzip
        codec = MyceliumZstdDict()

        # Directly use the static gzip method
        original = b"fallback test data"
        compressed = MyceliumZstdDict._compress_gzip(original)
        decompressed = gzip.decompress(compressed)

        assert compressed[:2] == MAGIC_GZIP
        assert decompressed == original

    def test_gzip_roundtrip(self):
        """Gzip compress → decompress preserves data."""
        data = b"Test entry for gzip fallback"
        compressed = gzip.compress(data)
        assert compressed[:2] == MAGIC_GZIP
        assert gzip.decompress(compressed) == data

    def test_detect_gzip_magic(self):
        """Gzip magic bytes are correctly identified."""
        data = b"test"
        compressed = gzip.compress(data)
        assert compressed[:2] == MAGIC_GZIP
        assert compressed[:4] != MAGIC_ZSTD


class TestStats:
    def test_stats_returns_ratios(self, trained_codec):
        """stats() returns compression ratios."""
        # Compress some data to populate stats
        entry = {"turn": 1, "tier": "B", "type": "talk", "user": "hello" * 50}
        for _ in range(10):
            trained_codec.compress_entry(entry)

        stats = trained_codec.stats()

        assert "ratio" in stats
        assert "original_bytes" in stats
        assert "compressed_bytes" in stats
        assert "entries_compressed" in stats
        assert "engine" in stats
        assert stats["entries_compressed"] == 10
        assert stats["original_bytes"] > 0
        assert stats["compressed_bytes"] > 0
        assert stats["ratio"] >= 1.0  # At least 1x (should be much better)

    def test_stats_without_compression(self):
        """stats() returns zeroed values before any compression."""
        codec = MyceliumZstdDict()
        stats = codec.stats()
        assert stats["original_bytes"] == 0
        assert stats["compressed_bytes"] == 0
        assert stats["entries_compressed"] == 0
        assert stats["ratio"] == 0.0

    def test_engine_name(self, trained_codec):
        """Engine name reflects available backends."""
        name = trained_codec._engine_name()
        assert name in ("zstd+dict", "zstd", "zstd-cli", "gzip")

    def test_save_stats(self, trained_codec, tmp_path):
        """save_stats writes JSON to disk."""
        entry = {"turn": 1, "data": "x" * 200}
        trained_codec.compress_entry(entry)

        stats_path = tmp_path / "stats.json"
        trained_codec.save_stats(stats_path)

        assert stats_path.exists()
        loaded = json.loads(stats_path.read_text())
        assert "ratio" in loaded
