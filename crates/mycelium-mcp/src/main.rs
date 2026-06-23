//! Mycelium MCP Server — Model Context Protocol implementation.
//!
//! Communicates over stdio using JSON-RPC. Provides tools for Claude
//! Desktop to access mycelium's permanent memory: search, recall,
//! facts, artifacts, and brain state.

use mycelium_core::{MyceliumConfig, Storage};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::{self, BufRead, Write};
use std::sync::Mutex;
use tracing::{error, info};

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
    let app = Mutex::new(App { storage });

    info!("Mycelium MCP server started (stdio)");

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut reader = stdin.lock();
    let mut line = String::new();

    loop {
        line.clear();
        if reader.read_line(&mut line).is_err() || line.is_empty() {
            break; // EOF
        }

        let req: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                error!("Failed to parse request: {}", e);
                continue;
            }
        };

        let id = req.get("id");
        let method = req.get("method").and_then(|v| v.as_str()).unwrap_or("");

        let response = match method {
            "initialize" => handle_initialize(id),
            "tools/list" => handle_tools_list(id, &app),
            "tools/call" => handle_tool_call(id, &req, &app),
            "resources/list" => handle_resources_list(id),
            _ => {
                let mut resp = serde_json::json!({"jsonrpc": "2.0", "error": {"code": -32601, "message": format!("Method not found: {}", method)}});
                if let Some(id) = id { resp["id"] = id.clone(); }
                resp
            }
        };

        let output = serde_json::to_string(&response).unwrap();
        let mut out = stdout.lock();
        writeln!(out, "{}", output).ok();
        out.flush().ok();
    }

    Ok(())
}

fn make_response(id: Option<&Value>, result: Value) -> Value {
    let mut resp = serde_json::json!({"jsonrpc": "2.0", "result": result});
    if let Some(id) = id { resp["id"] = id.clone(); }
    resp
}

fn handle_initialize(id: Option<&Value>) -> Value {
    make_response(id, serde_json::json!({
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {
                "listChanged": false
            }
        },
        "serverInfo": {
            "name": "mycelium-mcp",
            "version": "0.1.0"
        }
    }))
}

fn handle_resources_list(id: Option<&Value>) -> Value {
    make_response(id, serde_json::json!({"resources": []}))
}

fn handle_tools_list(id: Option<&Value>, _app: &Mutex<App>) -> Value {
    make_response(id, serde_json::json!({
        "tools": [
            {
                "name": "search",
                "description": "Search memory entries by keyword",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
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
                        "query": {"type": "string", "description": "Search query"}
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
                        "session": {"type": "string", "description": "Session name"}
                    },
                    "required": ["session"]
                }
            },
            {
                "name": "brain_status",
                "description": "Get brain status (entry count, sessions, DB size)",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "list_entities",
                "description": "List entities from memory entries",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "number", "description": "Max results"}
                    }
                }
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
            let app = app.lock().unwrap();
            let entries = app.storage.search_fts(query, 20).unwrap_or_default();
            let text: Vec<String> = entries.iter().map(|e| {
                format!("[#{}] {}: {}", e.turn, e.session, e.user.chars().take(200).collect::<String>())
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
        }
        "recall" => {
            let query = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
            let app = app.lock().unwrap();
            let facts = app.storage.search_facts(query, 20).unwrap_or_default();
            let text: Vec<String> = facts.iter().map(|f| {
                format!("[{}] {}.{} = {}", f.fact_type, f.entity, f.attribute, f.value.chars().take(200).collect::<String>())
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
        }
        "get_context" => {
            let session = args.get("session").and_then(|v| v.as_str()).unwrap_or("");
            let app = app.lock().unwrap();
            let entries = app.storage.entries_for_session(session, 10).unwrap_or_default();
            let text: Vec<String> = entries.iter().map(|e| {
                format!("[#{}] {}: {}", e.turn, e.session, e.user.chars().take(200).collect::<String>())
            }).collect();
            serde_json::json!({"content": [{"type": "text", "text": text.join("\n")}]})
        }
        "brain_status" => {
            let app = app.lock().unwrap();
            let count = app.storage.count_entries().unwrap_or(0);
            let sessions = app.storage.count_sessions().unwrap_or(0);
            let db_size = app.storage.db_size().unwrap_or(0);
            serde_json::json!({"content": [{"type": "text", "text":
                format!("Entries: {}\nSessions: {}\nDB: {} KB", count, sessions, db_size / 1024)
            }]})
        }
        "list_entities" => {
            serde_json::json!({"content": [{"type": "text", "text": "Entity listing coming soon"}]})
        }
        _ => {
            serde_json::json!({"content": [{"type": "text", "text": format!("Unknown tool: {}", name)}]})
        }
    };

    make_response(id, result)
}
