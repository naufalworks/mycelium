use mycelium_core::brain;
use mycelium_core::types::{EntityAnnotation, MemoryAnnotation, MemoryItem};
use rusqlite::params;
use rusqlite::Connection;

#[test]
fn test_annotation_round_trip() -> rusqlite::Result<()> {
    let conn = Connection::open_in_memory()?;
    brain::create_tables(&conn)?;

    let ann = MemoryAnnotation {
        phrases: vec![
            MemoryItem { text: "hash chain verification fix".into(), importance: 5.0 },
            MemoryItem { text: "storage.rs bug".into(), importance: 4.0 },
        ],
        actions: vec![
            MemoryItem { text: "fix hash chain verification".into(), importance: 5.0 },
        ],
        entities: vec![
            EntityAnnotation {
                name: "storage.rs".into(),
                typ: "file".into(),
                aliases: vec!["storage module".into()],
                importance: 4.0,
            },
            EntityAnnotation {
                name: "hash chain".into(),
                typ: "concept".into(),
                aliases: vec![],
                importance: 5.0,
            },
        ],
    };

    // Process annotated entry
    brain::consolidate_entry(
        &conn,
        1,
        "test-session",
        "I fixed the hash chain bug in storage.rs",
        Some(&ann),
        None,
    )?;

    // Verify: atoms were created
    let atom_count: i64 = conn.query_row("SELECT COUNT(*) FROM atoms", [], |row| row.get(0))?;
    assert!(atom_count > 0, "should create atoms from annotation + text");

    // Verify: entity was registered
    let entity_count: i64 =
        conn.query_row("SELECT COUNT(*) FROM entity_registry", [], |row| row.get(0))?;
    assert!(entity_count >= 2, "should register both entities");

    // Verify: edges exist (W=2 + entity bridge)
    let edge_count: i64 = conn.query_row("SELECT COUNT(*) FROM edges", [], |row| row.get(0))?;
    assert!(edge_count > 0, "should create edges");

    // Verify: atom has importance
    let importance: f64 = conn.query_row(
        "SELECT importance FROM atoms WHERE phrase LIKE '%hash%' LIMIT 1",
        [],
        |row| row.get(0),
    )?;
    assert_eq!(importance, 5.0, "hash-related atom should have importance 5");

    // Process second entry with overlapping entity
    let ann2 = MemoryAnnotation {
        phrases: vec![MemoryItem {
            text: "storage.rs path resolution".into(),
            importance: 3.0,
        }],
        actions: vec![],
        entities: vec![EntityAnnotation {
            name: "storage.rs".into(),
            typ: "file".into(),
            aliases: vec![],
            importance: 3.0,
        }],
    };
    brain::consolidate_entry(
        &conn,
        2,
        "test-session",
        "the path resolution in storage.rs",
        Some(&ann2),
        None,
    )?;

    // Verify: entity ref_count incremented
    let ref_count: i64 = conn.query_row(
        "SELECT ref_count FROM entity_registry WHERE name = ?1",
        params!["storage.rs"],
        |row| row.get(0),
    )?;
    assert_eq!(
        ref_count, 2,
        "storage.rs should have ref_count=2 after two mentions"
    );

    Ok(())
}
