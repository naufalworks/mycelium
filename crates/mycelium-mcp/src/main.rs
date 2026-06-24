//! Mycelium MCP Server — Model Context Protocol implementation.
//!
//! Communicates over stdio using JSON-RPC. Provides tools for Claude
//! to access mycelium's permanent memory: search, recall, facts,
//! artifacts, brain state, entity listing, and state persistence.

use mycelium_core::{Artifact, MyceliumConfig, Storage};
use serde_json::Value;
use std::io::{self, BufRead, Write};
use std::sync::Mutex;
use tracing::info;
use uuid::Uuid;

struct App {
    storage: Storage,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config = MyceliumConfig::default();
    let db_path = config.root_dir.join("mycelium.db");
    let storage = Storage::open(db_path)?;

    info!("Mycelium MCP server started (brain: {} entries)", storage.count_entries().unwrap_or(0));

    let app = Mutex::new(App { storage });

    let stdin = io::stdin();
    let stdout = io::stdout();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(e) => {
                eprintln!("stdin read error: {}", e);
                break;
            }
        };

        if line.trim().is_empty() {
            continue;
        }

        let req: Value = match serde_json::from_str(&line) {
            Ok(r) => r,
            Err(_) => continue,
        };

        let id = req.get("id");
        let method = req.get("method").and_then(|v| v.as_str()).unwrap_or("");

        let response = match method {
            "initialize" => handle_initialize(id),
            "tools/list" => handle_tools_list(id),
            "tools/call" => handle_tool_call(id, &req, &app),
            "ping" => handle_ping(id),
            _ => make_error(id, -32601, format!("Method not found: {}", method)),
        };

        let json = serde_json::to_string(&response).unwrap_or_default();
        let mut out = stdout.lock();
        writeln!(out, "{}", json).ok();
        out.flush().ok();
    }

    Ok(())
}

fn make_response(id: Option<&Value>, result: Value) -> Value {
    serde_json::json!({"jsonrpc": "2.0", "id": id, "result": result})
}

fn make_error(id: Option<&Value>, code: i64, msg: String) -> Value {
    serde_json::json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": msg}})
}

fn handle_initialize(id: Option<&Value>) -> Value {
    make_response(id, serde_json::json!({
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "mycelium-mcp", "version": "0.1.0"}
    }))
}

fn handle_ping(id: Option<&Value>) -> Value {
    make_response(id, serde_json::json!({}))
}

fn handle_tools_list(id: Option<&Value>) -> Value {
    make_response(id, serde_json::json!({
        "tools": [
            {
                "name": "search",
                "description": "Search memory entries by keyword",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query text"},
                        "limit": {"type": "number", "description": "Max results (default 5)", "default": 5}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "recall",
                "description": "Search memory facts by keyword",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query text"},
                        "limit": {"type": "number", "description": "Max results (default 5)", "default": 5}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "get_context",
                "description": "Get conversation context for a session",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string", "description": "Session name to get context for"},
                        "limit": {"type": "number", "description": "Max entries (default 10)", "default": 10}
                    },
                    "required": ["session"]
                }
            },
            {
                "name": "brain_status",
                "description": "Get brain status (entry count, sessions, DB size)",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "list_entities",
                "description": "List entities found in memory entries",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Optional prefix filter"}
                    }
                }
            },
            {
                "name": "store",
                "description": "Store a new entry in permanent memory",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "user": {"type": "string", "description": "User message content"},
                        "assistant": {"type": "string", "description": "Assistant response content"},
                        "type": {"type": "string", "description": "Entry type: talk, finding, decision, idea, dead-end (default talk)"},
                        "session": {"type": "string", "description": "Optional session identifier"},
                        "entities": {"type": "string", "description": "Optional comma-separated entity names"}
                    }
                }
            },
            {
                "name": "get_state",
                "description": "Get the last preserved agent state for a session",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session": {"type": "string", "description": "Session name to get state for"}
                    }
                }
            },
            {
                "name": "artifact_get",
                "description": "Retrieve a stored artifact by its ID",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Artifact UUID"}
                    },
                    "required": ["id"]
                }
            },
            {
                "name": "artifact_query",
                "description": "Run a SQL SELECT query over stored artifacts",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SELECT query over artifacts table"}
                    },
                    "required": ["sql"]
                }
            },
            {
                "name": "artifact_ls",
                "description": "List stored artifacts, optionally filtered by type",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "Filter by artifact type"},
                        "limit": {"type": "number", "description": "Max results (default 20)", "default": 20}
                    }
                }
            },
            {
                "name": "brain_recall",
                "description": "Search the Hebbian Crystal Brain for atom positions matching a phrase. Returns all occurrences across all sessions, sorted by recency.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "phrase": {"type": "string", "description": "Search phrase (prefix match)"},
                        "limit": {"type": "number", "description": "Max results (default 20)", "default": 20}
                    },
                    "required": ["phrase"]
                }
            },
            {
                "name": "brain_clusters",
                "description": "Find top-N Hebbian neighbors for a phrase — atoms that co-occur most frequently with the query in the same entries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "phrase": {"type": "string", "description": "Query phrase"},
                        "limit": {"type": "number", "description": "Max neighbors (default 10)", "default": 10}
                    },
                    "required": ["phrase"]
                }
            },
            {
                "name": "brain_when",
                "description": "Get first_seen, last_seen, and occurrence count for a phrase across all of permanent memory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "phrase": {"type": "string", "description": "Query phrase"}
                    },
                    "required": ["phrase"]
                }
            },
            {
                "name": "brain_status",
                "description": "Get brain statistics: atom count, position count, edge count, pending queue depth.",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ]
    }))
}

