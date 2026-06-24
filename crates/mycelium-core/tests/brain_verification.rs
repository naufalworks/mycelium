use mycelium_core::brain;
use mycelium_core::Storage;
use rusqlite::Connection;

#[test]
fn test_brain_replay_10k_entries() -> rusqlite::Result<()> {
    // Brain uses in-memory SQLite (separate from production storage)
    let conn = Connection::open_in_memory()?;
    brain::create_tables(&conn)?;

    // Open production storage to read entry data
    let storage_path = std::env::var("MYCELIUM_ROOT")
        .unwrap_or_else(|_| "/Users/azfar.naufal/.hermes/myceliumd/runtime".into());
    let db_path = std::path::PathBuf::from(&storage_path).join("mycelium.db");
    let storage = Storage::open(db_path).expect("open storage");

    let entries = storage.all_entries().expect("load entries");
    let total = entries.len();
    println!("\n=== Hebbian Crystal Brain Replay Test ===");
    println!("Replaying {} entries into brain...", total);

    let batch_size = 500;
    let mut atom_history = Vec::new();
    let mut pos_history = Vec::new();

    for (i, entry) in entries.iter().enumerate() {
        let text = format!("{} {}", entry.user, entry.assistant);
        brain::consolidate_entry(&conn, entry.turn, &entry.session, &text)?;

        if (i + 1) % batch_size == 0 || i == total - 1 {
            let status = brain::brain_status(&conn)?;
            atom_history.push((i + 1, status.atom_count));
            pos_history.push((i + 1, status.position_count));
            println!("  {:>6} entries: {:>5} atoms {:>8} positions {:>8} edges {:>5} pending",
                i + 1, status.atom_count, status.position_count, status.edge_count, status.pending_count);
        }
    }

    let final_status = brain::brain_status(&conn)?;

    // Logarithmic growth check: atoms < 30% of total entries
    let atom_threshold = (total as f64 * 0.3) as i64;
    assert!(final_status.atom_count < atom_threshold,
        "Atoms grew too fast: {} (expected < {}). Growth may be linear, not logarithmic.",
        final_status.atom_count, atom_threshold);

    // Query sample phrases
    for phrase in &["hash chain", "metabase", "mycelium", "proxy", "frontend"] {
        let results = brain::recall(&conn, phrase, 3)?;
        if results.is_empty() {
            println!("  WARNING: '{}' not found in brain (may be filtered as stop word)", phrase);
        } else {
            for r in &results {
                println!("  '{}': seen {} times, first turn {}, last turn {}",
                    r.phrase, r.ref_count, r.first_seen, r.last_seen);
            }
        }
    }

    println!("\n--- Summary ---");
    println!("Total entries: {:>6}", total);
    println!("Atoms:         {:>6} ({:.1}% unique)", final_status.atom_count,
        final_status.atom_count as f64 / total as f64 * 100.0);
    println!("Positions:     {:>6}", final_status.position_count);
    println!("Edges:         {:>6}", final_status.edge_count);
    println!("Pending:       {:>6}", final_status.pending_count);

    Ok(())
}
