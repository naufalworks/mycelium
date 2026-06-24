# Hebbian Brain — LLM-Guided Memory Transducer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Hebbian Brain's raw n-gram splitting with LLM-guided memory annotation + type-aware extraction to compress atom growth from linear (188K atoms / 3.6K entries) to logarithmic (< 1K).

**Architecture:** The proxy injects a memory instruction into the LLM's system prompt. The LLM emits a `<memory>` JSON block in its response specifying canonical phrases, actions, and entities to remember. The brain processes these alongside rule-based type extraction (paths, UUIDs, hashes, etc.), using W=2 bounded edges and 2.5× entity bridge edges. The proxy is unchanged beyond 3 insertion points; the brain API is unchanged beyond one optional parameter on `consolidate_entry`.

**Tech Stack:** Rust 2024, rusqlite 0.32, serde_json 1, regex on workspace deps.

## Global Constraints

- All new columns are NULLABLE / have defaults — existing data is never migrated
- `consolidate_entry` keeps its existing signature working (annotation is optional)
- Proxy must not add network calls or external services for memory processing
- `<memory>` block is stripped from the user-visible response before delivery
- When no `<memory>` block is present, behavior must match current system exactly
- entity_registry table is additive — no existing queries reference it

---

### Task 1: Define MemoryAnnotation data types

**Files:**
- Modify: `crates/mycelium-core/src/types.rs` (append before EOF)
- Test: None (pure data types, no behavior)

**Interfaces:**
- Consumes: Nothing
- Produces: `MemoryAnnotation`, `MemoryItem`, `EntityAnnotation` structs (`Serialize + Deserialize`)

- [ ] **Step 1: Add the three annotation types to types.rs**

Add after the `MemoryFact` struct definition:

```rust
/// A single item (phrase or action) from the LLM's <memory> annotation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryItem {
    /// Canonical form of the phrase to remember (e.g. "hash chain verification fix")
    pub text: String,
    /// LLM-assigned importance 1-5 (5 = most important)
    pub importance: f64,
}

/// An entity extracted and named by the LLM.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntityAnnotation {
    /// Canonical entity name (e.g. "storage.rs")
    pub name: String,
    /// Entity type (e.g. "file", "concept", "person")
    #[serde(rename = "type")]
    pub typ: String,
    /// Alternative names the LLM has seen this entity called
    #[serde(default)]
    pub aliases: Vec<String>,
    /// LLM-assigned importance 1-5
    pub importance: f64,
}

/// Complete memory annotation from the LLM's <memory> block.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryAnnotation {
    /// Canonical noun phrases to remember, each with importance
    #[serde(default)]
    pub phrases: Vec<MemoryItem>,
    /// Key actions, each with importance
    #[serde(default)]
    pub actions: Vec<MemoryItem>,
    /// Named entities mentioned, each with type, aliases, importance
    #[serde(default)]
    pub entities: Vec<EntityAnnotation>,
}
```

- [ ] **Step 2: Verify compilation**

```bash
cd /Users/azfar.naufal/Documents/mycelium && cargo check -p mycelium-core 2>&1 | tail -5
```
Expected: `Compiling mycelium-core v0.1.0 ...` then `Finished` with no errors.

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/src/types.rs
git commit -m "feat(core): add MemoryAnnotation types for LLM-guided extraction"
```

---

### Task 2: Add annotation column to entries table

**Files:**
- Modify: `crates/mycelium-core/src/storage.rs` (append_entry INSERT, row_to_entry SELECT, Entry struct field)
- Modify: `crates/mycelium-core/src/types.rs` (Entry struct + annotation field)

**Interfaces:**
- Consumes: `MemoryAnnotation` type from Task 1
- Produces: `Entry.annotation: Option<MemoryAnnotation>` field
- Ripple: Any code building an `Entry` literal needs `annotation: None`

- [ ] **Step 1: Read the current Entry struct definition**

Read `crates/mycelium-core/src/types.rs` lines around struct `Entry`.

```bash
grep -n "pub struct Entry" -A 30 crates/mycelium-core/src/types.rs
```

- [ ] **Step 2: Add annotation field to Entry struct**

```rust
/// LLM memory annotation from the proxy's <memory> block injection.
#[serde(default, skip_serializing_if = "Option::is_none")]
pub annotation: Option<String>,
```

Insert this field after `verdict` in the Entry struct.

- [ ] **Step 3: Add annotation column to the entries CREATE TABLE**

In `initialize_schema` in `storage.rs`, change the entries table INSERT columns list to include `annotation`:

```sql
-- Add after the verdict column:
annotation  TEXT DEFAULT NULL,
```

- [ ] **Step 4: Update `append_entry` INSERT to include annotation**

Change the INSERT in `append_entry` (storage.rs around line 213):

```rust
"INSERT INTO entries (turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict, annotation)
 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
