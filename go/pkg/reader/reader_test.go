package reader

import (
	"strings"
	"testing"

	"golang.org/x/net/html"
)

func TestFetch(t *testing.T) {
	// Test with a simple URL
	result, err := Fetch("https://example.com")
	if err != nil {
		t.Fatalf("Fetch failed: %v", err)
	}

	if result.Title == "" {
		t.Error("Expected non-empty title")
	}

	if result.Content == "" {
		t.Error("Expected non-empty content")
	}

	if result.WordCount < 5 {
		t.Errorf("Expected at least 5 words, got %d", result.WordCount)
	}

	t.Logf("Title: %s", result.Title)
	t.Logf("Words: %d", result.WordCount)
	t.Logf("Content preview: %s", result.Content[:min(100, len(result.Content))])
}

func TestFetchInvalidURL(t *testing.T) {
	_, err := Fetch("http://nonexistent-domain-12345.com/page.html")
	if err == nil {
		t.Error("Expected error for invalid URL")
	}
}

func TestExtractTitle(t *testing.T) {
	title := extractTitle(parseHTML("<html><head><title>Test Page</title></head><body></body></html>"))
	if title != "Test Page" {
		t.Errorf("Expected 'Test Page', got %q", title)
	}
}

func TestExtractContent(t *testing.T) {
	html := `<html><body><h1>Title</h1><p>Hello world this is a paragraph.</p><script>bad</script><nav>skip</nav></body></html>`
	content := extractContent(parseHTML(html))
	if content == "" {
		t.Error("Expected non-empty content")
	}
	if contains(content, "bad") {
		t.Error("Content should not contain script text")
	}
	if !contains(content, "Hello world") {
		t.Error("Content should contain paragraph text")
	}
}

func parseHTML(s string) *html.Node {
	doc, _ := html.Parse(strings.NewReader(s))
	return doc
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && strings.Contains(s, substr)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
