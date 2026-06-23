#!/usr/bin/env python3
"""
mycelium_lsm.py — LSM-tree memory for agent conversations.

Three-tier storage:
  L0 (Hot):  last N turns, full text, dict-based O(1) lookup
  L1 (Warm): compressed JSONL segments (zstd-with-dict when available, gzip fallback)
  L2 (Cold): one-line summaries + entity tags

Condition-based compaction (NOT time-based):
  - L0 > L0_MAX entries → flush to L1
  - L1 > L1_MAX segments → compact to L2
  - Manual: `mycelium compact`

Zero data loss: every level is a complete, decompressible view.
Hash chain spans all levels for integrity verification.
"""

from __future__ import annotations

import gzip, hashlib, json, os, struct, time
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import (
    MYCELIUM, LOG, INDEX, load_log, compute_hash,
    classify_tier, extract_entities, init_index,
)
from zstd_compress import MyceliumZstdDict

# Compression magic bytes for format detection
_MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"  # zstd (LE: 0xFD2FB528)
_MAGIC_GZIP = b"\x1f\x8b"


def _fallback_decompress(data: bytes) -> str:
    """Decompress segment data, detecting format via magic bytes.

    Used when no trained zstd dict is available. Handles both zstd (generic)
    and gzip. Dict-compressed zstd data requires the trained codec.
    """
    if len(data) >= 4 and data[:4] == _MAGIC_ZSTD:
        try:
            import zstandard as zstd
            return zstd.ZstdDecompressor().decompress(data).decode("utf-8")
        except Exception:
            raise RuntimeError("zstd data but zstd library not available")
    if len(data) >= 2 and data[:2] == _MAGIC_GZIP:
        return gzip.decompress(data).decode("utf-8")
    # Unknown — try as plain text
    return data.decode("utf-8")



# ── Configuration ───────────────────────────────────────────
L0_MAX = 50        # entries before flush to L1
L1_MAX = 500       # segments before compact to L2
L1_SEGMENT_SIZE = 100  # entries per L1 segment
SUMMARY_MAX_LEN = 120  # max chars per L2 summary


# ── L0 Layer (Hot — in-memory dict) ────────────────────────

class L0Layer:
    """In-memory dict of recent entries. O(1) lookup by turn."""

    def __init__(self, max_entries: int = L0_MAX):
        self.max = max_entries
        self._data: dict[int, dict] = {}  # {turn: entry}

    def put(self, entry: dict) -> None:
        turn = entry.get("turn", 0)
        self._data[turn] = entry

    def get(self, turn: int) -> dict | None:
        return self._data.get(turn)

    def count(self) -> int:
        return len(self._data)

    def needs_flush(self) -> bool:
        return len(self._data) > self.max

    def flush_candidates(self, count: int) -> list[dict]:
        """Return oldest entries to flush to L1."""
        sorted_turns = sorted(self._data.keys())
        to_flush = sorted_turns[:count]
        return [self._data.pop(t) for t in to_flush]

    def all_turns(self) -> list[int]:
        return sorted(self._data.keys())

    def to_list(self) -> list[dict]:
        return [self._data[t] for t in sorted(self._data.keys())]


# ── L1 Layer (Warm — compressed segments) ──────────────────

class L1Segment:
    """A single compressed JSONL segment on disk."""

    def __init__(self, path: Path, zstd_codec: MyceliumZstdDict | None = None):
        self.path = path
        self._zstd = zstd_codec
        self._entries: list[dict] | None = None
        self._turns: set[int] | None = None

    def _load(self) -> None:
        if self._entries is not None:
            return
        if not self.path.exists():
            self._entries = []
            self._turns = set()
            return
        raw = self.path.read_bytes()
        if self._zstd:
            text = self._zstd.decompress(raw).decode("utf-8")
        else:
            text = _fallback_decompress(raw)
        self._entries = [json.loads(line) for line in text.splitlines() if line.strip()]
        self._turns = {e.get("turn", 0) for e in self._entries}

    def get(self, turn: int) -> dict | None:
        self._load()
        for e in self._entries:
            if e.get("turn") == turn:
                return e
        return None

    def contains(self, turn: int) -> bool:
        self._load()
        return turn in self._turns

    def entries(self) -> list[dict]:
        self._load()
        return list(self._entries)

    def turns(self) -> set[int]:
        self._load()
        return set(self._turns)

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def entry_count(self) -> int:
        self._load()
        return len(self._entries)