```

Add the annotation param:
```rust
entry.annotation.as_deref().unwrap_or(""),
                // ^ after entry.verdict,
```

- [ ] **Step 5: Update `row_to_entry` SELECT to include annotation**

Find the `row_to_entry` method and add `annotation` column to the SELECT query. Inside the mapper callback, add:

```rust
let annotation: Option<String> = row.get(12).ok().filter(|s: &String| !s.is_empty());
```

Add `annotation` to the Entry constructor.

- [ ] **Step 6: Fix all Entry constructors outside storage.rs**

```bash
grep -rn "Entry {" crates/mycelium-core/src/ --include="*.rs" | grep -v test | grep -v "\.rs:"
```

For each one, add `annotation: None,`. This is needed because the struct now has a new field.

- [ ] **Step 7: Verify compilation**

```bash
cargo check -p mycelium-core 2>&1 | tail -10
```
Expected: clean compile.

- [ ] **Step 8: Commit**

```bash
git add crates/mycelium-core/src/
git commit -m "feat(core): add annotation column to entries table and Entry struct"
```

---

### Task 3: Add importance column to atoms table + entity_registry table

**Files:**
- Modify: `crates/mycelium-core/src/brain.rs` (create_tables, Atom struct)
- Modify: `crates/mycelium-core/src/types.rs` (Atom struct if there)

**Interfaces:**
- Consumes: Nothing
- Produces: `atoms.importance` column, `entity_registry` table

- [ ] **Step 1: Read Atom struct definition**

```bash
grep -n "pub struct Atom" crates/mycelium-core/src/brain.rs
```

- [ ] **Step 2: Add importance to Atom struct**

```rust
/// LLM-assigned importance (1-5, with 1.0 = default for rule-based atoms)
pub importance: f64,
```

- [ ] **Step 3: Add importance column to atoms CREATE TABLE**

In `create_tables()` in brain.rs, change:

```sql
CREATE TABLE IF NOT EXISTS atoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL UNIQUE,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    ref_count INTEGER NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 1.0
);
```

- [ ] **Step 4: Add entity_registry table after atoms table**

After the atoms table CREATE in `create_tables()`, add:

```sql
CREATE TABLE IF NOT EXISTS entity_registry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    entity_type  TEXT NOT NULL DEFAULT 'concept',
    aliases      TEXT NOT NULL DEFAULT '[]',
    importance   REAL NOT NULL DEFAULT 1.0,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    ref_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entity_name ON entity_registry(name);
