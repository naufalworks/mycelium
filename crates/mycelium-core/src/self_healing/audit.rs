//! Writes markdown audit files to `bugfixes/` with replay SQL.

use std::path::{Path, PathBuf};

use chrono::Utc;

use crate::self_healing::llm_agent::RepairLog;

/// Writes per-repair audit trails as markdown files.
pub struct AuditWriter {
    bugfixes_dir: PathBuf,
}

impl AuditWriter {
    pub fn new(root_dir: &Path) -> Self {
        let dir = root_dir.join("bugfixes");
        std::fs::create_dir_all(&dir).ok();
        Self { bugfixes_dir: dir }
    }

    /// Write a repair audit file. Returns the file path.
    pub fn write_repair_log(&self, log: &RepairLog) -> anyhow::Result<PathBuf> {
        let date = Utc::now().format("%Y-%m-%d");
        let uuid = uuid::Uuid::new_v4();
        let filename = format!("{}-hash-chain-repair-{}.md", date, uuid);
        let path = self.bugfixes_dir.join(&filename);

        let content = format!(
            r#"# Hash Chain Repair — {}

**Snapshot ID**: `{}`
**Model Used**: kimi-k2.7 / minimax-m3
**Tool Calls**: {}
**Duration**: {:.1}s
**Turns Repaired**: {}
**Final Broken Count**: {}

## Repairs
{}

## Replay SQL
```sql
BEGIN IMMEDIATE;
{}
COMMIT;
```

## Rollback
```bash
sqlite3 mycelium.db ".restore {} mycelium.db"
```
"#,
            Utc::now().format("%Y-%m-%d %H:%M:%S"),
            log.snapshot_id,
            log.total_tool_calls,
            log.duration.as_secs_f64(),
            log.repaired_turns.len(),
            log.final_broken_count,
            self.repair_table(log),
            self.replay_sql(log),
            log.snapshot_id,
        );

        std::fs::write(&path, content)?;
        tracing::info!("audit written: {}", path.display());
        Ok(path)
    }

    fn repair_table(&self, log: &RepairLog) -> String {
        let mut rows = String::new();
        for turn in &log.repaired_turns {
            rows.push_str(&format!("| {} | repaired | walk forward |\n", turn));
        }
        if rows.is_empty() {
            rows = "| (none) | | |\n".into();
        }
        format!("| Turn | Action | Reason |\n|------|--------|--------|\n{}", rows)
    }

    fn replay_sql(&self, log: &RepairLog) -> String {
        if log.repaired_turns.is_empty() {
            "-- no changes".into()
        } else {
            let mut sql = String::new();
            for turn in &log.repaired_turns {
                sql.push_str(&format!(
                    "UPDATE entries SET prev_hash = (SELECT hash FROM entries WHERE turn = {}) WHERE turn = {};\n",
                    turn.saturating_sub(1), turn
                ));
            }
            sql
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn make_log(repaired_turns: Vec<i64>) -> RepairLog {
        RepairLog {
            snapshot_id: "snap-test-001".into(),
            repaired_turns,
            total_tool_calls: 5,
            errors: vec![],
            final_broken_count: 0,
            duration: Duration::from_secs_f64(12.3),
        }
    }

    #[test]
    fn write_creates_markdown_file() {
        let dir = tempfile::tempdir().unwrap();
        let writer = AuditWriter::new(dir.path());
        let log = make_log(vec![3, 7]);
        let path = writer.write_repair_log(&log).unwrap();

        assert!(path.exists());
        assert!(path.to_string_lossy().contains("hash-chain-repair"));

        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("snap-test-001"));
        assert!(content.contains("Turns Repaired**: 2"));
        assert!(content.contains("| 3 | repaired |"));
        assert!(content.contains("UPDATE entries SET prev_hash"));
        assert!(content.contains("turn = 2) WHERE turn = 3"));
        assert!(content.contains("turn = 6) WHERE turn = 7"));
        assert!(content.contains("BEGIN IMMEDIATE;"));
        assert!(content.contains("COMMIT;"));
        assert!(content.contains(".restore snap-test-001"));
    }

    #[test]
    fn write_empty_repaired_turns() {
        let dir = tempfile::tempdir().unwrap();
        let writer = AuditWriter::new(dir.path());
        let log = make_log(vec![]);
        let path = writer.write_repair_log(&log).unwrap();

        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("| (none) |"));
        assert!(content.contains("-- no changes"));
    }

    #[test]
    fn creates_bugfixes_dir_if_missing() {
        let dir = tempfile::tempdir().unwrap();
        let bugfixes = dir.path().join("bugfixes");
        assert!(!bugfixes.exists());

        let writer = AuditWriter::new(dir.path());
        let log = make_log(vec![1]);
        writer.write_repair_log(&log).unwrap();

        assert!(bugfixes.exists());
    }
}
