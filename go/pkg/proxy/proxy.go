// Package proxy provides an HTTP reverse proxy that intercepts Anthropic API calls,
// logs conversations to mycelium, and injects relevant past context.
package proxy

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/naufalworks/mycelium/go/pkg/brain"
)

const (
	DefaultPort     = "8443"
	DefaultUpstream = "https://api.anthropic.com"
	UpstreamHost    = "api.anthropic.com"
)

// Proxy intercepts Claude Code ↔ Anthropic API traffic.
type Proxy struct {
	Brain         *brain.Brain
	Upstream      string
	Port          string
	server        *http.Server
	sessionLoaded map[string]bool // tracks which sessions got initial context (A1)
	injectedTurns map[int]bool    // tracks turn IDs already injected to avoid bloat (A2)
	mu            sync.Mutex      // protects sessionLoaded and injectedTurns
}

// New creates a new mycelium proxy.
func New(b *brain.Brain) *Proxy {
	return &Proxy{
		Brain:         b,
		Upstream:      DefaultUpstream,
		Port:          DefaultPort,
		sessionLoaded: make(map[string]bool),
		injectedTurns: make(map[int]bool),
	}
}

// Start begins listening on the configured port.
func (p *Proxy) Start() error {
	p.server = &http.Server{
		Addr:    fmt.Sprintf("127.0.0.1:%s", p.Port),
		Handler: http.HandlerFunc(p.handleRequest),
	}
	log.Printf("🧬 Mycelium proxy listening on 127.0.0.1:%s → %s", p.Port, p.Upstream)
	return p.server.ListenAndServe()
}

// Stop gracefully shuts down the proxy.
func (p *Proxy) Stop() error {
	if p.server != nil {
		return p.server.Close()
	}
	return nil
}

// handleRequest is the main HTTP handler. It acts as a transparent proxy.
func (p *Proxy) handleRequest(w http.ResponseWriter, r *http.Request) {
	// Intercept ALL API calls — log everything that looks like a chat request
	// This works with any backend (Anthropic, meshgate, OpenAI-compatible, etc.)
	log.Printf("➡️  %s %s", r.Method, r.URL.Path)

	// Read and parse the request body
	body, err := io.ReadAll(r.Body)
	r.Body.Close()
	if err != nil {
		http.Error(w, fmt.Sprintf("read body: %v", err), http.StatusBadRequest)
		return
	}

	var msgReq map[string]any
	if err := json.Unmarshal(body, &msgReq); err != nil {
		p.passthroughWithBody(w, r, body)
		return
	}

	// Extract user message (last user turn)
	userMsg := extractUserMessage(msgReq)

	// Skip internal noise (suggestion mode, system reminders)
	if userMsg == "" || strings.HasPrefix(userMsg, "[SUGGESTION MODE:") || strings.HasPrefix(userMsg, "<system-reminder>") {
		p.passthroughWithBody(w, r, body)
		return
	}

	// Extract session identifier (reused for A1 and logging)
	session := extractSession(msgReq)

	// ── A1: Session-start context loader ────────────────────────────────────
	// For the first request in a session, load recent entries as initial context.
	// This ensures even the first message benefits from past knowledge.
	p.mu.Lock()
	sessionInitialized := p.sessionLoaded[session]
	p.mu.Unlock()

	if !sessionInitialized {
		recentEntries := p.Brain.RecentEntries(10)
		if len(recentEntries) > 0 {
			msgReq = injectContext(msgReq, recentEntries)
		}
		p.mu.Lock()
		p.sessionLoaded[session] = true
		for _, e := range recentEntries {
			p.injectedTurns[e.Turn] = true
		}
		p.mu.Unlock()
	}

	// Query past context from mycelium
	var contextEntries []*brain.Entry
	if userMsg != "" {
		contextEntries = p.Brain.Search(userMsg, 3)
	}

	// ── A2: Dedup injected context ──────────────────────────────────────────
	// Filter out entries already injected (from session-start or prior turns)
	// to avoid prompt bloat.
	p.mu.Lock()
	var filteredContext []*brain.Entry
	for _, e := range contextEntries {
		if !p.injectedTurns[e.Turn] {
			filteredContext = append(filteredContext, e)
		}
	}
	p.mu.Unlock()

	// Inject context into system prompt
	msgReq = injectContext(msgReq, filteredContext)

	// Anti-Memory: inject verified memory facts alongside brain context.
	// Reads directly from SQLite — no HTTP, under 1ms.
	if len(userMsg) > 10 {
		facts := p.Brain.FetchMemoryFacts(userMsg, 5)
		if len(facts) > 0 {
			msgReq = injectMemoryFacts(msgReq, facts)
		}
	}

	// Track injected entries for future dedup
	p.mu.Lock()
	for _, e := range filteredContext {
		p.injectedTurns[e.Turn] = true
	}
	p.mu.Unlock()

	// Marshal modified request
	modifiedBody, err := json.Marshal(msgReq)
	if err != nil {
		p.passthroughWithBody(w, r, body)
		return
	}

	// Forward the request and capture the response
	respBody, assistantMsg := p.forwardAndCapture(r, modifiedBody)

	// Log to mycelium
	if userMsg != "" && assistantMsg != "" {
		p.logConversation(userMsg, assistantMsg, msgReq, contextEntries)

		// Hippocampus: real-time fact extraction after every exchange.
		// Non-blocking — the response is never delayed.
		go p.hippocampusExtract(userMsg, assistantMsg, session)
	}

	// Return the response
	w.Write(respBody)
}

