package artifacts

import (
	"encoding/json"
	"testing"
)

func TestStoreAndGet(t *testing.T) {
	s := New("/Users/azfar.naufal/Documents/mycelium")

	a := &Artifact{
		Type:          "expense-report",
		Name:          "Test Report",
		Data:          json.RawMessage(`{"total": 150.00, "vendor": "Acme"}`),
		Prompt:        "extract-invoice",
		PromptVersion: "v1",
		TokenCost:     150,
		Tags:          map[string]string{"month": "june"},
	}

	err := s.Store(a)
	if err != nil {
		t.Fatalf("Store failed: %v", err)
	}

	if a.ID == "" {
		t.Fatal("Expected non-empty ID after store")
	}

	got, err := s.Get(a.ID)
	if err != nil {
		t.Fatalf("Get failed: %v", err)
	}

	if got.Type != "expense-report" {
		t.Errorf("Expected type 'expense-report', got %q", got.Type)
	}
	if got.TokenCost != 150 {
		t.Errorf("Expected TokenCost 150, got %d", got.TokenCost)
	}
	if got.Tags["month"] != "june" {
		t.Errorf("Expected tag month=june, got %q", got.Tags["month"])
	}
	if got.Hash == "" {
		t.Error("Expected non-empty hash")
	}

	t.Logf("Artifact ID: %s", a.ID)
	t.Logf("Hash: %s", got.Hash)
}

func TestList(t *testing.T) {
	s := New("/Users/azfar.naufal/Documents/mycelium")

	// Store two test artifacts
	for i := 0; i < 3; i++ {
		s.Store(&Artifact{
			Type: "test-list",
			Name: "List Test",
			Data: json.RawMessage(`{"idx": ` + string(rune('0'+i)) + `}`),
		})
	}

	results, err := s.List("test-list", 10, 0)
	if err != nil {
		t.Fatalf("List failed: %v", err)
	}
	if len(results) == 0 {
		t.Fatal("Expected at least 1 result")
	}
}

func TestQuery(t *testing.T) {
	s := New("/Users/azfar.naufal/Documents/mycelium")

	// Store test data
	s.Store(&Artifact{
		Type: "test-query",
		Name: "Query Test",
		Data: json.RawMessage(`{"value": 42}`),
	})

	cols, rows, err := s.Query("SELECT COUNT(*) as cnt FROM artifacts WHERE type='test-query'")
	if err != nil {
		t.Fatalf("Query failed: %v", err)
	}
	if len(cols) == 0 {
		t.Fatal("Expected at least 1 column")
	}
	if len(rows) == 0 {
		t.Fatal("Expected at least 1 row")
	}
}

func TestStats(t *testing.T) {
	s := New("/Users/azfar.naufal/Documents/mycelium")
	stats := s.Stats()
	if stats["total"].(int64) < 0 {
		t.Error("Expected non-negative total")
	}
}

func TestDelete(t *testing.T) {
	s := New("/Users/azfar.naufal/Documents/mycelium")

	a := &Artifact{
		Type: "test-delete",
		Data: json.RawMessage(`{"temp": true}`),
	}
	s.Store(a)

	err := s.Delete(a.ID)
	if err != nil {
		t.Fatalf("Delete failed: %v", err)
	}

	got, _ := s.Get(a.ID)
	if got != nil {
		t.Error("Expected nil after delete")
	}
}
