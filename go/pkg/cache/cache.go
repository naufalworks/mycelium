// Package cache implements a speculative LLM response cache.
//
// It predicts likely next questions based on current session context,
// pre-computes answers via the LLM during idle time, and serves
// cached responses instantly when predictions are correct.
//
// Architecture:
//   Predictor: reads mycelium context → generates predicted questions
//   Precomputer: for each prediction with confidence > threshold,
//                calls LLM, stores result as artifact with type="speculative"
//   Matcher: intercepts incoming prompts, matches against cached predictions
//
// Usage:
//   c := cache.New("/path/to/mycelium", "http://127.0.0.1:8443/v1")
//   c.Predict(context)  // returns predicted questions
//   c.Precompute()      // background pre-computation
//   c.Lookup(prompt)    // check cache → hit or miss
package cache

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

const maxCacheEntries = 100

// Prediction represents a predicted question and its pre-computed answer.
type Prediction struct {
	Question   string  `json:"question"`
	Confidence float64 `json:"confidence"`
	Result     string  `json:"result,omitempty"`
	ArtifactID string  `json:"artifact_id,omitempty"`
	CreatedAt  string  `json:"created_at"`
}

// Cache manages speculative pre-computation and cache hits.
type Cache struct {
	dbPath    string
	llmURL    string
	mu        sync.Mutex
	stopCh    chan struct{}
}

// New creates a speculative cache.
func New(myceliumRoot, llmURL string) *Cache {
	c := &Cache{
		dbPath: filepath.Join(myceliumRoot, "index.db"),
		llmURL: llmURL,
		stopCh: make(chan struct{}),
	}
	return c
}

func (c *Cache) db() (*sql.DB, error) {
	return sql.Open("sqlite3", c.dbPath)
}

// Predict generates likely next questions based on context.
// Simple keyword-based prediction that improves over time.
func (c *Cache) Predict(context string) []Prediction {
	var predictions []Prediction

	// Strategy 1: Extract domain-specific follow-ups from context
	topics := extractTopics(context)
	for _, topic := range topics {
		predictions = append(predictions, Prediction{
			Question:   fmt.Sprintf("tell me more about %s", topic),
			Confidence: 0.3,
			CreatedAt:  time.Now().UTC().Format(time.RFC3339),
		})
	}

	// Strategy 2: Check for command patterns
	if containsAny(context, []string{"error", "fail", "bug", "crash", "broken"}) {
		predictions = append(predictions, Prediction{
			Question:   "how to fix this error",
			Confidence: 0.5,
			CreatedAt:  time.Now().UTC().Format(time.RFC3339),
		})
	}
	if containsAny(context, []string{"deploy", "release", "rollout"}) {
		predictions = append(predictions, Prediction{
			Question:   "deployment checklist",
			Confidence: 0.5,
			CreatedAt:  time.Now().UTC().Format(time.RFC3339),
		})
	}
	if containsAny(context, []string{"docker", "container", "compose"}) {
		predictions = append(predictions, Prediction{
			Question:   "docker best practices",
			Confidence: 0.4,
			CreatedAt:  time.Now().UTC().Format(time.RFC3339),
		})
	}
	if containsAny(context, []string{"sql", "postgres", "query", "database"}) {
		predictions = append(predictions, Prediction{
			Question:   "optimize this SQL query",
			Confidence: 0.4,
			CreatedAt:  time.Now().UTC().Format(time.RFC3339),
		})
	}
	if containsAny(context, []string{"test", "testing", "pytest", "unit"}) {
		predictions = append(predictions, Prediction{
			Question:   "how to improve test coverage",
			Confidence: 0.4,
			CreatedAt:  time.Now().UTC().Format(time.RFC3339),
		})
	}

	return predictions
}

// Precompute generates answers for predicted questions in the background.
// Only processes predictions with confidence >= threshold.
func (c *Cache) Precompute(threshold float64) []string {
	// Get recent context from mycelium facts
	context := c.getRecentContext()
	predictions := c.Predict(context)

	var computed []string
	for _, p := range predictions {
		if p.Confidence < threshold {
			continue
		}

		// Check if already cached
		if c.exists(p.Question) {
			continue
		}

		// Compute the answer
		result, err := c.callLLM(p.Question)
		if err != nil {
			log.Printf("[cache] Precompute failed for %q: %v", p.Question[:min(len(p.Question), 60)], err)
			continue
		}

		// Store as artifact
		artifactID, err := c.storePrediction(p.Question, result)
		if err != nil {
			log.Printf("[cache] Store failed: %v", err)
			continue
		}

		computed = append(computed, artifactID)
		log.Printf("[cache] Precomputed: %q → %s", p.Question[:min(len(p.Question), 60)], artifactID)
	}

	return computed
}

// Lookup checks if a prompt matches any cached prediction.
// Returns the cached result and artifact ID, or empty strings on miss.
func (c *Cache) Lookup(prompt string) (result string, artifactID string, hit bool) {
	if len(prompt) < 10 {
		return "", "", false
	}

	db, err := c.db()
	if err != nil {
		return "", "", false
	}
	defer db.Close()

	promptLower := strings.ToLower(prompt)

	rows, err := db.Query(
		`SELECT a.data, a.id FROM artifacts a
		 WHERE a.type = 'speculative'
		 ORDER BY a.created_at DESC LIMIT ?`, maxCacheEntries,
	)
	if err != nil {
		return "", "", false
	}
	defer rows.Close()

	for rows.Next() {
		var data, id string
		if err := rows.Scan(&data, &id); err != nil {
			continue
		}
		// Parse the artifact data to get the predicted question
		var entry struct {
			Question string `json:"question"`
			Result   string `json:"result"`
		}
		if err := json.Unmarshal([]byte(data), &entry); err != nil {
			continue
		}
		// Match by word overlap
		if wordOverlap(promptLower, strings.ToLower(entry.Question)) > 0.6 {
			log.Printf("[cache] HIT: %s matched %q", id, entry.Question[:min(len(entry.Question), 60)])
			return entry.Result, id, true
		}
	}

	return "", "", false
}

