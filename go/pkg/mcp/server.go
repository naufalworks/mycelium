// Package mcp implements a Model Context Protocol server for mycelium.
// Provides search, recall, get_context, list_entities, and get_state tools.
package mcp

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/naufalworks/mycelium/go/pkg/brain"
)

// ── JSON-RPC types ──────────────────────────────────────────────────────────

type Request struct {
	JSONRPC string `json:"jsonrpc"`
	ID      any    `json:"id"`
	Method  string `json:"method"`
	Params  any    `json:"params,omitempty"`
}

type Response struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      any         `json:"id"`
	Result  any         `json:"result,omitempty"`
	Error   *ErrorObj   `json:"error,omitempty"`
}

type ErrorObj struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

// ── Tool definitions ────────────────────────────────────────────────────────

type Tool struct {
	Name        string        `json:"name"`
	Description string        `json:"description"`
	InputSchema *InputSchema  `json:"inputSchema"`
}

type InputSchema struct {
	Type       string                    `json:"type"`
	Properties map[string]SchemaProperty `json:"properties"`
}

type SchemaProperty struct {
	Type        string   `json:"type"`
	Description string   `json:"description"`
	Default     any      `json:"default,omitempty"`
}

type ToolCallParams struct {
	Name      string         `json:"name"`
	Arguments map[string]any `json:"arguments"`
}

// ── Server ──────────────────────────────────────────────────────────────────

// Server implements the MCP protocol over stdio.
type Server struct {
	Brain   *brain.Brain
	mu      sync.Mutex
	reader  *json.Decoder
	writer  *json.Encoder
}

// New creates an MCP server.
func New(b *brain.Brain) *Server {
	return &Server{
		Brain:  b,
		reader: json.NewDecoder(io.LimitReader(noOpReader{}, 0)), // placeholder
		writer: json.NewEncoder(io.Discard),
	}
}

// ServeStdio runs the MCP server over stdin/stdout.
func (s *Server) ServeStdio() error {
	s.reader = json.NewDecoder(StdinReader{})
	s.writer = json.NewEncoder(StdoutWriter{})
	s.writer.SetEscapeHTML(false)

	log.Printf("🧬 Mycelium MCP server ready (stdio)")
	return s.serve()
}

// serve processes JSON-RPC requests.
func (s *Server) serve() error {
	for {
		var req Request
		if err := s.reader.Decode(&req); err != nil {
			if err == io.EOF {
				return nil
			}
			log.Printf("⚠️  MCP decode error: %v", err)
			continue
		}

		go s.handle(&req) // concurrent processing
	}
}

func (s *Server) handle(req *Request) {
	// Skip notifications (no ID → no response expected)
	if req.ID == nil {
		return
	}

	switch req.Method {
	case "initialize":
		s.respond(req.ID, map[string]any{
			"protocolVersion": "2024-11-05",
			"serverInfo": map[string]string{
				"name":    "mycelium-mcp",
				"version": "1.0.0",
			},
			"capabilities": map[string]any{
				"tools": map[string]any{},
			},
		})

	case "tools/list":
		s.respond(req.ID, map[string]any{
			"tools": s.tools(),
		})

	case "tools/call":
		s.handleToolCall(req)

	case "ping":
		s.respond(req.ID, map[string]any{})

	default:
		s.respond(req.ID, nil)
	}
}

func (s *Server) respond(id any, result any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	resp := Response{
		JSONRPC: "2.0",
		ID:      id,
		Result:  result,
	}
	s.writer.Encode(resp)
}

func (s *Server) respondError(id any, code int, msg string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	resp := Response{
		JSONRPC: "2.0",
		ID:      id,
		Error:   &ErrorObj{Code: code, Message: msg},
	}
	s.writer.Encode(resp)
}

// ── Tools ───────────────────────────────────────────────────────────────────

