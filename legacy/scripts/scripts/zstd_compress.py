#!/usr/bin/env python3
"""
zstd_compress.py — Zstd trained-dictionary compression for mycelium entries.

Compression tiers (auto-detected on decompress via magic bytes):
  1. zstd + trained dict  → best ratio (10-20x on repetitive JSON)
  2. zstd (generic)       → good ratio (3-5x)
  3. gzip (stdlib)        → fallback (2-3x)

Training pipeline:
  - Reads log.jsonl, samples N entries
  - Writes each entry as individual .json to temp dir
  - Trains dict via zstd CLI or zstandard library
  - Saves dict to disk for reuse

Magic bytes:
  \x28\xb5\x2f\xfd = zstd (with or without dict)
  \x1f\x8b         = gzip
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ── Try imports ──────────────────────────────────────────────

_ZSTANDARD = False
try:
    import zstandard as zstd_mod
    _ZSTANDARD = True
except ImportError:
    pass

_ZSTD_CLI = shutil.which("zstd")

# ── Magic bytes ──────────────────────────────────────────────

MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"
MAGIC_GZIP = b"\x1f\x8b"

# ── Defaults ─────────────────────────────────────────────────

_DEFAULT_DICT_DIR = Path(__file__).resolve().parent.parent / "dicts"
_DEFAULT_DICT_NAME = "mycelium.dict.zst"


class MyceliumZstdDict:
    """Zstd trained-dictionary compression for mycelium entries.

    Usage:
        codec = MyceliumZstdDict()
        codec.train("log.jsonl")          # train dict from log
        compressed = codec.compress_entry(entry)
        entry = codec.decompress_entry(compressed)
        print(codec.stats())
    """

    def __init__(self, dict_path: str | Path | None = None):
        """
        Args:
            dict_path: Path to trained .dict.zst file. If None, looks for
                       default location under dicts/mycelium.dict.zst
        """
        self._dict_path: Path | None = None
        self._dict_data: bytes | None = None
        self._cctx: Any = None
        self._dctx: Any = None

        # Compression stats
        self._total_compressed = 0
        self._total_original = 0
        self._total_entries = 0

        if dict_path:
            self._dict_path = Path(dict_path)
        else:
            default = _DEFAULT_DICT_DIR / _DEFAULT_DICT_NAME
            if default.exists():
                self._dict_path = default

        if self._dict_path and self._dict_path.exists():
            self._load_dict()

    def _load_dict(self) -> None:
        """Load trained dictionary and create compressor/decompressor contexts."""
        if not self._dict_path or not self._dict_path.exists():
            return
        if not _ZSTANDARD:
            return
        self._dict_data = self._dict_path.read_bytes()
        self._zdict = zstd_mod.ZstdCompressionDict(self._dict_data)
        self._cctx = zstd_mod.ZstdCompressor(dict_data=self._zdict)
        self._dctx = zstd_mod.ZstdDecompressor(dict_data=self._zdict)

    # ── Training ─────────────────────────────────────────────

    def train(
        self,
        log_path: str | Path,
        output: str | Path | None = None,
        samples: int = 500,
        maxdict: int = 102400,
    ) -> str:
        """Train a zstd dictionary from log entries.

        Args:
            log_path: Path to log.jsonl
            output: Where to save the trained dict (default: dicts/mycelium.dict.zst)
            samples: Number of random entries to sample for training
            maxdict: Max dictionary size in bytes (default 100KB)

        Returns:
            Path to the trained dictionary file.

        Raises:
            RuntimeError: If neither zstd CLI nor zstandard library available.
        """
        log_path = Path(log_path)
        if not log_path.exists():
            raise FileNotFoundError(f"Log not found: {log_path}")

        # Load and sample entries
        entries = []
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(line)

        if not entries:
            raise ValueError("Log file is empty — nothing to train on")

        if len(entries) > samples:
            entries = random.sample(entries, samples)

        # Output path
        if output is None:
            _DEFAULT_DICT_DIR.mkdir(parents=True, exist_ok=True)
            output = _DEFAULT_DICT_DIR / _DEFAULT_DICT_NAME
        output = Path(output)

        # Try zstd CLI first (better training)
        dict_data = self._train_cli(entries, maxdict)
        if dict_data is None:
            # Fallback to Python library
            dict_data = self._train_library(entries, maxdict)

        if dict_data is None:
            raise RuntimeError(
                "Cannot train dict: need either 'zstd' CLI or 'zstandard' Python library"
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(dict_data)
        self._dict_path = output
        self._dict_data = dict_data

        if _ZSTANDARD:
            self._zdict = zstd_mod.ZstdCompressionDict(dict_data)
            self._cctx = zstd_mod.ZstdCompressor(dict_data=self._zdict)
            self._dctx = zstd_mod.ZstdDecompressor(dict_data=self._zdict)

        return str(output)

    def _train_cli(self, entries: list[str], maxdict: int) -> bytes | None:
        """Train dict using zstd CLI."""
        if not _ZSTD_CLI:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            corpus = Path(tmpdir) / "corpus"
            corpus.mkdir()

            for i, entry in enumerate(entries):
                (corpus / f"entry_{i:06d}.json").write_text(entry, encoding="utf-8")

            dict_out = Path(tmpdir) / "dict.zst"
            cmd = [
                _ZSTD_CLI,
                "--train",
                "-o",
                str(dict_out),
                "--maxdict",
                str(maxdict),
            ] + [str(f) for f in sorted(corpus.iterdir())]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0 and dict_out.exists():
                    return dict_out.read_bytes()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        return None

    def _train_library(self, entries: list[str], maxdict: int) -> bytes | None:
        """Train dict using Python zstandard library.

        API: zstandard.train_dictionary(dict_size, samples)
        """
        if not _ZSTANDARD:
            return None

        corpus = [e.encode("utf-8") for e in entries]
        try:
            trained = zstd_mod.train_dictionary(maxdict, corpus)
            return trained.as_bytes()
        except Exception:
            return None


    # ── Compression ──────────────────────────────────────────

    def compress(self, data: bytes) -> bytes:
        """Compress bytes. Uses dict if available, else generic zstd, else gzip."""
        self._total_original += len(data)

        if _ZSTANDARD:
            if self._cctx:
                compressed = self._cctx.compress(data)
            else:
                compressed = zstd_mod.ZstdCompressor().compress(data)
        elif _ZSTD_CLI:
            compressed = self._compress_cli(data)
        else:
            compressed = self._compress_gzip(data)

        self._total_compressed += len(compressed)
        return compressed

    def decompress(self, data: bytes) -> bytes:
        """Decompress bytes. Auto-detects codec via magic bytes."""
        if len(data) < 2:
            return data

        if data[:4] == MAGIC_ZSTD:
            if self._dctx:
                return self._dctx.decompress(data)
            elif _ZSTANDARD:
                return zstd_mod.ZstdDecompressor().decompress(data)
            elif _ZSTD_CLI:
                return self._decompress_cli(data)
            else:
                raise RuntimeError("Compressed with zstd but no zstd available")
        elif data[:2] == MAGIC_GZIP:
            return gzip.decompress(data)

        # Unknown format — return raw
        return data

    def compress_entry(self, entry: dict) -> bytes:
        """Compress a dict entry (serialized as JSON)."""
        data = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._total_entries += 1
        return self.compress(data)

    def decompress_entry(self, data: bytes) -> dict:
        """Decompress bytes back to a dict entry."""
        raw = self.decompress(data)
        return json.loads(raw.decode("utf-8"))

    # ── CLI helpers ──────────────────────────────────────────

    def _compress_cli(self, data: bytes) -> bytes:
        """Compress via zstd CLI (no dict)."""
        cmd = [_ZSTD_CLI, "-f", "-o", "-"]
        try:
            result = subprocess.run(
                cmd,
                input=data,
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return self._compress_gzip(data)

    def _decompress_cli(self, data: bytes) -> bytes:
        """Decompress via zstd CLI."""
        cmd = [_ZSTD_CLI, "-d", "-f", "-o", "-"]
        try:
            result = subprocess.run(
                cmd,
                input=data,
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        raise RuntimeError("zstd CLI decompression failed")

    @staticmethod
    def _compress_gzip(data: bytes) -> bytes:
        """Fallback: gzip compression."""
        return gzip.compress(data, compresslevel=6)

    # ── Stats ────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return compression statistics."""
        ratio = 0.0
        if self._total_compressed > 0:
            ratio = self._total_original / self._total_compressed

        dict_size = 0
        if self._dict_path and self._dict_path.exists():
            dict_size = self._dict_path.stat().st_size

        return {
            "original_bytes": self._total_original,
            "compressed_bytes": self._total_compressed,
            "ratio": round(ratio, 2),
            "entries_compressed": self._total_entries,
            "dict_path": str(self._dict_path) if self._dict_path else None,
            "dict_size_bytes": dict_size,
            "engine": self._engine_name(),
        }

    def _engine_name(self) -> str:
        if self._dict_data and _ZSTANDARD:
            return "zstd+dict"
        if _ZSTANDARD:
            return "zstd"
        if _ZSTD_CLI:
            return "zstd-cli"
        return "gzip"

    def save_stats(self, path: str | Path) -> None:
        """Save stats to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.stats(), indent=2))


# ── CLI mode ─────────────────────────────────────────────────

def main():
    """CLI interface for dict training and testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Mycelium zstd dictionary compression")
    sub = parser.add_subparsers(dest="cmd")

    # train
    train_p = sub.add_parser("train", help="Train dictionary from log")
    train_p.add_argument("log", help="Path to log.jsonl")
    train_p.add_argument("-o", "--output", help="Dict output path")
    train_p.add_argument("-n", "--samples", type=int, default=500)
    train_p.add_argument("--maxdict", type=int, default=102400)

    # stats
    stats_p = sub.add_parser("stats", help="Show compression stats")
    stats_p.add_argument("file", help="Compressed file to analyze")

    # test
    test_p = sub.add_parser("test", help="Compress/decompress roundtrip test")
    test_p.add_argument("text", nargs="?", default="test entry data")

    args = parser.parse_args()

    if args.cmd == "train":
        codec = MyceliumZstdDict()
        path = codec.train(args.log, args.output, args.samples, args.maxdict)
        print(f"Dict trained: {path}")
        print(f"Dict size: {Path(path).stat().st_size} bytes")

    elif args.cmd == "stats":
        data = Path(args.file).read_bytes()
        codec = MyceliumZstdDict()
        codec.compress(data)  # dummy to get baseline
        codec._total_original = len(data)
        codec._total_compressed = len(data)
        print(json.dumps(codec.stats(), indent=2))

    elif args.cmd == "test":
        codec = MyceliumZstdDict()
        entry = {"turn": 1, "tier": "S", "type": "test", "text": args.text}
        compressed = codec.compress_entry(entry)
        decompressed = codec.decompress_entry(compressed)
        print(f"Original:   {len(json.dumps(entry).encode())} bytes")
        print(f"Compressed: {len(compressed)} bytes")
        print(f"Ratio:      {len(json.dumps(entry).encode()) / len(compressed):.2f}x")
        print(f"Roundtrip:  {'PASS' if decompressed == entry else 'FAIL'}")
        print(f"Engine:     {codec._engine_name()}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
