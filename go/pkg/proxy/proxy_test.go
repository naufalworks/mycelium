package proxy

import (
	"strings"
	"testing"

	"github.com/naufalworks/mycelium/go/pkg/brain"
)

func TestExtractPromptName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"/prompt extract-invoice", "extract-invoice"},
		{"run prompt summarize", "summarize"},
		{".prompt translate", "translate"},
		{"normal message", ""},
		{"/prompt", ""},
		{"", ""},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := extractPromptName(tt.input)
			if got != tt.want {
				t.Errorf("extractPromptName(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

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

func TestExtractJSONFromResponse(t *testing.T) {
	tests := []struct {
		body       string
		wantNonNil bool
	}{
		{`{"name":"Alice"}`, true},
		{`{"name":"Alice"}`, true},
		{`no json here`, false},
		{`{"invalid`, false},
	}

	for _, tt := range tests {
		t.Run(tt.body[:min(len(tt.body), 15)], func(t *testing.T) {
			got := extractJSONFromResponse([]byte(tt.body), `{"type":"object"}`)
			if tt.wantNonNil && got == nil {
				t.Errorf("expected non-nil for: %q", tt.body)
			}
			if !tt.wantNonNil && got != nil {
				t.Errorf("expected nil, got: %s", string(got))
			}
		})
	}
}

func TestInjectMemoryFactsEmpty(t *testing.T) {
	req := map[string]interface{}{
		"system": "original system prompt",
	}
	result := injectMemoryFacts(req, nil)
	if result["system"] != "original system prompt" {
		t.Error("injectMemoryFacts with nil should not modify req")
	}
}

func TestInjectMemoryFactsAddsBlock(t *testing.T) {
	req := map[string]interface{}{
		"system": "original",
	}
	facts := []brain.MemoryFact{
		{Entity: "metabase", Attribute: "api_key", Value: "mb_test", FactType: "credential", Confidence: 1.0},
	}
	result := injectMemoryFacts(req, facts)
	sys, ok := result["system"].(string)
	if !ok {
		t.Fatal("system should be a string")
	}
	if !strings.Contains(sys, "<mycelium-facts>") {
		t.Error("expected <mycelium-facts> in system prompt")
	}
	if !strings.Contains(sys, "metabase") {
		t.Error("expected fact content in system prompt")
	}
}

func TestInjectContext(t *testing.T) {
	req := map[string]interface{}{
		"system": "original",
	}
	entries := []*brain.Entry{
		{Turn: 1, Tier: "B", User: "hello", Assistant: "world"},
	}
	result := injectContext(req, entries)
	sys, ok := result["system"].(string)
	if !ok {
		t.Fatal("system should be a string")
	}
	if !strings.Contains(sys, "<mycelium-memory>") {
		t.Error("expected <mycelium-memory> block")
	}
}