func (s *Server) tools() []Tool {
	return []Tool{
		{
			Name:        "search",
			Description: "Search mycelium permanent memory for past findings, decisions, and conversations matching a query",
			InputSchema: &InputSchema{
				Type: "object",
				Properties: map[string]SchemaProperty{
					"query": {Type: "string", Description: "Search query text"},
					"limit": {Type: "number", Description: "Max results (default 5)", Default: 5},
				},
			},
		},
		{
			Name:        "recall",
			Description: "Smart session recall — returns relevant past context for the given working context",
			InputSchema: &InputSchema{
				Type: "object",
				Properties: map[string]SchemaProperty{
					"context": {Type: "string", Description: "Current working context or question"},
					"limit":   {Type: "number", Description: "Max results (default 5)", Default: 5},
				},
			},
		},
		{
			Name:        "get_context",
			Description: "Get the current workspace context snapshot (git branch, recent errors, etc.)",
			InputSchema: &InputSchema{
				Type: "object",
				Properties: map[string]SchemaProperty{
					"session": {Type: "string", Description: "Optional session name to get context for", Default: ""},
				},
			},
		},
		{
			Name:        "list_entities",
			Description: "List mycelium entities (concepts, tools, domains tracked in memory)",
			InputSchema: &InputSchema{
				Type: "object",
				Properties: map[string]SchemaProperty{
					"entity": {Type: "string", Description: "Optional entity prefix filter", Default: ""},
				},
			},
		},
		{
			Name:        "get_state",
			Description: "Get the last preserved agent state (for crash recovery / session handoff)",
			InputSchema: &InputSchema{
				Type: "object",
				Properties: map[string]SchemaProperty{
					"session": {Type: "string", Description: "Optional session name to get state for", Default: ""},
				},
			},
		},
		{
			Name:        "store",
			Description: "Store a new entry in mycelium permanent memory for future recall across sessions",
			InputSchema: &InputSchema{
				Type: "object",
				Properties: map[string]SchemaProperty{
					"user":     {Type: "string", Description: "The user message or context to store"},
					"assistant": {Type: "string", Description: "The assistant response or finding to store"},
					"type":     {Type: "string", Description: "Entry type: talk, finding, decision, idea, dead-end, tech_verdict (default talk)"},
					"session":  {Type: "string", Description: "Optional session identifier"},
					"entities": {Type: "string", Description: "Optional comma-separated entity names for better searchability"},
				},
			},
		},
	}
}

func (s *Server) handleToolCall(req *Request) {
	params, _ := req.Params.(map[string]any)
	if params == nil {
		s.respondError(req.ID, -32602, "Invalid params")
		return
	}

	name, _ := params["name"].(string)
	args, _ := params["arguments"].(map[string]any)

	switch name {
	case "search":
		s.handleSearch(req.ID, args)
	case "recall":
		s.handleRecall(req.ID, args)
	case "get_context":
		s.handleGetContext(req.ID, args)
	case "list_entities":
		s.handleListEntities(req.ID, args)
	case "get_state":
		s.handleGetState(req.ID, args)
	case "store":
		s.handleStore(req.ID, args)
	default:
		s.respondError(req.ID, -32601, fmt.Sprintf("Unknown tool: %s", name))
	}
}

func (s *Server) handleSearch(id any, args map[string]any) {
	query, _ := args["query"].(string)
	limit := int(getFloat(args, "limit", 5))
	if query == "" {
		s.respondError(id, -32602, "Missing query")
		return
	}

	entries := s.Brain.Search(query, limit)
	results := make([]map[string]any, len(entries))
	for i, e := range entries {
		results[i] = entryToMap(e)
	}

	s.respond(id, map[string]any{
		"content": []map[string]any{
			{
				"type": "text",
				"text": formatResults(results),
			},
		},
	})
}

func (s *Server) handleRecall(id any, args map[string]any) {
	ctx, _ := args["context"].(string)
	limit := int(getFloat(args, "limit", 5))
	if ctx == "" {
		// Return recent entries
		entries := s.Brain.RecentEntries(limit)
		results := make([]map[string]any, len(entries))
		for i, e := range entries {
			results[i] = entryToMap(e)
		}
		s.respond(id, map[string]any{
			"content": []map[string]any{
				{"type": "text", "text": formatResults(results)},
			},
		})
		return
	}

	entries := s.Brain.Search(ctx, limit)
	results := make([]map[string]any, len(entries))
	for i, e := range entries {
		results[i] = entryToMap(e)
	}
	s.respond(id, map[string]any{
		"content": []map[string]any{
			{"type": "text", "text": formatResults(results)},
		},
	})
}