// passthrough forwards a request without interception.
func (p *Proxy) passthrough(w http.ResponseWriter, r *http.Request) {
	proxy := httputil.ReverseProxy{
		Director: func(req *http.Request) {
			p.directRequest(req, r)
		},
	}
	proxy.ServeHTTP(w, r)
}

// passthroughWithBody forwards a request with a replaced body.
func (p *Proxy) passthroughWithBody(w http.ResponseWriter, r *http.Request, body []byte) {
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = int64(len(body))
	p.passthrough(w, r)
}

// forwardAndCapture forwards the request and captures the full response.
// Returns the raw response body and the extracted assistant message text.
func (p *Proxy) forwardAndCapture(r *http.Request, body []byte) ([]byte, string) {
	req, err := http.NewRequest(r.Method, p.Upstream+r.URL.Path, bytes.NewReader(body))
	if err != nil {
		return body, ""
	}
	p.directRequest(req, r)

	// Copy headers
	for k, v := range r.Header {
		if k != "Host" {
			req.Header[k] = v
		}
	}

	// Determine if streaming
	msgReq := make(map[string]any)
	json.Unmarshal(body, &msgReq)
	isStream := false
	if s, ok := msgReq["stream"]; ok {
		isStream = s.(bool)
	}

	client := &http.Client{Timeout: 300 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return body, ""
	}
	defer resp.Body.Close()

	if isStream {
		return p.handleStreaming(resp)
	}

	// Non-streaming: read full response
	respBody, _ := io.ReadAll(resp.Body)
	var msgResp map[string]any
	if json.Unmarshal(respBody, &msgResp) == nil {
		assistantMsg := extractAssistantResponse(msgResp)
		return respBody, assistantMsg
	}
	return respBody, ""
}

// handleStreaming processes SSE responses, reconstructing the full text.
// Supports Anthropic format and generic OpenAI-compatible format.
func (p *Proxy) handleStreaming(resp *http.Response) ([]byte, string) {
	var fullText strings.Builder
	var respBody bytes.Buffer

	// Copy headers to client first
	// Then stream the body through while capturing

	// Use io.TeeReader to capture and forward simultaneously
	tee := io.TeeReader(resp.Body, &respBody)

	// Try to extract text from SSE events
	scanner := bufio.NewScanner(tee)
	for scanner.Scan() {
		line := scanner.Text()

		if strings.HasPrefix(line, "data: ") {
			data := strings.TrimPrefix(line, "data: ")
			if data == "[DONE]" || data == "" {
				continue
			}

			var event map[string]any
			if err := json.Unmarshal([]byte(data), &event); err != nil {
				continue
			}

			// Anthropic format: content_block_delta → delta.text
			if event["type"] == "content_block_delta" {
				if delta, ok := event["delta"].(map[string]any); ok {
					if text, ok := delta["text"].(string); ok {
						fullText.WriteString(text)
					}
				}
			}

			// OpenAI-compatible format (used by some gateways): choices[].delta.content
			if choices, ok := event["choices"].([]any); ok {
				for _, c := range choices {
					if choice, ok := c.(map[string]any); ok {
						if delta, ok := choice["delta"].(map[string]any); ok {
							if text, ok := delta["content"].(string); ok {
								fullText.WriteString(text)
							}
						}
						if msg, ok := choice["message"].(map[string]any); ok {
							if content, ok := msg["content"].(string); ok {
								fullText.WriteString(content)
							}
						}
					}
				}
			}

			// Unified format: some gateways use content directly
			if content, ok := event["content"].(string); ok && content != "" {
				fullText.WriteString(content)
			}
		}

		// Non-SSE: plain text line (fallback)
		if !strings.HasPrefix(line, "event:") && !strings.HasPrefix(line, ":") && line != "" {
			if fullText.Len() == 0 {
				fullText.WriteString(line)
			}
		}
	}

	text := fullText.String()
	if text == "" {
		// Fallback: use raw response body
		text = fmt.Sprintf("[streaming response: %d bytes]", respBody.Len())
	}

	return respBody.Bytes(), text
}