// Stats returns cache hit/miss counts and current size.
func (c *Cache) Stats() map[string]interface{} {
	db, err := c.db()
	if err != nil {
		return map[string]interface{}{"error": err.Error()}
	}
	defer db.Close()

	var total int
	db.QueryRow("SELECT COUNT(*) FROM artifacts WHERE type='speculative'").Scan(&total)

	return map[string]interface{}{
		"cached_entries": total,
		"max_entries":    maxCacheEntries,
	}
}

// Clear removes all speculative cache entries.
func (c *Cache) Clear() error {
	db, err := c.db()
	if err != nil {
		return err
	}
	defer db.Close()

	_, err = db.Exec("DELETE FROM artifacts WHERE type='speculative'")
	return err
}

// getRecentContext reads recent mycelium context for prediction.
func (c *Cache) getRecentContext() string {
	db, err := c.db()
	if err != nil {
		return ""
	}
	defer db.Close()

	// Get recent memory facts
	rows, err := db.Query(
		`SELECT entity, attribute, value FROM memory_facts
		 ORDER BY updated_at DESC LIMIT 20`,
	)
	if err != nil {
		return ""
	}
	defer rows.Close()

	var parts []string
	for rows.Next() {
		var e, a, v string
		if rows.Scan(&e, &a, &v) == nil {
			parts = append(parts, fmt.Sprintf("%s %s %s", e, a, v))
		}
	}

	return strings.Join(parts, ", ")
}

// exists checks if a question is already cached.
func (c *Cache) exists(question string) bool {
	db, err := c.db()
	if err != nil {
		return false
	}
	defer db.Close()

	var count int
	// Simple check by matching against stored artifact data
	rows, _ := db.Query(
		`SELECT COUNT(*) FROM artifacts WHERE type='speculative' AND data LIKE ?`,
		"%"+question+"%",
	)
	if rows != nil {
		defer rows.Close()
		if rows.Next() {
			rows.Scan(&count)
		}
	}
	return count > 0
}

// callLLM sends a prompt to the LLM endpoint.
func (c *Cache) callLLM(prompt string) (string, error) {
	payload := map[string]interface{}{
		"model": "kimi-k2.6",
		"messages": []map[string]string{
			{"role": "user", "content": prompt},
		},
		"max_tokens": 2048,
	}
	body, _ := json.Marshal(payload)

	req, err := http.NewRequest("POST", c.llmURL, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", err
	}

	choices, _ := result["choices"].([]interface{})
	if len(choices) == 0 {
		return "", fmt.Errorf("empty response")
	}
	msg, _ := choices[0].(map[string]interface{})
	content, _ := msg["message"].(map[string]interface{})
	text, _ := content["content"].(string)
	return text, nil
}

// storePrediction saves a prediction as a speculative artifact.
func (c *Cache) storePrediction(question, result string) (string, error) {
	db, err := c.db()
	if err != nil {
		return "", err
	}
	defer db.Close()

	// Evict oldest if at capacity
	var count int
	db.QueryRow("SELECT COUNT(*) FROM artifacts WHERE type='speculative'").Scan(&count)
	if count >= maxCacheEntries {
		db.Exec(`DELETE FROM artifacts WHERE type='speculative'
		         AND id IN (SELECT id FROM artifacts WHERE type='speculative' ORDER BY created_at ASC LIMIT 1)`)
	}

	data, _ := json.Marshal(map[string]string{
		"question": question,
		"result":   result,
	})

	artifactID := fmt.Sprintf("spec_%x", time.Now().UnixNano())
	ts := time.Now().UTC().Format(time.RFC3339)

	_, err = db.Exec(
		`INSERT INTO artifacts (id, type, name, data, created_at)
		 VALUES (?, 'speculative', ?, ?, ?)`,
		artifactID, question, string(data), ts,
	)
	if err != nil {
		return "", err
	}

	return artifactID, nil
}

// wordOverlap computes the fraction of shared words between two strings.
func wordOverlap(a, b string) float64 {
	wordsA := tokenize(a)
	wordsB := tokenize(b)
	if len(wordsA) == 0 || len(wordsB) == 0 {
		return 0
	}

	set := make(map[string]bool, len(wordsA))
	for _, w := range wordsA {
		if len(w) > 2 {
			set[w] = true
		}
	}

	intersection := 0
	for _, w := range wordsB {
		if len(w) > 2 && set[w] {
			intersection++
		}
	}

	union := len(wordsA) + len(wordsB) - intersection
	if union == 0 {
		return 0
	}
	return float64(intersection) / float64(union)
}

func tokenize(s string) []string {
	return strings.Fields(strings.ToLower(s))
}

func extractTopics(context string) []string {
	words := tokenize(context)
	seen := make(map[string]bool)
	var topics []string

	// Look for capitalized words (likely entities/topics)
	for _, w := range words {
		if len(w) > 3 && !seen[w] {
			seen[w] = true
			topics = append(topics, w)
		}
	}
	if len(topics) > 5 {
		topics = topics[:5]
	}
	return topics
}

func containsAny(s string, keywords []string) bool {
	s = strings.ToLower(s)
	for _, k := range keywords {
		if strings.Contains(s, k) {
			return true
		}
	}
	return false
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