```

- [ ] **Step 5: Update upsert_atom to set importance**

Change `upsert_atom` signature:
```rust
pub fn upsert_atom(conn: &Connection, phrase: &str, turn: i64, importance: f64) -> rusqlite::Result<i64> {
```

Update the INSERT to include importance:
```rust
"INSERT INTO atoms (phrase, first_seen, last_seen, ref_count, importance) VALUES (?1, ?2, ?2, 1, ?3)
 ON CONFLICT(phrase) DO UPDATE SET last_seen = ?2, ref_count = ref_count + 1, importance = MAX(importance, ?3)",
```

- [ ] **Step 6: Fix all call sites of upsert_atom**

```bash
grep -rn "upsert_atom" crates/mycelium-core/src/ --include="*.rs"
```

Every call site currently passes `(conn, phrase, turn)` — add `, 1.0` as the default importance for rule-based atoms.

- [ ] **Step 7: Verify compilation**

```bash
cargo check -p mycelium-core 2>&1 | tail -10
```

- [ ] **Step 8: Commit**

```bash
git add crates/mycelium-core/src/brain.rs crates/mycelium-core/src/types.rs
git commit -m "feat(core): add importance to atoms, create entity_registry table"
```

---

### Task 4: Implement type-aware normalization (rule-based extraction)

**Files:**
- Modify: `crates/mycelium-core/src/brain.rs` (normalize, extract_atoms functions)

**Interfaces:**
- Consumes: Nothing
- Produces: `normalize()` detects types and applies type-specific normalization; `extract_atoms()` unchanged signature but uses new normalize

- [ ] **Step 1: Read the current normalize() and extract_atoms() functions**

```bash
sed -n '141,180p' crates/mycelium-core/src/brain.rs
```

- [ ] **Step 2: Replace normalize() with type-aware version**

```rust
/// Normalize a phrase with type-aware normalization.
/// Detects the phrase type and applies the appropriate normalization strategy.
pub fn normalize(phrase: &str) -> String {
    let s = phrase.trim().to_lowercase();

    // 1. Path normalization: strip user prefix, keep semantic location
    if looks_like_path(&s) {
        return normalize_path(&s);
    }

    // 2. UUID normalization: all UUIDs become {uuid}
    if looks_like_uuid(&s) {
        return "{uuid}".to_string();
    }

    // 3. Hash normalization (hex 32+ chars)
    if looks_like_hash(&s) {
        return "{hash}".to_string();
    }

    // 4. Number normalization (integers, decimals, hex numbers)
    if looks_like_number(&s) {
        return "{number}".to_string();
    }

    // 5. Identifier normalization (CamelCase, snake_case, PascalCase)
    if looks_like_identifier(&s) {
        return normalize_identifier(&s);
    }

    // 6. URL normalization
    if looks_like_url(&s) {
        return normalize_url(&s);
    }

    // 7. Error code normalization (EACCES, 404, ENOENT, etc.)
    if looks_like_error_code(&s) {
        return s; // Already normalized
    }

    // 8. Date/timestamp normalization
    if looks_like_date(&s) {
        return "{date}".to_string();
    }

    // 9. Default: Porter-style stemmer + stop words
    stem_word(&s)
}
```

- [ ] **Step 3: Add helper classifiers**

```rust
fn looks_like_path(s: &str) -> bool {
    // Contains path separators or file extensions like .rs, .py, .ts
    s.contains('/') || s.contains('\\') || s.contains(".rs") || s.contains(".py")
        || s.contains(".ts") || s.starts_with('/')
}

fn looks_like_uuid(s: &str) -> bool {
    let clean = s.replace('-', "");
    clean.len() == 32 && clean.chars().all(|c| c.is_ascii_hexdigit())
}

fn looks_like_hash(s: &str) -> bool {
    let clean = s.trim_end_matches(|c: char| !c.is_ascii_hexdigit() && c != ':');
    clean.len() >= 40 && clean.chars().all(|c| c.is_ascii_hexdigit())
        || (s.len() >= 8 && s.len() <= 64 && s.chars().all(|c| c.is_ascii_hexdigit()) && !looks_like_uuid(s))
}

fn looks_like_number(s: &str) -> bool {
    s.chars().all(|c| c.is_ascii_digit() || c == '.' || c == '-' || c == '+')
        || (s.starts_with("0x") && s[2..].chars().all(|c| c.is_ascii_hexdigit()))
}

fn looks_like_identifier(s: &str) -> bool {
    s.contains("::") || s.contains("::")  // Rust paths
        || (s.contains(|c: char| c.is_ascii_uppercase())  // CamelCase
            && s.contains(|c: char| c == '.' || c == ':' || c == '_'))
        || s.chars().any(|c| c == '_')  // snake_case
}

fn looks_like_url(s: &str) -> bool {
    s.starts_with("http://") || s.starts_with("https://") || s.contains("://")
}

fn looks_like_error_code(s: &str) -> bool {
    // EACCES, ENOENT, HTTP 404, exit code 1, etc.
    (s.len() <= 8 && s.starts_with('e') && s[1..].chars().all(|c| c.is_ascii_uppercase()))
        || s.starts_with("exit code") || s.contains("error:") || s.starts_with("err_")
}

fn looks_like_date(s: &str) -> bool {
    // ISO dates, timestamps
    s.contains('-') && s.len() >= 8 && s.chars().filter(|c| *c == '-').count() >= 2
}
```

- [ ] **Step 4: Add normalization helpers**

```rust
fn normalize_path(s: &str) -> String {
    // Strip user-specific prefix
    let cleaned = s
        .replace("/Users/", "/~/")
        .replace("/home/", "/~/");
    // Extract meaningful tail
    cleaned
}

fn normalize_identifier(s: &str) -> String {
    // Convert CamelCase to lowercase_with_underscores
    let mut result = String::new();
    for c in s.chars() {
        if c.is_ascii_uppercase() {
            if !result.is_empty() && !result.ends_with('_') {
                result.push('_');
            }
            result.push(c.to_ascii_lowercase());
        } else if c == ':' || c == '.' {
            result.push('_');
        } else {
            result.push(c);
        }
    }
    result
}

fn normalize_url(s: &str) -> String {
    // Strip protocol and trailing slash
    s.trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string()
}

fn stem_word(s: &str) -> String {
    // Porter-style basic stemmer (reuse existing logic from current normalize())
    if s.ends_with("ing") && s.len() > 4 {
        s[..s.len() - 3].to_string()
    } else if s.ends_with("ed") && s.len() > 3 {
        s[..s.len() - 2].to_string()
    } else if s.ends_with("ly") && s.len() > 3 {
        s[..s.len() - 2].to_string()
    } else if s.ends_with("s") && s.len() > 3 && !s.ends_with("ss") {
        s[..s.len() - 1].to_string()
    } else {
        s.to_string()
    }
}
```

- [ ] **Step 5: Keep extract_atoms() signature unchanged**

`extract_atoms` continues to split into bi-grams and tri-grams, but the individual words/phrases are now normalized through the new type-aware `normalize()`. The existing loop logic stays the same:

```rust
pub fn extract_atoms(text: &str) -> Vec<String> {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() < 2 {
        return vec![];
    }
    let mut atoms: Vec<String> = Vec::new();
    for w in words.windows(2) {
        let joined = format!("{} {}", w[0], w[1]);
        atoms.push(normalize(&joined));
    }
    if words.len() >= 3 {
        for w in words.windows(3) {
            let joined = format!("{} {} {}", w[0], w[1], w[2]);
            atoms.push(normalize(&joined));
        }
    }
    atoms
}
```

The key difference: previously `normalize` only stemmed. Now it also detects types and applies type-specific normalization. This is where the dedup happens.

- [ ] **Step 6: Write unit tests for type-aware normalization**

Add to the `#[cfg(test)] mod tests` block in brain.rs:

```rust
#[test]
fn test_normalize_path() {
    let result = normalize("/Users/azfar/src/storage.rs");
    assert!(result.contains("storage.rs") || result.contains("storage"), "path normalization: {}", result);
}

#[test]
fn test_normalize_uuid() {
    assert_eq!(normalize("550e8400-e29b-41d4-a716-446655440000"), "{uuid}");
}

#[test]
fn test_normalize_hash() {
    assert_eq!(normalize("433b12ac60da89ef1234567890abcdef12345678"), "{hash}");
}

#[test]
fn test_normalize_number() {
    assert_eq!(normalize("234"), "{number}");
    assert_eq!(normalize("8080"), "{number}");
}

#[test]
fn test_normalize_identifier() {
    let result = normalize("Storage::append_entry");
    assert_eq!(result, "storage__append_entry");
}

#[test]
fn test_normalize_stemming() {
    assert_eq!(normalize("running"), "run");
    assert_eq!(normalize("fixed"), "fix");
}
```

- [ ] **Step 7: Run tests**

```bash
cargo test -p mycelium-core -- test_normalize_ --nocapture 2>&1 | tail -15
```
Expected: all normalization tests PASS.

- [ ] **Step 8: Commit**

```bash
git add crates/mycelium-core/src/brain.rs
git commit -m "feat(core): type-aware normalization for paths, UUIDs, hashes, identifiers, URLs, dates"
```

---

### Task 5: Rewrite consolidate_entry with annotation processing + W=2 + entity bridge

**Files:**
- Modify: `crates/mycelium-core/src/brain.rs` (consolidate_entry, add helper functions)
- Modify: `crates/mycelium-core/tests/brain_verification.rs` (update consolidate_entry calls)

**Interfaces:**
- Consumes: `MemoryAnnotation` type (Task 1), `upsert_atom(..., importance)` (Task 3), type-aware normalize (Task 4)
- Produces: `consolidate_entry(conn, turn, session, text, annotation?)` with W=2 edges + entity bridge

- [ ] **Step 1: Rewrite consolidate_entry with optional annotation**

```rust
/// Consolidate an entry by extracting atoms from annotation + rule-based types,
/// with W=2 local edges and entity bridge edges (2.5× weight).
pub fn consolidate_entry(
    conn: &Connection,
    turn: i64,
    session: &str,
    text: &str,
    annotation: Option<&MemoryAnnotation>,
) -> rusqlite::Result<()> {
    let mut all_ids: Vec<i64> = Vec::new();
    // Track which atom belongs to which entity for bridge edges
    // entity_name -> Vec<atom_id>
    let mut entity_atoms: std::collections::HashMap<String, Vec<i64>> = std::collections::HashMap::new();

    // Phase 1: Process LLM annotation (if present)
    if let Some(ann) = annotation {
        // Process phrases
        for item in &ann.phrases {
            let norm = normalize(&item.text);
            if norm.is_empty() || norm.len() < 3 { continue; }
            let id = upsert_atom(conn, &norm, turn, item.importance)?;
            record_position(conn, id, turn, session)?;
            all_ids.push(id);
        }

        // Process actions
        for item in &ann.actions {
            let norm = normalize(&item.text);
            if norm.is_empty() || norm.len() < 3 { continue; }
            let id = upsert_atom(conn, &norm, turn, item.importance)?;
            record_position(conn, id, turn, session)?;
            all_ids.push(id);
        }

        // Process entities + build entity→atoms map for bridges
        for entity in &ann.entities {
            upsert_entity(conn, entity)?;
            // Match entity name against atom phrases
            for &atom_id in &all_ids {
                // Need to check if atom phrase contains entity name
                // We store this mapping for bridge edge creation
            }
            // Store mapping for bridge pass later
            let normalized_name = normalize(&entity.name);
            entity_atoms.entry(normalized_name).or_default();
        }
    }

    // Phase 2: Rule-based extraction (always runs)
    let rule_atoms = extract_atoms(text);
    let rule_atoms: Vec<String> = rule_atoms.into_iter().filter(|a| {
        !is_stop_word(conn, a).unwrap_or(false)
    }).collect();

    for phrase in &rule_atoms {
        let id = upsert_atom(conn, phrase, turn, 1.0)?; // default importance for rules
        record_position(conn, id, turn, session)?;
        all_ids.push(id);
    }

    if all_ids.is_empty() {
        return Ok(());
    }

    // Phase 3: Entity bridge edges (2.5× weight, across distance)
    // Match entity names from annotation to atoms by substring
    if let Some(ann) = annotation {
        for entity in &ann.entities {
            let entity_lower = entity.name.to_lowercase();
            // Find which atoms in all_ids have phrases containing this entity name
            let matching_ids: Vec<i64> = all_ids.iter().copied().enumerate()
                .filter(|(_, id)| {
                    // Look up phrase for each atom id
                    get_atom_phrase(conn, *id)
                        .map(|p| p.to_lowercase().contains(&entity_lower))
                        .unwrap_or(false)
                })
                .map(|(_, id)| id)
                .collect();

            // Bridge edges: connect all pairs of matching atoms for this entity
            for i in 0..matching_ids.len() {
                for j in i + 1..matching_ids.len() {
                    let a = matching_ids[i];
                    let b = matching_ids[j];
                    // Only create bridge edge if atoms are NOT adjacent (W=2 covers those)
                    if !are_adjacent(&all_ids, a, b) {
                        increment_edge_weighted(conn, a, b, turn, 2.5)?;
                    }
                }
            }
        }
    }

    // Phase 4: W=2 local edges
    // Only connect adjacent atoms in the ordered list
    for i in 0..all_ids.len().saturating_sub(1) {
        increment_edge(conn, all_ids[i], all_ids[i + 1], turn)?;
    }

    Ok(())
}
```

- [ ] **Step 2: Add get_atom_phrase helper**

```rust
/// Look up the phrase for an atom by its ID.
fn get_atom_phrase(conn: &Connection, atom_id: i64) -> rusqlite::Result<String> {
    conn.query_row(
        "SELECT phrase FROM atoms WHERE id = ?1",
        params![atom_id],
        |row| row.get(0),
    )
}
```

- [ ] **Step 3: Add are_adjacent helper**

```rust
/// Check if two atom IDs are adjacent (W=2 distance) in the ordered list.
fn are_adjacent(ordered: &[i64], a: i64, b: i64) -> bool {
    for i in 0..ordered.len().saturating_sub(1) {
        if (ordered[i] == a && ordered[i + 1] == b)
            || (ordered[i] == b && ordered[i + 1] == a)
        {
            return true;
        }
    }
    false
}
```

- [ ] **Step 4: Add increment_edge_weighted helper**

```rust
/// Increment an edge with a custom weight multiplier (for entity bridges).
pub fn increment_edge_weighted(
    conn: &Connection,
    a: i64,
    b: i64,
    turn: i64,
    weight_multiplier: f64,
) -> rusqlite::Result<()> {
    if a == b { return Ok(()); }
    let (low, high) = if a < b { (a, b) } else { (b, a) };
    conn.execute(
        "INSERT INTO edges (atom_a, atom_b, weight, last_updated, access_count) VALUES (?1, ?2, ?3, ?4, 1)
         ON CONFLICT(atom_a, atom_b) DO UPDATE SET
            weight = weight + ?3,
            last_updated = ?4,
            access_count = access_count + 1",
        params![low, high, weight_multiplier, turn],
    )?;
    Ok(())
}
```

- [ ] **Step 5: Add upsert_entity helper**

```rust
/// Upsert an entity into the entity_registry.
fn upsert_entity(conn: &Connection, entity: &EntityAnnotation) -> rusqlite::Result<()> {
    let normalized = normalize(&entity.name);
    let aliases_json = serde_json::to_string(&entity.aliases).unwrap_or_default();
    conn.execute(
        "INSERT INTO entity_registry (name, display_name, entity_type, aliases, importance, first_seen, last_seen, ref_count)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, 1)
         ON CONFLICT(name) DO UPDATE SET
            display_name = CASE WHEN ?2 != '' THEN ?2 ELSE display_name END,
            entity_type = CASE WHEN ?3 != '' THEN ?3 ELSE entity_type END,
            aliases = ?4,
            importance = MAX(importance, ?5),
            last_seen = ?6,
            ref_count = ref_count + 1",
        params![
            normalized,
            entity.name,
            entity.typ,
            aliases_json,
            entity.importance,
            Utc::now().timestamp(),
        ],
    )?;
    Ok(())
}
```

- [ ] **Step 6: Use Utc timestamp**

Add `use chrono::Utc;` import at the top of brain.rs.

- [ ] **Step 7: Update all call sites of consolidate_entry**

```bash
grep -rn "consolidate_entry" crates/mycelium-core/src/ crates/mycelium-server/src/ --include="*.rs" | grep -v test
```

Every non-test call site needs the new `annotation: None` param:
```rust
// In brain_daemon.rs:
brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, None)?;

// In tests:
consolidate_entry(&conn, 1, "s1", "hash chain merkle tree", None)?;
```

- [ ] **Step 8: Write unit test for annotation processing**

In brain.rs `#[cfg(test)]`:

```rust
#[test]
fn test_consolidate_with_annotation() -> rusqlite::Result<()> {
    let conn = setup_test_db()?;

    let ann = MemoryAnnotation {
        phrases: vec![MemoryItem { text: "hash chain verification".into(), importance: 5.0 }],
        actions: vec![],
        entities: vec![],
    };

    consolidate_entry(&conn, 1, "test", "", Some(&ann))?;

    // Verify atom was created with importance=5
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM atoms WHERE phrase = ?1",
        params!["hash chain verification"],
        |row| row.get(0),
    )?;
    assert_eq!(count, 1, "annotation phrase atom should exist");

    Ok(())
}

/// Helper: create in-memory DB with brain tables.
fn setup_test_db() -> rusqlite::Result<Connection> {
    let conn = Connection::open_in_memory()?;
    brain::create_tables(&conn)?;
    Ok(())
}
```

- [ ] **Step 9: Run tests**

```bash
cargo test -p mycelium-core -- test_consolidate_with_annotation --nocapture 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add crates/mycelium-core/src/brain.rs
git commit -m "feat(core): rewrite consolidate_entry with W=2 edges, entity bridge, annotation support"
```

---

### Task 6: Proxy integration — inject memory instruction + extract annotation

**Files:**
- Modify: `crates/mycelium-proxy/src/interceptor.rs` (process_request, extract_assistant_response)
- Modify: `crates/mycelium-proxy/src/lib.rs` (entry storage — pass annotation through)

**Interfaces:**
- Consumes: Request/response stream
- Produces: `Entry.annotation: Option<String>` from extracted `<memory>` block

- [ ] **Step 1: Read current process_request to understand system prompt injection**

```bash
sed -n '15,70p' crates/mycelium-proxy/src/interceptor.rs
```

- [ ] **Step 2: Add memory instruction constant**

At the top of `interceptor.rs`, add:

```rust
/// Instruction injected into the system prompt to request a memory annotation.
const MEMORY_INSTRUCTION: &str = "\n\nAfter your response, emit a <memory> block containing JSON with: phrases (canonical noun phrases to remember, each with text and importance 1-5), actions (key actions taken/fixed/explained, each with text and importance 1-5), entities (named things mentioned, each with name, type, aliases, and importance 1-5). Keep the block under 200 tokens.";
```

- [ ] **Step 3: Inject instruction into system prompt**

In `process_request`, after the existing `<mycelium-facts>` block injection, append `MEMORY_INSTRUCTION`:

```rust
// After the existing facts block injection logic around line 50-56:
if let Some(system) = req.get_mut("system") {
    if let Some(s) = system.as_str() {
        *system = Value::String(format!("{}\n\n{}\n{}", s, block, MEMORY_INSTRUCTION));
    } else {
        req["system"] = Value::String(format!("{}\n{}", block, MEMORY_INSTRUCTION));
    }
}
```

- [ ] **Step 4: Also handle OpenAI-format request injection**

In `process_openai`, similarly append `MEMORY_INSTRUCTION` after the facts block. Find where system messages are built and append it.

- [ ] **Step 5: Add <memory> extraction function**

```rust
/// Extract the <memory> annotation block from an assistant response.
/// Returns (cleaned_response_text, optional_annotation_json).
pub fn extract_memory_block(response: &str) -> (String, Option<String>) {
    use regex::Regex;
    // Match <memory>...</memory> with optional newlines
    let re = Regex::new(r"(?s)<memory>(.*?)</memory>").unwrap();
    if let Some(caps) = re.captures(response) {
        let annotation = caps.get(1).map(|m| m.as_str().trim().to_string());
        let cleaned = re.replace_all(response, "").trim().to_string();
        (cleaned, annotation)
    } else {
        (response.to_string(), None)
    }
}
```

- [ ] **Step 6: Integrate memory extraction into response pipeline**

After `extract_assistant_response` gets the full text, call `extract_memory_block`:

```rust
pub fn extract_assistant_response(body: &[u8]) -> String {
    // ... existing logic to get full_text ...
    let (cleaned, _annotation) = extract_memory_block(&full_text);
    cleaned
}
```

Store the annotation in context so it can be saved with the entry. You'll need to return it:

```rust
pub fn extract_assistant_response(body: &[u8]) -> (String, Option<String>) {
    // ... existing extraction logic ...
    extract_memory_block(&full_text)
}
```

- [ ] **Step 7: Update the storage call to include annotation**

In `lib.rs`, where the proxy calls `storage.append_entry()`, pass the annotation from the extraction step:

```rust
// After extracting assistant response:
let (cleaned_response, annotation) = extract_assistant_response(&body);

// When building the Entry:
let mut entry = Entry { /* ... */ };
entry.annotation = annotation;
storage.append_entry(&entry)?;
```

- [ ] **Step 8: Verify compilation**

```bash
cargo check -p mycelium-proxy 2>&1 | tail -10
```
Expected: clean compile. Expect `regex` dependency may not exist — add to proxy's Cargo.toml if needed.

- [ ] **Step 9: Commit**

```bash
git add crates/mycelium-proxy/src/ crates/mycelium-proxy/Cargo.toml
git commit -m "feat(proxy): inject memory annotation instruction and extract <memory> blocks"
```

---

### Task 7: Daemon integration — pass annotation to brain

**Files:**
- Modify: `crates/mycelium-server/src/brain_daemon.rs`

**Interfaces:**
- Consumes: `consolidate_entry(conn, turn, session, text, annotation?)` from Task 5, `entry.annotation` from Task 2
- Produces: Passes annotation through to the brain

- [ ] **Step 1: Find the consolidate_entry call in brain_daemon.rs**

```bash
grep -n -B5 -A5 "consolidate_entry" crates/mycelium-server/src/brain_daemon.rs
```

- [ ] **Step 2: Read the entry and parse annotation**

```rust
// Before calling consolidate_entry, parse the annotation if present
let annotation: Option<MemoryAnnotation> = entry.annotation
    .as_ref()
    .and_then(|a| serde_json::from_str(a).ok());

brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, annotation.as_ref())?;
```

- [ ] **Step 3: Add the import**

Make sure `use mycelium_core::types::MemoryAnnotation;` is imported.

- [ ] **Step 4: Verify compilation**

```bash
cargo check -p mycelium-server 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add crates/mycelium-server/src/brain_daemon.rs
git commit -m "feat(daemon): pass memory annotation to consolidate_entry"
```

---

### Task 8: Update verification test for new consolidate_entry

**Files:**
- Modify: `crates/mycelium-core/tests/brain_verification.rs`
- Read: `crates/mycelium-core/tests/brain_verification.rs`

**Interfaces:**
- Consumes: `consolidate_entry(conn, turn, session, text, annotation?)`

- [ ] **Step 1: Fix the test's consolidate_entry calls**

Add `None` as the last parameter:

```rust
brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, None)?;
```

- [ ] **Step 2: Run the verification test**

```bash
cargo test -p mycelium-core --test brain_verification -- --nocapture 2>&1 | tail -20
```
Expected: FAIL (same as before — the assertion threshold issue is expected). We're just verifying it still compiles and runs.

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/tests/brain_verification.rs
git commit -m "test: update brain_verification for new consolidate_entry signature"
```

---

### Task 9: Full integration test — annotation round-trip

**Files:**
- Create: `crates/mycelium-core/tests/brain_annotation.rs`

**Interfaces:**
- Consumes: Full pipeline types from all prior tasks

- [ ] **Step 1: Create integration test**

```rust
use mycelium_core::brain;
use mycelium_core::types::{MemoryAnnotation, MemoryItem, EntityAnnotation};
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
    brain::consolidate_entry(&conn, 1, "test-session", "I fixed the hash chain bug in storage.rs", Some(&ann))?;

    // Verify: atoms were created
    let atom_count: i64 = conn.query_row("SELECT COUNT(*) FROM atoms", [], |row| row.get(0))?;
    assert!(atom_count > 0, "should create atoms from annotation + text");

    // Verify: entity was registered
    let entity_count: i64 = conn.query_row("SELECT COUNT(*) FROM entity_registry", [], |row| row.get(0))?;
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
        phrases: vec![MemoryItem { text: "storage.rs path resolution".into(), importance: 3.0 }],
        actions: vec![],
        entities: vec![
            EntityAnnotation {
                name: "storage.rs".into(),
                typ: "file".into(),
                aliases: vec![],
                importance: 3.0,
            },
        ],
    };
    brain::consolidate_entry(&conn, 2, "test-session", "the path resolution in storage.rs", Some(&ann2))?;

    // Verify: entity ref_count incremented
    let ref_count: i64 = conn.query_row(
        "SELECT ref_count FROM entity_registry WHERE name = ?1",
        params!["storage.rs"],
        |row| row.get(0),
    )?;
    assert_eq!(ref_count, 2, "storage.rs should have ref_count=2 after two mentions");

    Ok(())
}
```

- [ ] **Step 2: Run the integration test**

```bash
cargo test -p mycelium-core --test brain_annotation -- --nocapture 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/tests/brain_annotation.rs
git commit -m "test: full annotation round-trip with entity bridge and importance"
```

---

### Task 10: Replay verification — run existing entries through new pipeline

**Files:**
- Execute: the existing `brain_verification.rs` test
- Read: Results and compare against previous 188K atoms

**Note:** This task uses the existing 3,630-entry replay test with the new `consolidate_entry`, but WITHOUT an LLM annotation (since the old entries don't have one). This verifies:
1. The new code handles `annotation = None` (rule-based fallback)
2. The type-aware normalization already improves atom dedup even without annotations
3. W=2 edges drastically reduce edge count

- [ ] **Step 1: Update the test to measure atom growth with new normalization**

Modify the verification test to also print per-phase atom counts and compute new expected bounds based on type-aware normalization.

- [ ] **Step 2: Run replay**

```bash
cargo test -p mycelium-core --test brain_verification -- --nocapture 2>&1 | tail -30
```
Expected: Atom count should be significantly lower than 188K even without annotations, because type-aware normalization now collapses paths/UUIDs/hashes/numbers into single atoms.

- [ ] **Step 3: Record results as a comment in the test file**

```rust
// Replay results with type-aware normalization (2026-06-24):
// 500 entries: X atoms    Y edges
// ...
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-core/tests/brain_verification.rs
git commit -m "test: replay with type-aware normalization — measure improvement"