// logConversation logs a user↔assistant pair to mycelium.
func (p *Proxy) logConversation(userMsg, assistantMsg string, msgReq map[string]any, context []*brain.Entry) {
	// Extract session info
	session := extractSession(msgReq)

	// Build context string
	var ctxStrs []string
	for _, e := range context {
		ctxStrs = append(ctxStrs, fmt.Sprintf("[turn %d/%s] %s", e.Turn, e.Tier, e.User))
	}
	userContext := userMsg
	if len(ctxStrs) > 0 {
		userContext = fmt.Sprintf("[context: %s] %s", strings.Join(ctxStrs, "; "), userMsg)
	}

	entry := &brain.Entry{
		Type:      "talk",
		Session:   session,
		User:      userContext[:min(len(userContext), 500)],
		Assistant: assistantMsg[:min(len(assistantMsg), 2000)],
	}

	if _, err := p.Brain.Append(entry); err != nil {
		log.Printf("⚠️  Failed to log to mycelium: %v", err)
	} else {
		log.Printf("📝 Logged turn %d to mycelium [session=%s]", entry.Turn, session)
	}
}

// directRequest modifies an outgoing request to point at the upstream.
func (p *Proxy) directRequest(req *http.Request, orig *http.Request) {
	upstreamURL, _ := url.Parse(p.Upstream)
	req.URL.Scheme = upstreamURL.Scheme
	req.URL.Host = upstreamURL.Host
	req.Host = upstreamURL.Host

	// Copy query params
	req.URL.RawQuery = orig.URL.RawQuery

	// Set API key from environment
	if apiKey := os.Getenv("ANTHROPIC_API_KEY"); apiKey != "" {
		req.Header.Set("x-api-key", apiKey)
	}
	if apiKey := os.Getenv("CLAUDE_API_KEY"); apiKey != "" {
		req.Header.Set("x-api-key", apiKey)
	}
}

// ── Request parsing helpers ─────────────────────────────────────────────────

func extractUserMessage(req map[string]any) string {
	messages, _ := req["messages"].([]any)
	if len(messages) == 0 {
		return ""
	}
	// Find the last user message
	for i := len(messages) - 1; i >= 0; i-- {
		msg, ok := messages[i].(map[string]any)
		if !ok {
			continue
		}
		if role, _ := msg["role"].(string); role == "user" {
			return extractTextContent(msg)
		}
	}
	return ""
}

func extractAssistantMessages(req map[string]any) []string {
	var result []string
	messages, _ := req["messages"].([]any)
	for _, m := range messages {
		msg, ok := m.(map[string]any)
		if !ok {
			continue
		}
		if role, _ := msg["role"].(string); role == "assistant" {
			result = append(result, extractTextContent(msg))
		}
	}
	return result
}

func extractTextContent(msg map[string]any) string {
	// Content could be a string or an array of blocks
	content, ok := msg["content"]
	if !ok {
		return ""
	}
	switch v := content.(type) {
	case string:
		return v
	case []any:
		var parts []string
		for _, block := range v {
			b, ok := block.(map[string]any)
			if !ok {
				continue
			}
			if text, ok := b["text"].(string); ok {
				parts = append(parts, text)
			}
		}
		return strings.Join(parts, "\n")
	}
	return ""
}

