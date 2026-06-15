#!/usr/bin/env python3
"""
Bloom filter for Mycelium agent memory.

O(1) probabilistic membership checks on entities/topics.
No false negatives, configurable false positive rate.

Pure Python — no external dependencies. Uses hashlib for hash functions,
bytearray for bit array. Standard Bloom filter with k hash functions.

Persistence:
  - File: .bloom (pickle-free binary format)
  - DB: bloom_states table in index.db (optional SQLite integration)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
from pathlib import Path

# ── Dynamic path resolution ──────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
MYCELIUM = SCRIPT_DIR.parent
LOG = MYCELIUM / "log.jsonl"
INDEX = MYCELIUM / "index.db"

# ── Bloom filter constants ──────────────────────────────────
BLOOM_STATES_TABLE = """
    CREATE TABLE IF NOT EXISTS bloom_states (
        name TEXT PRIMARY KEY,
        filter BLOB NOT NULL,
        element_count INTEGER NOT NULL,
        m INTEGER NOT NULL,
        k INTEGER NOT NULL,
        created TEXT NOT NULL,
        updated TEXT NOT NULL
    );
"""


class MyceliumBloom:
    """
    Standard Bloom filter — double hashing for k independent hash positions.

    Hash scheme: h_i(x) = (h1(x) + i * h2(x)) mod m
    where h1 = sha256, h2 = sha512, truncated to 8 bytes each.

    Capacity is a soft target — filter continues to work beyond capacity
    but FP rate degrades. Memory grows linearly with m.

    Args:
        capacity: expected number of distinct elements
        error_rate: desired false positive probability (0,1)
        name: identifier for DB persistence
    """

    def __init__(self, capacity: int = 10000, error_rate: float = 0.01, name: str = "entities"):
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if not 0 < error_rate < 1:
            raise ValueError("error_rate must be in (0, 1)")

        self.name = name
        self._capacity = capacity
        self._error_rate = error_rate

        # Optimal parameters per Bloom filter theory
        # m = -n * ln(p) / (ln(2))^2
        # k = (m/n) * ln(2)
        self._m = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self._k = max(1, round(self._m / capacity * math.log(2)))

        # Bit array — store as bytes, bit operations via bytearray
        self._byte_len = (self._m + 7) // 8
        self._bits: bytearray = bytearray(self._byte_len)
        self._count = 0

    # ── Core operations ──────────────────────────────────────

    def add_entity(self, entity: str) -> None:
        """Add entity to the filter. Idempotent — adding twice has same effect."""
        h1, h2 = self._hash_pair(entity)
        for i in range(self._k):
            idx = (h1 + i * h2) % self._m
            byte_pos = idx >> 3        # idx // 8
            bit_pos = idx & 7          # idx % 8
            self._bits[byte_pos] |= (1 << bit_pos)
        self._count += 1

    def check(self, entity: str) -> bool:
        """
        Check membership.
        Returns True if entity MIGHT be in the filter (true positive or false positive).
        Returns False if entity is DEFINITELY NOT in the filter (no false negatives).
        """
        h1, h2 = self._hash_pair(entity)
        for i in range(self._k):
            idx = (h1 + i * h2) % self._m
            byte_pos = idx >> 3
            bit_pos = idx & 7
            if not (self._bits[byte_pos] & (1 << bit_pos)):
                return False
        return True

    def count(self) -> int:
        """Number of elements added (with duplicates counted)."""
        return self._count

    def bits_set(self) -> int:
        """Number of bits currently set."""
        return sum(bin(b).count('1') for b in self._bits)

    def stats(self) -> dict:
        """Return filter statistics."""
        return {
            "elements": self._count,
            "bits": self._m,
            "bytes": self._byte_len,
            "hash_functions": self._k,
            "error_rate": self._error_rate,
            "memory_bytes": self._byte_len + 64,  # filter + metadata overhead
            "bits_set": self.bits_set(),
            "fill_ratio": self.bits_set() / self._m if self._m > 0 else 0,
        }

    # ── Build from log ───────────────────────────────────────

    def build_from_log(self, log_path: str | Path | None = None) -> int:
        """
        Rebuild bloom filter from existing log.jsonl.
        Extracts entities from each entry's 'entities' field.
        Returns count of entities added.
        """
        log_path = Path(log_path) if log_path else LOG
        if not log_path.exists():
            return 0

        self._bits = bytearray(self._byte_len)
        self._count = 0
        added = 0

        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Use pre-extracted entities if available
                entities = entry.get("entities", [])
                if not entities:
                    # Fallback: extract from user + assistant fields
                    from mycelium_lib import extract_entities
                    user = entry.get("user", "")
                    assistant = entry.get("assistant", "")
                    entities = extract_entities(user + " " + assistant)

                for ent in entities:
                    self.add_entity(ent)
                    added += 1

        return added

    # ── Persistence ──────────────────────────────────────────

    def save(self, path: str | Path | None = None) -> Path:
        """
        Save filter to binary file.
        Format: magic(4) + version(1) + m(4) + k(2) + count(4) + bytes(4) + data(...)
        """
        path = Path(path) if path else MYCELIUM / f".bloom_{self.name}"
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'wb') as f:
            # Header: magic + version + params
            f.write(b'MBLM')                    # magic bytes
            f.write(struct.pack('>B', 1))       # version 1
            f.write(struct.pack('>I', self._m))  # m (bit count)
            f.write(struct.pack('>H', self._k))  # k (hash count)
            f.write(struct.pack('>I', self._count))  # element count
            f.write(struct.pack('>I', self._byte_len))  # data length
            f.write(self._bits)                  # raw bit array

        return path

    @classmethod
    def load(cls, path: str | Path, name: str = "entities") -> MyceliumBloom:
        """
        Load filter from binary file.
        Reconstructs MyceliumBloom with same parameters.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Bloom filter not found: {path}")

        with open(path, 'rb') as f:
            magic = f.read(4)
            if magic != b'MBLM':
                raise ValueError(f"Invalid bloom file: bad magic {magic!r}")

            version = struct.unpack('>B', f.read(1))[0]
            if version != 1:
                raise ValueError(f"Unsupported bloom file version: {version}")

            m = struct.unpack('>I', f.read(4))[0]
            k = struct.unpack('>H', f.read(2))[0]
            count = struct.unpack('>I', f.read(4))[0]
            data_len = struct.unpack('>I', f.read(4))[0]
            data = f.read(data_len)

        if len(data) != data_len:
            raise ValueError(f"Truncated bloom file: expected {data_len} bytes, got {len(data)}")

        # Reconstruct with same m, k — compute capacity from m and k
        # m = -n * ln(p) / (ln2)^2 → n = -m * (ln2)^2 / ln(p)
        # But we don't store p, so we just set capacity = count (or reasonable default)
        b = cls.__new__(cls)
        b.name = name
        b._m = m
        b._k = k
        b._count = count
        b._byte_len = data_len
        b._bits = bytearray(data)
        b._capacity = max(count, 1000)  # reasonable default
        b._error_rate = 0.01  # default — not used after load
        return b

    def save_to_db(self, db_path: str | Path | None = None) -> None:
        """Save filter to bloom_states table in index.db."""
        db_path = Path(db_path) if db_path else INDEX
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(str(db_path))
        conn.executescript(BLOOM_STATES_TABLE)
        conn.execute(
            """INSERT OR REPLACE INTO bloom_states
               (name, filter, element_count, m, k, created, updated)
               VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created FROM bloom_states WHERE name=?), ?), ?)""",
            (self.name, bytes(self._bits), self._count, self._m, self._k, self.name, now, now)
        )
        conn.commit()
        conn.close()

    @classmethod
    def load_from_db(cls, name: str = "entities", db_path: str | Path | None = None) -> MyceliumBloom:
        """Load filter from bloom_states table in index.db."""
        db_path = Path(db_path) if db_path else INDEX

        conn = sqlite3.connect(str(db_path))
        conn.executescript(BLOOM_STATES_TABLE)
        row = conn.execute(
            "SELECT filter, element_count, m, k FROM bloom_states WHERE name=?", (name,)
        ).fetchone()
        conn.close()

        if row is None:
            raise ValueError(f"No bloom filter '{name}' in database")

        data, count, m, k = row
        b = cls.__new__(cls)
        b.name = name
        b._m = m
        b._k = k
        b._count = count
        b._byte_len = len(data)
        b._bits = bytearray(data)
        b._capacity = max(count, 1000)
        b._error_rate = 0.01
        return b

    # ── Internal ─────────────────────────────────────────────

    def _hash_pair(self, entity: str) -> tuple[int, int]:
        """
        Double-hash: return (h1, h2) as 64-bit ints from sha256 + sha512.
        Double hashing: h_i(x) = (h1 + i * h2) mod m
        """
        data = entity.encode('utf-8', errors='replace')
        h1_digest = hashlib.sha256(data).digest()[:8]
        h2_digest = hashlib.sha512(data).digest()[:8]
        h1 = struct.unpack('>Q', h1_digest)[0]
        h2 = struct.unpack('>Q', h2_digest)[0]
        if h2 == 0:
            h2 = 1  # avoid zero — would make all hashes identical
        return h1, h2

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"MyceliumBloom(name={self.name!r}, elements={s['elements']}, "
            f"bits={s['bits']}, k={s['hash_functions']}, "
            f"FP≈{s['error_rate']*100:.1f}%, mem={s['memory_bytes']}B)"
        )


