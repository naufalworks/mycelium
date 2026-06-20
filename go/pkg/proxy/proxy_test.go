package proxy

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/naufalworks/mycelium/go/pkg/brain"
)

// TestExtractPromptName covers all prompt patterns
func TestExtractPromptName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"/prompt extract-invoice", "extract-invoice"},
		{"/prompt   spaced-name", "spaced-name"},
		{"run prompt summarize", "summarize"},
		{".prompt translate", "translate"},
		{"normal message with no prompt reference", ""},
		{"/prompt", ""},
		{"", ""},
		{"/prompt extract with extra words", "extract"},
		{"run prompt", ""}, // no name after
	}

	for _, tt := range tests {
		t.Run(tt.input[:min(len(tt.input), 20)], func(t *testing.T) {
			got := extractPromptName(tt.input)
			if got != tt.want {
				t.Errorf("extractPromptName(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// TestTruncateStr covers edge cases
func TestTruncateStr(t *testing.T) {
	tests := []struct {
		input string
		limit int
		want  string
	}{
		{"hello", 10, "hello"},
		{"hello world", 5, "hello"},
		{"", 5, ""},
		{"abcdef", 3, "abc"},
		{"a", 1, "a"},
		{"abc", 0, ""},
	}

	for _, tt := range tests {
		t.Run("", func(t *testing.T) {
			got := truncateStr(tt.input, tt.limit)
			if got != tt.want {
				t.Errorf("truncateStr(%q, %d) = %q, want %q", tt.input, tt.limit, got, tt.want)
			}
		})
	}
}

// TestExtractJSONFromResponse covers markdown fences and raw JSON
func TestExtractJSONFromResponse(t *testing.T) {
	tests := []struct {
		name   string
		body   string
		hasOut bool
	}{
		{"raw JSON", `{"name":"Alice"}`, true},
		{"markdown fence", "text\n```json\n{\"name\":\"Alice\"}\n```\nend", true},
		{"no JSON at all", "just plain text", false},
		{"malformed JSON", `{"invalid`, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractJSONFromResponse([]byte(tt.body), "")
			if tt.hasOut && got == nil {
				t.Errorf("expected JSON, got nil for: %s", tt.name)
			}
			if !tt.hasOut && got != nil {
				t.Errorf("expected nil, got %q for: %s", string(got), tt.name)
			}
		})
	}
}

// TestInjectMemoryFacts verifies fact injection into system prompt
func TestInjectMemoryFacts(t *testing.T) {
	// Test nil facts doesn't modify request
	req := map[string]interface{}{"system": "original prompt"}
	result := injectMemoryFacts(req, nil)
	if result["system"] != "original prompt" {
		t.Error("nil facts should not modify req")
	}

	// Test injection adds <mycelium-facts> block
	req2 := map[string]interface{}{"system": "original"}
	facts := []brain.MemoryFact{
		{Entity: "metabase", Attribute: "api_key", Value: "mb_test", FactType: "credential", Confidence: 1.0},
	}
	result2 := injectMemoryFacts(req2, facts)
	sys, ok := result2["system"].(string)
	if !ok || !strings.Contains(sys, "<mycelium-facts>") {
		t.Error("expected <mycelium-facts> in system prompt")
	}
	if !strings.Contains(sys, "metabase") {
		t.Error("expected fact content in system prompt")
	}

	// Test first-time injection (no existing system)
	req3 := map[string]interface{}{}
	result3 := injectMemoryFacts(req3, facts)
	sys3, ok3 := result3["system"].(string)
	if !ok3 || !strings.Contains(sys3, "<mycelium-facts>") {
		t.Error("expected system to be created")
	}
}

// TestInjectContext verifies brain context injection
func TestInjectContext(t *testing.T) {
	entries := []*brain.Entry{
		{Turn: 1, Tier: "B", User: "user msg", Assistant: "assistant msg"},
	}

	// With existing system
	req := map[string]interface{}{"system": "original"}
	result := injectContext(req, entries)
	sys := result["system"].(string)
	if !strings.Contains(sys, "<mycelium-memory>") {
		t.Error("expected <mycelium-memory> block")
	}
	if !strings.Contains(sys, "user msg") {
		t.Error("expected user message in context")
	}

	// Without existing system
	req2 := map[string]interface{}{}
	result2 := injectContext(req2, entries)
	sys2 := result2["system"].(string)
	if !strings.Contains(sys2, "<mycelium-memory>") {
		t.Error("expected <mycelium-memory> block on first injection")
	}

	// Empty context
	req3 := map[string]interface{}{"system": "original"}
	result3 := injectContext(req3, nil)
	if result3["system"] != "original" {
		t.Error("nil context should not modify req")
	}
}

// TestHandleReaderTool validates tool call parsing
func TestHandleReaderTool(t *testing.T) {
	// Test with empty URL — should return empty without error
	params, _ := json.Marshal(map[string]string{"url": ""})
	p := &Proxy{}
	_, err := p.handleReaderTool(params)
	if err != nil {
		t.Errorf("empty URL should not error: %v", err)
	}

	// Test with missing URL
	params, _ = json.Marshal(map[string]string{})
	_, err = p.handleReaderTool(params)
	if err != nil {
		t.Errorf("missing URL should not error: %v", err)
	}
}
