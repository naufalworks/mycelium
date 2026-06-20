package cache

import (
	"testing"
)

func TestPredict(t *testing.T) {
	c := New("/Users/azfar.naufal/Documents/mycelium", "http://127.0.0.1:8443/v1")

	tests := []struct {
		context string
		minP    int // minimum expected predictions
	}{
		{"error in database connection", 1},
		{"deploy docker compose to production", 2},
		{"random text about weather", 0},
		{"", 0},
	}

	for _, tt := range tests {
		t.Run(tt.context[:min(len(tt.context), 15)], func(t *testing.T) {
			p := c.Predict(tt.context)
			if len(p) < tt.minP {
				t.Errorf("Expected at least %d predictions for %q, got %d",
					tt.minP, tt.context, len(p))
			}
		})
	}
}

func TestWordOverlap(t *testing.T) {
	tests := []struct {
		a, b string
		want float64
	}{
		{"hello world", "hello world", 1.0},
		{"hello world", "world hello", 1.0},
		{"how to fix error", "how to fix this error", 0.5},
		{"hello world", "goodbye world", 1.0 / 3.0},
		{"abc", "def", 0},
		{"", "", 0},
	}

	for _, tt := range tests {
		t.Run(tt.a, func(t *testing.T) {
			got := wordOverlap(tt.a, tt.b)
			if got < tt.want-0.01 || got > tt.want+0.01 {
				t.Errorf("wordOverlap(%q, %q) = %.2f, want %.2f", tt.a, tt.b, got, tt.want)
			}
		})
	}
}

func TestTokenize(t *testing.T) {
	result := tokenize("Hello World Test")
	if len(result) != 3 {
		t.Errorf("Expected 3 tokens, got %d", len(result))
	}
	if result[0] != "hello" {
		t.Errorf("Expected 'hello', got %q", result[0])
	}
}

func TestContainsAny(t *testing.T) {
	if !containsAny("this is an error message", []string{"error", "warning"}) {
		t.Error("Expected to find 'error'")
	}
	if containsAny("everything is fine", []string{"error", "warning"}) {
		t.Error("Expected NOT to find 'error'")
	}
	if containsAny("", []string{"error"}) {
		t.Error("Empty string should not match")
	}
}

func TestExtractTopics(t *testing.T) {
	topics := extractTopics("Working on PostgreSQL replication")
	if topics == nil {
		t.Error("Expected non-nil result")
	}
}