func (s *Server) handleGetContext(id any, args map[string]any) {
	// Collect workspace context
	session, _ := args["session"].(string)
	ctx := map[string]any{
		"timestamp":    timeNow(),
		"session":      session,
		"brain_entries": s.Brain.Count(),
		"note":         "Live workspace context — extend with git/fs watcher",
	}
	if session != "" {
		// Find recent entries for this session
		entries := s.Brain.Search(session, 3)
		ctx["related"] = len(entries)
	}

	s.respond(id, map[string]any{
		"content": []map[string]any{
			{"type": "text", "text": formatJSON(ctx)},
		},
	})
}

func (s *Server) handleListEntities(id any, args map[string]any) {
	// Extract unique entities from recent entries
	entries := s.Brain.RecentEntries(500)
	entitySet := make(map[string]int)
	for _, e := range entries {
		for _, ent := range e.Entities {
			entitySet[ent]++
		}
	}

	filter, _ := args["entity"].(string)
	var entities []map[string]any
	for name, count := range entitySet {
		if filter == "" || strings.Contains(strings.ToLower(name), strings.ToLower(filter)) {
			entities = append(entities, map[string]any{
				"name":  name,
				"count": count,
			})
		}
	}

	s.respond(id, map[string]any{
		"content": []map[string]any{
			{"type": "text", "text": formatEntityResults(entities)},
		},
	})
}

func (s *Server) handleGetState(id any, args map[string]any) {
	// Find most recent state_snapshot entry
	entries := s.Brain.RecentEntries(100)
	var stateEntry *brain.Entry
	for _, e := range entries {
		if e.Type == "state_snapshot" {
			stateEntry = e
			break
		}
	}

	if stateEntry == nil {
		s.respond(id, map[string]any{
			"content": []map[string]any{
				{"type": "text", "text": "No preserved agent state found.\n\nTo preserve state: use `mycelium state save` in your current session."},
			},
		})
		return
	}

	s.respond(id, map[string]any{
		"content": []map[string]any{
			{"type": "text", "text": fmt.Sprintf(
				"## Last Agent State (Turn %d)\n\n**Session:** %s\n**User:** %s\n**Assistant:** %s",
				stateEntry.Turn, stateEntry.Session, stateEntry.User, stateEntry.Assistant,
			)},
		},
	})
}

// ── Store tool ──────────────────────────────────────────────────────────────

func (s *Server) handleStore(id any, args map[string]any) {
	user, _ := args["user"].(string)
	assistant, _ := args["assistant"].(string)
	entryType, _ := args["type"].(string)
	session, _ := args["session"].(string)
	entitiesStr, _ := args["entities"].(string)

	if user == "" && assistant == "" {
		s.respondError(id, -32602, "Must provide user or assistant text")
		return
	}
	if entryType == "" {
		entryType = "talk"
	}

	var entities []string
	if entitiesStr != "" {
		for _, e := range strings.Split(entitiesStr, ",") {
			e = strings.TrimSpace(e)
			if e != "" {
				entities = append(entities, e)
			}
		}
	}

	// Auto-extract entities from text (simple word extraction)
	if len(entities) == 0 {
		words := extractKeywords(user + " " + assistant)
		entities = words
	}

	entry := &brain.Entry{
		Type:      entryType,
		Session:   session,
		User:      user[:min(len(user), 500)],
		Assistant: assistant[:min(len(assistant), 2000)],
		Entities:  entities,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}

	saved, err := s.Brain.Append(entry)
	if err != nil {
		s.respondError(id, -32603, fmt.Sprintf("Failed to store: %v", err))
		return
	}

	s.respond(id, map[string]any{
		"content": []map[string]any{
			{
				"type": "text",
				"text": fmt.Sprintf("Stored turn %d in mycelium [type=%s, session=%s]", saved.Turn, entryType, session),
			},
		},
	})
}

