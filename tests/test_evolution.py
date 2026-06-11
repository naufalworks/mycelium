#!/usr/bin/env python3
"""Tests for evolution.py — LLM Self-Evolution Engine."""
import json, os, shutil, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Add scripts to path
SCRIPTS = Path.home() / "Documents" / "mycelium" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import importlib
evo = importlib.import_module("evolution")


class TmpEvoDir(unittest.TestCase):
    """Base class — isolates evolution data to a temp dir."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = {}
        for attr in ("EVO_DIR", "FAILURES_LOG", "PATCHES_LOG", "STATS_DB"):
            self._orig[attr] = getattr(evo, attr)
        evo.EVO_DIR = Path(self.tmpdir)
        evo.FAILURES_LOG = Path(self.tmpdir) / "failures.jsonl"
        evo.PATCHES_LOG = Path(self.tmpdir) / "patches.jsonl"
        evo.STATS_DB = Path(self.tmpdir) / "stats.db"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for attr, val in self._orig.items():
            setattr(evo, attr, val)


class TestCorrectionDetection(unittest.TestCase):
    """Watch — correction signal detection."""

    def test_explicit_correction(self):
        signals = evo.detect_corrections("that's wrong, it should be port 3000")
        cats = [s["category"] for s in signals]
        self.assertIn("explicit-correction", cats)

    def test_memory_failure(self):
        signals = evo.detect_corrections("you forgot to run the precheck")
        self.assertIn("memory-failure", [s["category"] for s in signals])

    def test_already_told(self):
        signals = evo.detect_corrections("I already told you about the database")
        self.assertIn("memory-failure", [s["category"] for s in signals])

    def test_behavioral_drift(self):
        signals = evo.detect_corrections("I said don't use that endpoint, stop doing it")
        self.assertIn("behavioral-drift", [s["category"] for s in signals])

    def test_wrong_context(self):
        signals = evo.detect_corrections("that's not the right port")
        self.assertIn("wrong-context", [s["category"] for s in signals])

    def test_append_discipline(self):
        signals = evo.detect_corrections("check the mycelium status")
        self.assertIn("append-discipline", [s["category"] for s in signals])

    def test_no_correction(self):
        signals = evo.detect_corrections("this looks great, let's proceed")
        self.assertEqual(len(signals), 0)

    def test_friendly_message(self):
        signals = evo.detect_corrections("thanks for the help!")
        self.assertEqual(len(signals), 0)

    def test_deduplication(self):
        signals = evo.detect_corrections("you forgot, you're not checking, already told you")
        cats = [s["category"] for s in signals]
        self.assertEqual(cats.count("memory-failure"), 1)


class TestFailureLogger(TmpEvoDir):
    """Log — failure event storage."""

    def test_log_creates_entry(self):
        entry = evo.log_failure("test", "memory-failure", "you forgot", "run check")
        self.assertIn("id", entry)
        self.assertEqual(entry["category"], "memory-failure")
        self.assertTrue(evo.FAILURES_LOG.exists())

    def test_log_appends_multiple(self):
        evo.log_failure("s1", "cat1", "msg1", "fix1")
        evo.log_failure("s2", "cat2", "msg2", "fix2")
        lines = evo.FAILURES_LOG.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)

    def test_stats_db_updated(self):
        evo.log_failure("test", "append-discipline", "forgot", "append")
        self.assertTrue(evo.STATS_DB.exists())
        import sqlite3
        conn = sqlite3.connect(str(evo.STATS_DB))
        row = conn.execute("SELECT hit_count FROM patterns WHERE category='append-discipline'").fetchone()
        conn.close()
        self.assertEqual(row[0], 1)


class TestPatternClusterer(TmpEvoDir):
    """Cluster — group failures into patterns."""

    def test_empty_cluster(self):
        self.assertEqual(evo.cluster_patterns(), [])

    def test_cluster_with_data(self):
        import sqlite3
        conn = evo._get_stats_db()
        conn.execute("INSERT INTO patterns VALUES ('test-cat', 3, 't1', 't3', NULL)")
        conn.commit()
        conn.close()
        patterns = evo.cluster_patterns()
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["category"], "test-cat")
        self.assertEqual(patterns[0]["hit_count"], 3)
        self.assertTrue(patterns[0]["at_threshold"])


class TestPatchGenerator(TmpEvoDir):
    """Generate — create patches from patterns."""

    def _seed_pattern(self, category, count):
        import sqlite3
        conn = evo._get_stats_db()
        conn.execute("INSERT OR REPLACE INTO patterns VALUES (?, ?, 't1', 't5', NULL)",
                     (category, count))
        conn.commit()
        conn.close()

    def test_generate_patch(self):
        self._seed_pattern("memory-failure", 5)
        patch = evo.generate_patch("memory-failure")
        self.assertIsNotNone(patch)
        self.assertEqual(patch["pattern"], "memory-failure")
        self.assertEqual(patch["status"], "active")
        self.assertEqual(patch["strength"], "soft")

    def test_generate_below_threshold(self):
        self._seed_pattern("rare-cat", 1)
        patch = evo.generate_patch("rare-cat")
        self.assertIsNone(patch)

    def test_generate_force(self):
        self._seed_pattern("rare-cat", 1)
        patch = evo.generate_patch("rare-cat", force=True)
        self.assertIsNotNone(patch)

    def test_generate_escalates_existing(self):
        self._seed_pattern("memory-failure", 5)
        p1 = evo.generate_patch("memory-failure")
        self.assertEqual(p1["strength"], "soft")
        # Bump count to trigger escalation
        self._seed_pattern("memory-failure", 10)
        p2 = evo.generate_patch("memory-failure")
        self.assertEqual(p2["strength"], "hard")


class TestPatchLoader(TmpEvoDir):
    """Load — session injection."""

    def test_load_empty(self):
        self.assertEqual(evo.load_patches(), "")

    def test_load_active_patches(self):
        evo._save_patches([{
            "id": "p1", "pattern": "test", "constraint": "Do X",
            "status": "active", "strength": "soft", "clean_sessions": 0,
            "hit_count": 2, "last_seen": "", "created_at": "",
        }])
        output = evo.load_patches()
        self.assertIn("Active Evolution Patches", output)
        self.assertIn("Do X", output)

    def test_load_excludes_retired(self):
        evo._save_patches([{
            "id": "p1", "pattern": "test", "constraint": "Do X",
            "status": "retired", "strength": "soft", "clean_sessions": 5,
            "hit_count": 2, "last_seen": "", "created_at": "",
        }])
        self.assertEqual(evo.load_patches(), "")

    def test_strength_ordering(self):
        evo._save_patches([
            {"id": "p1", "pattern": "soft-one", "constraint": "Soft",
             "status": "active", "strength": "soft", "clean_sessions": 0,
             "hit_count": 2, "last_seen": "", "created_at": ""},
            {"id": "p2", "pattern": "critical-one", "constraint": "Critical",
             "status": "active", "strength": "critical", "clean_sessions": 0,
             "hit_count": 5, "last_seen": "", "created_at": ""},
        ])
        output = evo.load_patches()
        self.assertLess(output.index("Critical"), output.index("Soft"))


class TestEvaluator(TmpEvoDir):
    """Evaluate — patch effectiveness."""

    def test_evaluate_retires_after_clean(self):
        evo._save_patches([{
            "id": "p1", "pattern": "test-cat", "constraint": "Do X",
            "status": "active", "strength": "soft", "clean_sessions": 4,
            "hit_count": 2, "last_seen": "", "created_at": "",
        }])
        # No recent failures for test-cat → clean_sessions goes 4→5 → retired
        evo.evaluate()
        updated = evo._load_patches()
        self.assertEqual(updated[0]["status"], "retired")

    def test_evaluate_escalates_on_recurrence(self):
        evo._save_patches([{
            "id": "p1", "pattern": "test-cat", "constraint": "Do X",
            "status": "active", "strength": "soft", "clean_sessions": 3,
            "hit_count": 2, "last_seen": "", "created_at": "",
        }])
        # Add a recent failure (with current timestamp)
        with open(evo.FAILURES_LOG, "a") as f:
            f.write(json.dumps({
                "category": "test-cat",
                "ts": datetime.now(timezone.utc).isoformat()
            }) + "\n")
        evo.evaluate()
        updated = evo._load_patches()
        self.assertEqual(updated[0]["strength"], "hard")
        self.assertEqual(updated[0]["clean_sessions"], 0)

    def test_evaluate_ignores_old_failures(self):
        """Failures older than 7 days should not prevent retirement."""
        evo._save_patches([{
            "id": "p1", "pattern": "old-cat", "constraint": "Do X",
            "status": "active", "strength": "soft", "clean_sessions": 4,
            "hit_count": 2, "last_seen": "", "created_at": "",
        }])
        # Add an OLD failure (30 days ago)
        with open(evo.FAILURES_LOG, "a") as f:
            old_ts = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=30)).isoformat()
            f.write(json.dumps({"category": "old-cat", "ts": old_ts}) + "\n")
        evo.evaluate()
        updated = evo._load_patches()
        # Should retire despite old failure
        self.assertEqual(updated[0]["status"], "retired")


class TestStatus(TmpEvoDir):
    """Status dashboard."""

    def test_status_empty(self):
        output = evo.status()
        self.assertIn("Evolution Engine Status", output)
        self.assertIn("Active patches: 0", output)

    def test_status_with_patches(self):
        evo._save_patches([{
            "id": "p1", "pattern": "test", "constraint": "Do X",
            "status": "active", "strength": "hard", "clean_sessions": 2,
            "hit_count": 3, "last_seen": "", "created_at": "",
        }])
        output = evo.status()
        self.assertIn("Active patches: 1", output)
        self.assertIn("test", output)


class TestStrengthEscalation(unittest.TestCase):
    """Strength level progression."""

    def test_soft_to_hard(self):
        self.assertEqual(evo._escalate_strength("soft"), "hard")

    def test_hard_to_critical(self):
        self.assertEqual(evo._escalate_strength("hard"), "critical")

    def test_critical_stays(self):
        self.assertEqual(evo._escalate_strength("critical"), "critical")


class TestAtomicSave(TmpEvoDir):
    """Verify _save_patches uses atomic write."""

    def test_save_then_load_roundtrip(self):
        patches = [
            {"id": "p1", "pattern": "test", "constraint": "X",
             "status": "active", "strength": "soft", "clean_sessions": 0,
             "hit_count": 1, "last_seen": "", "created_at": ""},
        ]
        evo._save_patches(patches)
        loaded = evo._load_patches()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "p1")

    def test_no_tmp_file_left(self):
        evo._save_patches([{"id": "p1", "pattern": "t", "constraint": "c",
                            "status": "active", "strength": "soft",
                            "clean_sessions": 0, "hit_count": 1,
                            "last_seen": "", "created_at": ""}])
        tmp = evo.PATCHES_LOG.with_suffix(".tmp")
        self.assertFalse(tmp.exists())


if __name__ == "__main__":
    unittest.main()