---

### Task 11: Update brain query functions to use importance

**Files:**
- Modify: `crates/mycelium-core/src/brain.rs` (recall, clusters, when)

**Interfaces:**
- Consumes: `atoms.importance` column from Task 3
- Produces: Query results weighted by `ref_count × importance`

- [ ] **Step 1: Update `recall` to include importance in scoring**

Change the recall query to order by `(ref_count * importance)` instead of just `ref_count`:

```rust
"SELECT id, phrase, first_seen, last_seen, ref_count, importance FROM atoms WHERE phrase LIKE ?1 ORDER BY (ref_count * importance) DESC LIMIT ?2"
```

Also update the Atom struct mapping to include importance:

```rust
|row| {
    Ok(Atom {
        id: row.get(0)?,
        phrase: row.get(1)?,
        first_seen: row.get(2)?,
        last_seen: row.get(3)?,
        ref_count: row.get(4)?,
        importance: row.get(5)?,
    })
}
```

- [ ] **Step 2: Update `clusters` to surface importance**

In `clusters`, also fetch and display importance alongside atom data in result items.

- [ ] **Step 3: Verify compilation + test recall**

```bash
cargo test -p mycelium-core -- test_recall --nocapture 2>&1 | tail -10
```
Expected: PASS. Existing tests should update to match new Atom struct.

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-core/src/brain.rs
git commit -m "feat(core): weight brain query results by ref_count × importance"
```
```
