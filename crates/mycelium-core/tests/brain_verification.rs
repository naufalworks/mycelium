use mycelium_core::brain;
use mycelium_core::types::{Entry, EntryType, Tier};
use mycelium_core::Storage;
use rusqlite::Connection;
use std::path::PathBuf;

/// Replay test: processes entries through the brain and measures atom/edge growth.
///
/// Reads from production DB if available; otherwise creates synthetic sample
/// entries to demonstrate the improvement from type-aware normalization
/// and W=2 edges vs the old n-gram approach (which produced 188,544 atoms
/// from 3,630 entries).
#[test]
fn test_brain_replay_10k_entries() -> rusqlite::Result<()> {
    let conn = Connection::open_in_memory()?;
    brain::create_tables(&conn)?;

    let entries = load_entries_or_sample();
    let total = entries.len();
    println!("\n=== Hebbian Crystal Brain Replay Test ===");
    println!("Replaying {} entries into brain...", total);

    let batch_size = 500;
    for (i, entry) in entries.iter().enumerate() {
        let text = format!("{} {}", entry.user, entry.assistant);
        brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, None, None)?;

        if (i + 1) % batch_size == 0 || i + 1 == total {
            let status = brain::brain_status(&conn)?;
            println!(
                "{:>8} entries: {:>6} atoms  {:>6} positions  {:>8} edges  {} pending",
                i + 1, status.atom_count, status.position_count, status.edge_count, status.pending_count
            );
        }
    }

    let status = brain::brain_status(&conn)?;
    println!("\n--- Results ---");
    println!("Atoms:  {} (old system: 188,544 for 3,630 entries)", status.atom_count);
    println!("Edges:  {} (old system: 35,701,558 for 3,630 entries)", status.edge_count);

    // The new system should produce significantly fewer atoms per entry
    // Even without LLM annotations, type-aware normalization collapses paths/UUIDs/numbers.
    // Old system: ~52 atoms/entry. New: should be well below that.
    let atoms_per_entry = status.atom_count as f64 / total as f64;
    println!("Atoms per entry: {:.2} (old system: ~52.0)", atoms_per_entry);
    assert!(
        atoms_per_entry < 52.0,
        "Atoms per entry ({:.2}) should be below old system baseline (52.0)",
        atoms_per_entry
    );

    Ok(())
}

/// Load entries from production DB; fall back to synthetic sample data.
fn load_entries_or_sample() -> Vec<Entry> {
    let storage_path = std::env::var("MYCELIUM_ROOT")
        .unwrap_or_else(|_| "/Users/azfar.naufal/.hermes/myceliumd/runtime".into());
    let db_path = PathBuf::from(&storage_path).join("mycelium.db");

    if let Ok(storage) = Storage::open(db_path) {
        if let Ok(entries) = storage.all_entries() {
            if !entries.is_empty() {
                return entries;
            }
        }
        println!("(production entries loaded but empty)");
    } else {
        println!("(no production DB found at {})", storage_path);
    }

    // Fallback: create sample entries with diverse structured data
    // that exposes the old n-gram splitting weaknesses
    println!("(using synthetic sample data)");
    let mut entries = Vec::new();
    for i in 0..100 {
        let user_msg = format!(
            "fix bug in /src/storage.rs at line {} with hash {}",
            i,
            hex::encode(&[i as u8; 20])
        );
        let asst_msg = format!(
            "fixed the hash chain verification in storage.rs, deployed to port {}",
            8080 + i
        );
        entries.push(Entry {
            turn: i as i64,
            tier: Tier::Core,
            entry_type: EntryType::Conversation,
            session: "test-replay".into(),
            ts: chrono::Utc::now(),
            user: user_msg,
            assistant: asst_msg,
            entities: vec![],
            prev_hash: String::new(),
            hash: String::new(),
            finding: None,
            verdict: None,
            annotation: None,
        });
    }
    entries
}