func extractAssistantResponse(resp map[string]any) string {
	// Non-streaming: content is an array of blocks
	content, _ := resp["content"].([]any)
	if len(content) == 0 {
		return ""
	}
	var parts []string
	for _, block := range content {
		b, ok := block.(map[string]any)
		if !ok {
			continue
		}
		if text, ok := b["text"].(string); ok {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, "\n")
}

func extractSession(req map[string]any) string {
	// Extract a clean session identifier from metadata
	if meta, ok := req["metadata"].(map[string]any); ok {
		// Prefer session_id (clean UUID)
		if sessionID, ok := meta["session_id"].(string); ok && sessionID != "" {
			return sessionID[:min(len(sessionID), 20)]
		}
		// Fallback to user_id
		if userID, ok := meta["user_id"].(string); ok && userID != "" {
			return userID[:min(len(userID), 20)]
		}
	}
	// Fallback to ANTHROPIC_AUTH_TOKEN (stable per Claude Code config)
	if key := os.Getenv("ANTHROPIC_AUTH_TOKEN"); key != "" {
		if len(key) > 12 {
			return "token-" + key[:12]
		}
		return "token-" + key
	}
	// Fallback to CLAUDE_API_KEY (stable per machine config)
	if key := os.Getenv("CLAUDE_API_KEY"); key != "" {
		if len(key) > 12 {
			return "token-" + key[:12]
		}
		return "token-" + key
	}
	// Last resort: hostname-based (stable per machine)
	host, err := os.Hostname()
	if err != nil {
		return fmt.Sprintf("proxy-%d", time.Now().Unix())
	}
	return host + "-mycelium"
}

// ── Context injection ───────────────────────────────────────────────────────

func injectContext(req map[string]any, context []*brain.Entry) map[string]any {
	if len(context) == 0 {
		return req
	}

	// Build context block
	var ctxLines []string
	ctxLines = append(ctxLines, "\n<mycelium-memory>")
	ctxLines = append(ctxLines, "Relevant past findings and context from mycelium permanent memory:")
	ctxLines = append(ctxLines, "")
	for _, e := range context {
		ctxLines = append(ctxLines, fmt.Sprintf("[Turn %d | Tier %s]", e.Turn, e.Tier))
		ctxLines = append(ctxLines, fmt.Sprintf("  User: %s", truncate(e.User, 120)))
		ctxLines = append(ctxLines, fmt.Sprintf("  Assistant: %s", truncate(e.Assistant, 200)))
		ctxLines = append(ctxLines, "")
	}
	ctxLines = append(ctxLines, "</mycelium-memory>")
	ctxBlock := strings.Join(ctxLines, "\n")

	// Append to existing system prompt
	if existing, ok := req["system"].(string); ok {
		req["system"] = existing + "\n\n" + ctxBlock
	} else {
		req["system"] = ctxBlock
	}

	return req
}

// ── Utilities ───────────────────────────────────────────────────────────────

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}


// ── Hippocampus: Real-Time Fact Extraction ─────────────────────

// myceliumAPI base URL for the mycelium web backend.
const myceliumAPI = "http://127.0.0.1:8421"

// hippocampusExtract sends a single exchange to the fact extraction endpoint.
// Called as a goroutine — never blocks the response.
func (p *Proxy) hippocampusExtract(user, assistant, session string) {
	payload, err := json.Marshal(map[string]string{
		"user":      user,
		"assistant": assistant,
		"session":   session,
	})
	if err != nil {
		return
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Post(
		myceliumAPI+"/api/memory/extract",
		"application/json",
		bytes.NewReader(payload),
	)
	if err != nil {
		return
	}
	resp.Body.Close()
}


// ── Anti-Memory: Verified Fact Injection ──────────────────────

// injectMemoryFacts adds a structured <mycelium-facts> block
// to the system prompt, giving Claude verified facts to work from.
func injectMemoryFacts(req map[string]any, facts []brain.MemoryFact) map[string]any {
	if len(facts) == 0 {
		return req
	}

	var lines []string
	lines = append(lines, "\n<mycelium-facts>")
	lines = append(lines, "Verified facts from permanent memory:")
	lines = append(lines, "")
	for _, f := range facts {
		lines = append(lines, fmt.Sprintf("  [%s] %s.%s = %s", f.FactType, f.Entity, f.Attribute, truncate(f.Value, 80)))
	}
	lines = append(lines, "</mycelium-facts>")
	block := strings.Join(lines, "\n")

	if existing, ok := req["system"].(string); ok {
		req["system"] = existing + "\n\n" + block
	} else {
		req["system"] = block
	}
	return req
}