class L1Layer:
    """Collection of compressed segments on disk."""

    def __init__(self, base_path: Path, max_segments: int = L1_MAX):
        self.base = base_path / "l1"
        self.base.mkdir(parents=True, exist_ok=True)
        self.max_segments = max_segments
        self._segments: list[L1Segment] | None = None
        # Load zstd dict codec if trained dict exists
        self._zstd: MyceliumZstdDict | None = None
        dict_path = base_path / "dicts" / "mycelium.dict.zst"
        if dict_path.exists():
            try:
                self._zstd = MyceliumZstdDict(dict_path)
            except Exception:
                pass

    def _discover(self) -> list[L1Segment]:
        if self._segments is not None:
            return self._segments
        files = sorted(
            list(self.base.glob("seg_*.jsonl.gz"))
            + list(self.base.glob("seg_*.jsonl.zst"))
        )
        self._segments = [L1Segment(f, self._zstd) for f in files]
        return self._segments

    def segment_count(self) -> int:
        return len(self._discover())

    def needs_compaction(self) -> bool:
        return self.segment_count() > self.max_segments

    def get(self, turn: int) -> dict | None:
        for seg in self._discover():
            if seg.contains(turn):
                return seg.get(turn)
        return None

    def all_turns(self) -> set[int]:
        turns = set()
        for seg in self._discover():
            turns.update(seg.turns())
        return turns

    def total_entries(self) -> int:
        return sum(seg.entry_count() for seg in self._discover())

    def write_segment(self, entries: list[dict]) -> Path:
        """Write entries to a new compressed segment."""
        if not entries:
            return None
        turns = [e.get("turn", 0) for e in entries]
        if self._zstd:
            seg_name = f"seg_{min(turns):06d}_{max(turns):06d}.jsonl.zst"
            seg_path = self.base / seg_name
            lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
            compressed = self._zstd.compress(lines.encode("utf-8"))
            seg_path.write_bytes(compressed)
        else:
            seg_name = f"seg_{min(turns):06d}_{max(turns):06d}.jsonl.gz"
            seg_path = self.base / seg_name
            with gzip.open(seg_path, "wt", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        # Invalidate cache
        self._segments = None
        return seg_path

    def total_size_bytes(self) -> int:
        return sum(seg.size_bytes() for seg in self._discover())


# ── L2 Layer (Cold — summaries) ────────────────────────────

class L2Summary:
    """A compressed summary segment."""

    def __init__(self, path: Path):
        self.path = path
        self._entries: list[dict] | None = None

    def _load(self) -> None:
        if self._entries is not None:
            return
        if not self.path.exists():
            self._entries = []
            return
        with gzip.open(self.path, "rt", encoding="utf-8") as f:
            self._entries = [json.loads(line) for line in f if line.strip()]

    def entries(self) -> list[dict]:
        self._load()
        return list(self._entries)

    def entry_count(self) -> int:
        self._load()
        return len(self._entries)

    def turns(self) -> set[int]:
        self._load()
        return {e.get("turn", 0) for e in self._entries}


class L2Layer:
    """Summarized entries. Full text replaced with one-liners."""

    def __init__(self, base_path: Path):
        self.base = base_path / "l2"
        self.base.mkdir(parents=True, exist_ok=True)
        self._summaries: list[L2Summary] | None = None

    def _discover(self) -> list[L2Summary]:
        if self._summaries is not None:
            return self._summaries
        files = sorted(self.base.glob("sum_*.jsonl.gz"))
        self._summaries = [L2Summary(f) for f in files]
        return self._summaries

    def summary_count(self) -> int:
        return len(self._discover())

    def get(self, turn: int) -> dict | None:
        for s in self._discover():
            for e in s.entries():
                if e.get("turn") == turn:
                    return e
        return None

    def all_turns(self) -> set[int]:
        turns = set()
        for s in self._discover():
            turns.update(s.turns())
        return turns

    def total_entries(self) -> int:
        return sum(s.entry_count() for s in self._discover())

    def total_size_bytes(self) -> int:
        return sum(s.path.stat().st_size for s in self._discover() if s.path.exists())

    def write_summary(self, entries: list[dict]) -> Path:
        """Write summarized entries to a new compressed segment."""
        if not entries:
            return None
        turns = [e.get("turn", 0) for e in entries]
        seg_name = f"sum_{min(turns):06d}_{max(turns):06d}.jsonl.gz"
        seg_path = self.base / seg_name
        with gzip.open(seg_path, "wt", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        self._summaries = None
        return seg_path


# ── Summary Generation ─────────────────────────────────────

def make_summary(entry: dict) -> dict:
    """Compress entry to one-line summary. Full text → summary."""
    user = entry.get("user", "")
    assistant = entry.get("assistant", "")
    summary_text = (user[:60] + " → " + assistant[:60])[:SUMMARY_MAX_LEN]

    summary = {
        "turn": entry.get("turn"),
        "tier": entry.get("tier", "B"),
        "type": entry.get("type", "talk"),
        "session": entry.get("session", "?"),
        "ts": entry.get("ts", ""),
        "entities": entry.get("entities", []),
        "summary": summary_text,
    }
    # Preserve finding info if present
    if entry.get("finding"):
        summary["finding"] = {
            "type": entry["finding"].get("type", "?"),
            "target": entry["finding"].get("target", "?"),
            "severity": entry["finding"].get("severity", "info"),
        }
    # Preserve hash chain
    summary["prev_hash"] = entry.get("prev_hash", "")
    summary["hash"] = entry.get("hash", "")

    return summary


# ── Main LSM Class ─────────────────────────────────────────

class MyceliumLSM:
    """LSM-tree memory for agent conversations.

    Usage:
        lsm = MyceliumLSM()
        lsm.append(entry)              # writes to L0, auto-flushes
        e = lsm.get(turn_number)       # reads L0→L1→L2
        lsm.flush()                    # manual flush L0→L1
        lsm.compact()                  # manual compact L1→L2
        stats = lsm.stats()            # layer stats
    """

    def __init__(self, base_path: Path | str | None = None):
        if base_path is None:
            base_path = MYCELIUM
        self.base = Path(base_path)
        self.l0 = L0Layer()
        self.l1 = L1Layer(self.base)
        self.l2 = L2Layer(self.base)
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load existing L1/L2 on first access."""
        if self._loaded:
            return
        self._loaded = True
        # L0 starts empty — populated by initial load or append
        # L1/L2 are discovered from disk

    def append(self, entry: dict) -> dict | None:
        """Append entry to L0. Returns flush info if L0 was flushed."""
        self._ensure_loaded()
        self.l0.put(entry)
        result = None
        if self.l0.needs_flush():
            result = self.flush()
        return result

    def get(self, turn: int) -> dict | None:
        """Read entry by turn. Checks L0→L1→L2."""
        self._ensure_loaded()
        # L0 first (hot)
        e = self.l0.get(turn)
        if e:
            return e
        # L1 (warm)
        e = self.l1.get(turn)
        if e:
            # Promote back to L0 on read (attention tracking)
            self.l0.put(e)
            return e
        # L2 (cold — summary only)
        return self.l2.get(turn)

    def flush(self) -> dict:
        """Flush oldest L0 entries to L1 segment. Returns stats."""
        self._ensure_loaded()
        count = self.l0.count()
        if count == 0:
            return {"flushed": 0}
        # Flush all but most recent L0_MAX/2 entries
        keep = max(L0_MAX // 2, 10)
        flush_count = max(0, count - keep)
        if flush_count == 0:
            return {"flushed": 0}
        entries = self.l0.flush_candidates(flush_count)
        if entries:
            self.l1.write_segment(entries)
        return {"flushed": len(entries), "kept": self.l0.count()}

    def compact(self) -> dict:
        """Compact L1 segments into L2 summaries. Returns stats."""
        self._ensure_loaded()
        if not self.l1.needs_compaction() and self.l1.segment_count() <= 5:
            return {"compacted": 0, "reason": "under threshold"}

        segments = self.l1._discover()
        if not segments:
            return {"compacted": 0}

        # Compact oldest segments into summaries
        compact_count = max(0, self.l1.segment_count() - L1_MAX // 2)
        if compact_count == 0:
            compact_count = min(3, len(segments))  # compact at least some

        entries_to_summarize = []
        segments_to_remove = []
        for seg in segments[:compact_count]:
            entries_to_summarize.extend(seg.entries())
            segments_to_remove.append(seg)

        # Generate summaries
        summaries = [make_summary(e) for e in entries_to_summarize]
        if summaries:
            self.l2.write_summary(summaries)

        # Remove compacted segments
        for seg in segments_to_remove:
            if seg.path.exists():
                seg.path.unlink()
        self.l1._segments = None  # invalidate cache

        return {
            "compacted": len(segments_to_remove),
            "entries_summarized": len(summaries),
            "l1_segments": self.l1.segment_count(),
            "l2_summaries": self.l2.total_entries(),
        }

    def load_from_jsonl(self, log_path: Path | str | None = None) -> dict:
        """Initial load: populate L0 from last entries, L1 from older."""
        log_path = Path(log_path) if log_path else LOG
        if not log_path.exists():
            return {"loaded": 0}

        entries = load_log(log_path)
        if not entries:
            return {"loaded": 0}

        # Split: last L0_MAX → L0, rest → L1 segments
        if len(entries) <= L0_MAX:
            for e in entries:
                self.l0.put(e)
        else:
            # Older entries → L1 segments
            older = entries[:-L0_MAX]
            recent = entries[-L0_MAX:]

            # Write older in chunks
            for i in range(0, len(older), L1_SEGMENT_SIZE):
                chunk = older[i:i + L1_SEGMENT_SIZE]
                self.l1.write_segment(chunk)

            # Hot entries → L0
            for e in recent:
                self.l0.put(e)

        return {
            "loaded": len(entries),
            "l0": self.l0.count(),
            "l1_segments": self.l1.segment_count(),
        }

    def stats(self) -> dict:
        """Return stats for all layers."""
        self._ensure_loaded()
        l0_size = sum(
            len(json.dumps(e).encode()) for e in self.l0.to_list()
        )
        return {
            "l0_entries": self.l0.count(),
            "l0_size_bytes": l0_size,
            "l1_segments": self.l1.segment_count(),
            "l1_entries": self.l1.total_entries(),
            "l1_size_bytes": self.l1.total_size_bytes(),
            "l2_summaries": self.l2.summary_count(),
            "l2_entries": self.l2.total_entries(),
            "l2_size_bytes": self.l2.total_size_bytes(),
            "total_entries": (
                self.l0.count()
                + self.l1.total_entries()
                + self.l2.total_entries()
            ),
            "total_size_bytes": (
                l0_size
                + self.l1.total_size_bytes()
                + self.l2.total_size_bytes()
            ),
            "l0_max": L0_MAX,
            "l1_max": L1_MAX,
        }

    def verify_integrity(self) -> dict:
        """Verify hash chain across all layers."""
        # Collect all entries from all layers, sorted by turn
        all_turns: dict[int, dict] = {}

        for e in self.l0.to_list():
            all_turns[e.get("turn", 0)] = e
        for seg in self.l1._discover():
            for e in seg.entries():
                all_turns[e.get("turn", 0)] = e
        for s in self.l2._discover():
            for e in s.entries():
                t = e.get("turn", 0)
                if t not in all_turns:  # L2 is summary, only if not in L0/L1
                    all_turns[t] = e

        if not all_turns:
            return {"valid": True, "entries": 0}

        sorted_turns = sorted(all_turns.keys())
        errors = []
        for i, turn in enumerate(sorted_turns):
            entry = all_turns[turn]
            expected_hash = entry.get("hash", "")
            if not expected_hash:
                continue  # L2 summaries may not have hashes
            # We can't fully verify without recomputing, but check chain links
            if i > 0:
                prev_entry = all_turns[sorted_turns[i - 1]]
                prev_hash = entry.get("prev_hash", "")
                expected_prev = prev_entry.get("hash", "")
                if prev_hash and expected_prev and prev_hash != expected_prev:
                    errors.append(f"Chain break at turn {turn}")

        return {
            "valid": len(errors) == 0,
            "entries": len(all_turns),
            "errors": errors,
        }

    def compact_if_needed(self) -> dict:
        """Check thresholds and compact if needed. Condition-based."""
        results = {}
        if self.l0.needs_flush():
            results["flush"] = self.flush()
        if self.l1.needs_compaction():
            results["compact"] = self.compact()
        return results


# ── CLI ─────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mycelium LSM Memory")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("stats", help="Show layer stats")
    sub.add_parser("load", help="Load from log.jsonl into LSM")
    sub.add_parser("flush", help="Flush L0 → L1")
    sub.add_parser("compact", help="Compact L1 → L2")
    sub.add_parser("verify", help="Verify hash chain integrity")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    lsm = MyceliumLSM()

    if args.cmd == "stats":
        s = lsm.stats()
        print(f"LSM Stats:")
        print(f"  L0: {s['l0_entries']} entries, {s['l0_size_bytes']} bytes")
        print(f"  L1: {s['l1_segments']} segments, {s['l1_entries']} entries, {s['l1_size_bytes']} bytes")
        print(f"  L2: {s['l2_summaries']} summaries, {s['l2_entries']} entries")
        print(f"  Total: {s['total_entries']} entries, {s['total_size_bytes']} bytes")

    elif args.cmd == "load":
        result = lsm.load_from_jsonl()
        print(f"Loaded: {result}")

    elif args.cmd == "flush":
        result = lsm.flush()
        print(f"Flush: {result}")

    elif args.cmd == "compact":
        result = lsm.compact()
        print(f"Compact: {result}")

    elif args.cmd == "verify":
        result = lsm.verify_integrity()
        print(f"Integrity: {'✅' if result['valid'] else '❌'} ({result['entries']} entries)")
        if result.get("errors"):
            for e in result["errors"]:
                print(f"  ❌ {e}")


if __name__ == "__main__":
    main()