func extractKeywords(text string) []string {
	text = strings.ToLower(text)
	// Common stop words to skip
	stopWords := map[string]bool{
		"the": true, "a": true, "an": true, "is": true, "are": true, "was": true,
		"were": true, "be": true, "been": true, "being": true, "have": true,
		"has": true, "had": true, "do": true, "does": true, "did": true,
		"will": true, "would": true, "could": true, "should": true, "may": true,
		"might": true, "shall": true, "can": true, "need": true, "dare": true,
		"to": true, "of": true, "in": true, "for": true, "on": true, "with": true,
		"at": true, "by": true, "from": true, "and": true, "or": true, "but": true,
		"not": true, "this": true, "that": true, "it": true, "its": true, "you": true,
		"i": true, "me": true, "my": true, "we": true, "our": true, "us": true,
		"they": true, "them": true, "their": true, "he": true, "she": true, "him": true,
		"her": true, "his": true, "what": true, "which": true, "who": true, "how": true,
		"when": true, "where": true, "why": true, "ok": true, "yes": true, "no": true,
		"please": true, "thanks": true, "thank": true, "hi": true, "hello": true,
		"hey": true, "so": true, "just": true, "like": true, "also": true, "very": true,
		"really": true, "well": true, "then": true, "there": true, "here": true,
		"about": true, "into": true, "over": true, "after": true, "before": true,
		"if": true, "as": true, "because": true, "up": true,
		"down": true, "out": true, "off": true, "all": true, "each": true, "every": true,
		"some": true, "any": true, "more": true, "most": true, "other": true, "such": true,
		"only": true, "own": true, "same": true, "than": true, "too": true,
	}

	seen := make(map[string]bool)
	var keywords []string
	for _, word := range strings.Fields(text) {
		// Remove punctuation
		word = strings.Trim(word, ".,!?;:\"'()[]{}/<>-")
		if len(word) < 3 || stopWords[word] || seen[word] {
			continue
		}
		seen[word] = true
		keywords = append(keywords, word)
	}
	if len(keywords) > 10 {
		keywords = keywords[:10]
	}
	return keywords
}

// ── Formatting helpers ──────────────────────────────────────────────────────

func entryToMap(e *brain.Entry) map[string]any {
	return map[string]any{
		"turn":      e.Turn,
		"tier":      e.Tier,
		"type":      e.Type,
		"session":   e.Session,
		"ts":        e.Timestamp,
		"entities":  e.Entities,
		"user":      truncate(e.User, 200),
		"assistant": truncate(e.Assistant, 500),
	}
}

func formatResults(results []map[string]any) string {
	if len(results) == 0 {
		return "No matching entries found in mycelium memory."
	}
	var b strings.Builder
	for i, r := range results {
		b.WriteString(fmt.Sprintf("### %d. Turn %d [Tier %s] %s\n", i+1, r["turn"], r["tier"], r["ts"]))
		b.WriteString(fmt.Sprintf("**Session:** %s  \n", r["session"]))
		b.WriteString(fmt.Sprintf("**User:** %s  \n", r["user"]))
		b.WriteString(fmt.Sprintf("**Assistant:** %s  \n", truncate(r["assistant"].(string), 300)))
		if entities, ok := r["entities"].([]string); ok && len(entities) > 0 {
			b.WriteString(fmt.Sprintf("**Entities:** %s  \n", strings.Join(entities, ", ")))
		}
		b.WriteString("\n")
	}
	return b.String()
}

func formatEntityResults(entities []map[string]any) string {
	if len(entities) == 0 {
		return "No entities found in recent memory."
	}
	var b strings.Builder
	b.WriteString("## Mycelium Entities\n\n")
	b.WriteString("| Entity | Occurrences |\n|--------|-------------|\n")
	for _, e := range entities {
		b.WriteString(fmt.Sprintf("| %s | %d |\n", e["name"], e["count"]))
	}
	return b.String()
}

func formatJSON(v any) string {
	b, _ := json.MarshalIndent(v, "", "  ")
	return string(b)
}

// ── I/O wrappers ────────────────────────────────────────────────────────────

type StdinReader struct{}
func (StdinReader) Read(p []byte) (int, error) { return os.Stdin.Read(p) }

type StdoutWriter struct{}
func (StdoutWriter) Write(p []byte) (int, error) { return os.Stdout.Write(p) }

type noOpReader struct{}
func (noOpReader) Read(p []byte) (int, error) { return 0, io.EOF }

// ── Utilities ───────────────────────────────────────────────────────────────

func getFloat(m map[string]any, key string, def float64) float64 {
	if v, ok := m[key].(float64); ok {
		return v
	}
	return def
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

// timeNow is overridable for testing
var timeNow = func() string { return time.Now().UTC().Format(time.RFC3339) }
func init() {}