fn handle_tool_call(id: Option<&Value>, req: &Value, app: &Mutex<App>) -> Value {
    let name = req.pointer("/params/name").and_then(|v| v.as_str()).unwrap_or("");
    let args = req.pointer("/params/arguments").and_then(|v| v.as_object()).cloned().unwrap_or_default();

    let result = match name {
        "search" => {
            let query = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(5) as i64;
            if query.is_empty() {
                return make_error(id, -32602, "Missing query".into());
            }
            let guard = app.lock().unwrap();
            let entries = guard.storage.search_fts(query, limit).unwrap_or_default();
            let text: Vec<String> = entries.iter().map(|e| {
                format!("[#{}] ({}) {}: {}",
                    e.turn, e.tier.as_str(), e.session,
                    e.user.chars().take(200).collect::<String>())
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n\n")}]})
        }
        "recall" => {
            let query = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(5) as i64;
            if query.is_empty() {
                return make_error(id, -32602, "Missing query".into());
            }
            let guard = app.lock().unwrap();
            let facts = guard.storage.search_facts(query, limit).unwrap_or_default();
            let text: Vec<String> = facts.iter().map(|f| {
                format!("{} → {}: {} (confidence: {})", f.entity, f.attribute, f.value, f.confidence)
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
        }
        "get_context" => {
            let session = args.get("session").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(10) as i64;
            if session.is_empty() {
                return make_error(id, -32602, "Missing session".into());
            }
            let guard = app.lock().unwrap();
            let entries = guard.storage.entries_for_session(session, limit).unwrap_or_default();
            let text: Vec<String> = entries.iter().map(|e| {
                format!("[#{}] {}: {}",
                    e.turn, e.tier.as_str(),
                    e.user.chars().take(200).collect::<String>())
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n\n")}]})
        }
        "brain_status" => {
            let guard = app.lock().unwrap();
            let conn = guard.storage.conn().lock().unwrap();
            match mycelium_core::brain::brain_status(&*conn) {
                Ok(st) => serde_json::json!({"content": [{"type": "text", "text":
                    format!("Atoms: {}\nPositions: {}\nEdges: {}\nPending: {}",
                        st.atom_count, st.position_count, st.edge_count, st.pending_count)
                }]}),
                Err(e) => return make_error(id, -32603, format!("Brain error: {}", e)),
            }
        }
        "list_entities" => {
            let filter = args.get("filter").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
            let guard = app.lock().unwrap();
            let entries = guard.storage.all_entries().unwrap_or_default();
            let mut entity_set: std::collections::BTreeMap<String, i64> = std::collections::BTreeMap::new();
            for entry in &entries {
                for entity in &entry.entities {
                    let name = entity.to_lowercase();
                    if filter.is_empty() || name.contains(&filter) {
                        *entity_set.entry(entity.clone()).or_insert(0) += 1;
                    }
                }
            }
            let text: Vec<String> = entity_set.iter()
                .map(|(name, count)| format!("{} ({})", name, count))
                .collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
        }
        "store" => {
            let user = args.get("user").and_then(|v| v.as_str()).unwrap_or("");
            let assistant = args.get("assistant").and_then(|v| v.as_str()).unwrap_or("");
            let entry_type = args.get("type").and_then(|v| v.as_str()).unwrap_or("talk");
            let session = args.get("session").and_then(|v| v.as_str()).unwrap_or("mcp-store");
            let entities_str = args.get("entities").and_then(|v| v.as_str()).unwrap_or("");

            if user.is_empty() && assistant.is_empty() {
                return make_error(id, -32602, "Must provide user or assistant text".into());
            }

            let mut entities: Vec<String> = if entities_str.is_empty() {
                let text = format!("{} {}", user, assistant);
                text.split_whitespace()
                    .filter(|w| w.len() > 3 && w.chars().all(|c| c.is_alphanumeric()))
                    .map(|w| w.to_string())
                    .collect()
            } else {
                entities_str.split(',')
                    .map(|e| e.trim().to_string())
                    .filter(|e| !e.is_empty())
                    .collect()
            };
            entities.dedup();
            entities.truncate(10);

            let entry_type = match entry_type.to_lowercase().as_str() {
                "fact" | "decision" => mycelium_core::EntryType::Fact,
                "finding" => mycelium_core::EntryType::Finding,
                "system" => mycelium_core::EntryType::System,
                _ => mycelium_core::EntryType::Conversation,
            };

            let entry = mycelium_core::Entry {
                turn: 0,
                tier: mycelium_core::Tier::Ephemeral,
                entry_type,
                session: session.to_string(),
                ts: chrono::Utc::now(),
                user: user.to_string(),
                assistant: assistant.to_string(),
                entities,
                prev_hash: String::new(),
                hash: String::new(),
                finding: None,
                verdict: None,
            };

            let guard = app.lock().unwrap();
            match guard.storage.append_entry(&entry) {
                Ok(saved) => serde_json::json!({"content": [{"type": "text", "text":
                    format!("Stored entry #{}", saved.turn)
                }]}),
                Err(e) => return make_error(id, -32603, format!("Store failed: {}", e)),
            }
        }
        "get_state" => {
            let session = args.get("session").and_then(|v| v.as_str()).unwrap_or("");
            let guard = app.lock().unwrap();
            let entries = if session.is_empty() {
                let mut all = guard.storage.all_entries().unwrap_or_default();
                all.reverse();
                all
            } else {
                guard.storage.entries_for_session(session, 1).unwrap_or_default()
            };
            if let Some(state) = entries.first() {
                serde_json::json!({"content": [{"type": "text", "text":
                    format!("## Last Agent State (Turn {})\n\n**Session:** {}\n**User:** {}\n**Assistant:** {}",
                        state.turn, state.session,
                        state.user.chars().take(200).collect::<String>(),
                        state.assistant.chars().take(200).collect::<String>())
                }]})
            } else {
                serde_json::json!({"content": [{"type": "text", "text": "No preserved agent state found."}]})
            }
        }
        "artifact_get" => {
            let id_str = args.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let uuid: Uuid = match id_str.parse() {
                Ok(u) => u,
                Err(_) => return make_error(id, -32602, format!("Invalid artifact ID: {}", id_str)),
            };
            let guard = app.lock().unwrap();
            match guard.storage.get_artifact(&uuid) {
                Ok(Some(artifact)) => serde_json::json!({"content": [{"type": "text", "text":
                    format!("ID: {}\nSession: {}\nType: {}\nFilename: {}\nContent-Type: {}\nDescription: {}\nCreated: {}\n\nContent: {}",
                        artifact.id, artifact.session, artifact.artifact_type, artifact.filename,
                        artifact.content_type,
                        artifact.description.as_deref().unwrap_or(""),
                        artifact.created_at,
                        String::from_utf8_lossy(&artifact.content).chars().take(500).collect::<String>())
                }]}),
                Ok(None) => serde_json::json!({"content": [{"type": "text", "text": format!("Artifact not found: {}", id_str)}]}),
                Err(e) => return make_error(id, -32603, format!("Error: {}", e)),
            }
        }
        "artifact_query" => {
            let sql = args.get("sql").and_then(|v| v.as_str()).unwrap_or("");
            if sql.is_empty() {
                return make_error(id, -32602, "Missing SQL query".into());
            }
            let guard = app.lock().unwrap();
            match guard.storage.query_artifacts(sql) {
                Ok(rows) => {
                    let text = serde_json::to_string_pretty(&rows).unwrap_or_default();
                    serde_json::json!({"content": [{"type": "text", "text": text}]})
                }
                Err(e) => return make_error(id, -32603, format!("Query error: {}", e)),
            }
        }
        "artifact_ls" => {
            let filter_type = args.get("type").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(20) as i64;
            let guard = app.lock().unwrap();
            let all = guard.storage.list_artifacts("").unwrap_or_default();
            let filtered: Vec<&Artifact> = if filter_type.is_empty() {
                all.iter().collect()
            } else {
                all.iter().filter(|a| a.artifact_type == filter_type).collect()
            };
            let text: Vec<String> = filtered.iter().take(limit as usize).map(|a| {
                format!("{} | {} | {} | {} | {}",
                    a.id, a.artifact_type, a.filename, a.content_type,
                    a.created_at.format("%Y-%m-%d %H:%M"))
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
        }
        "brain_recall" => {
            let phrase = args.get("phrase").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(20) as i64;
            if phrase.is_empty() {
                return make_error(id, -32602, "Missing phrase".into());
            }
            let guard = app.lock().unwrap();
            let conn = guard.storage.conn().lock().unwrap();
            match mycelium_core::brain::recall(&*conn, phrase, limit) {
                Ok(atoms) => {
                    let text: Vec<String> = atoms.iter().map(|a| {
                        format!("{} | first: turn {} | last: turn {} | seen: {} times",
                            a.phrase, a.first_seen, a.last_seen, a.ref_count)
                    }).collect();
                    serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
                }
                Err(e) => return make_error(id, -32603, format!("Brain error: {}", e)),
            }
        }
        "brain_clusters" => {
            let phrase = args.get("phrase").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(10) as i64;
            if phrase.is_empty() {
                return make_error(id, -32602, "Missing phrase".into());
            }
            let guard = app.lock().unwrap();
            let conn = guard.storage.conn().lock().unwrap();
            match mycelium_core::brain::clusters(&*conn, phrase, limit) {
                Ok(neighbors) => {
                    if neighbors.is_empty() {
                        serde_json::json!({"content": [{"type": "text", "text": format!("No neighbors found for '{}'", phrase)}]})
                    } else {
                        let text: Vec<String> = neighbors.iter().map(|(phrase, weight)| {
                            format!("{} (weight: {})", phrase, weight)
                        }).collect();
                        serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
                    }
                }
                Err(e) => return make_error(id, -32603, format!("Brain error: {}", e)),
            }
        }
        "brain_when" => {
            let phrase = args.get("phrase").and_then(|v| v.as_str()).unwrap_or("");
            if phrase.is_empty() {
                return make_error(id, -32602, "Missing phrase".into());
            }
            let guard = app.lock().unwrap();
            let conn = guard.storage.conn().lock().unwrap();
            match mycelium_core::brain::when(&*conn, phrase) {
                Ok(Some((first, last, count))) => serde_json::json!({"content": [{"type": "text", "text":
                    format!("Phrase: {}\nFirst seen: turn {}\nLast seen: turn {}\nTimes seen: {}",
                        phrase, first, last, count)
                }]}),
                Ok(None) => serde_json::json!({"content": [{"type": "text", "text": format!("Phrase '{}' not found in brain", phrase)}]}),
                Err(e) => return make_error(id, -32603, format!("Brain error: {}", e)),
            }
        }
        _ => {
            serde_json::json!({"content": [{"type": "text", "text": format!("Unknown tool: {}", name)}]})
        }
    };

    make_response(id, result)
}