# ── CLI ─────────────────────────────────────────────────────

def main():
    """CLI interface for bloom filter operations."""
    import argparse
    parser = argparse.ArgumentParser(description="Mycelium Bloom Filter")
    sub = parser.add_subparsers(dest="cmd")

    build_p = sub.add_parser("build", help="Build bloom from log.jsonl")
    build_p.add_argument("--capacity", type=int, default=10000)
    build_p.add_argument("--error-rate", type=float, default=0.01)
    build_p.add_argument("--name", default="entities")
    build_p.add_argument("--log", help="Path to log file")

    check_p = sub.add_parser("check", help="Check entity membership")
    check_p.add_argument("entity")
    check_p.add_argument("--name", default="entities")

    sub.add_parser("stats", help="Show filter stats")
    sub.add_parser("verify", help="Verify filter against index.db entities")

    args = parser.parse_args()

    if args.cmd == "build":
        bloom = MyceliumBloom(capacity=args.capacity, error_rate=args.error_rate, name=args.name)
        added = bloom.build_from_log(args.log)
        bloom.save()
        bloom.save_to_db()
        print(f"Built: {added} entities, {bloom}")

    elif args.cmd == "check":
        try:
            bloom = MyceliumBloom.load(MYCELIUM / f".bloom_{args.name}", name=args.name)
        except FileNotFoundError:
            bloom = MyceliumBloom.load_from_db(name=args.name)
        result = bloom.check(args.entity)
        print(f"{'POSSIBLE' if result else 'DEFINITELY NOT'}: {args.entity}")

    elif args.cmd == "stats":
        try:
            bloom = MyceliumBloom.load_from_db(name="entities")
            for k, v in bloom.stats().items():
                print(f"  {k}: {v}")
        except (ValueError, FileNotFoundError):
            print("No bloom filter found. Run 'build' first.")

    elif args.cmd == "verify":
        # Cross-check bloom against index.db entities table
        from mycelium_lib import init_index
        conn = init_index()
        db_entities = set(r[0] for r in conn.execute("SELECT DISTINCT entity FROM entities").fetchall())
        conn.close()

        try:
            bloom = MyceliumBloom.load_from_db(name="entities")
        except (ValueError, FileNotFoundError):
            print("No bloom filter found. Run 'build' first.")
            return

        false_negatives = [e for e in db_entities if not bloom.check(e)]
        print(f"DB entities: {len(db_entities)}, Bloom count: {bloom.count()}")
        print(f"False negatives: {len(false_negatives)}")
        if false_negatives:
            for fn in false_negatives[:10]:
                print(f"  MISS: {fn}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
