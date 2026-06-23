package brain

import (
	"testing"
)

func TestFetchMemoryFacts(t *testing.T) {
	b, err := New(DefaultMyceliumDir)
	if err != nil {
		// On machines without mycelium, skip test
		t.Skip("No mycelium installation found:", err)
	}

	t.Run("returns facts for known query", func(t *testing.T) {
		facts := b.FetchMemoryFacts("metabase", 5)
		if len(facts) == 0 {
			t.Skip("No memory facts found — run mycelium snapshot first")
		}
		for _, f := range facts {
			if f.Entity == "" || f.Attribute == "" || f.Value == "" {
				t.Errorf("Incomplete fact: entity=%q attr=%q val=%q",
					f.Entity, f.Attribute, f.Value)
			}
		}
		// Facts should be ordered by confidence descending
		for i := 1; i < len(facts); i++ {
			if facts[i].Confidence > facts[i-1].Confidence {
				t.Errorf("Facts not ordered by confidence: %.2f > %.2f",
					facts[i].Confidence, facts[i-1].Confidence)
			}
		}
	})

	t.Run("returns empty for nonsense query", func(t *testing.T) {
		facts := b.FetchMemoryFacts("xyznonexistent12345", 5)
		if len(facts) != 0 {
			t.Errorf("Expected 0 facts for nonsense query, got %d", len(facts))
		}
	})

	t.Run("short query returns nothing", func(t *testing.T) {
		facts := b.FetchMemoryFacts("ab", 5)
		if len(facts) != 0 {
			t.Errorf("Expected 0 facts for short query, got %d", len(facts))
		}
	})

	t.Run("respects limit", func(t *testing.T) {
		facts := b.FetchMemoryFacts("metabase", 1)
		if len(facts) > 1 {
			t.Errorf("Expected at most 1 fact, got %d", len(facts))
		}
	})
}
