package proxy

import (
	"database/sql"
	"encoding/json"
	"log"

	"github.com/naufalworks/mycelium/go/pkg/reader"
	_ "github.com/mattn/go-sqlite3"
)

// handleReaderTool handles a "mycelium_read" tool call from Claude.
func (p *Proxy) handleReaderTool(params json.RawMessage) (string, error) {
	var input struct {
		URL  string `json:"url"`
		Save bool   `json:"save,omitempty"`
	}
	if err := json.Unmarshal(params, &input); err != nil {
		return "", err
	}
	if input.URL == "" {
		return "", nil
	}

	result, err := reader.Fetch(input.URL)
	if err != nil {
		return "", err
	}

	if input.Save && result.TextOnly != "" {
		go p.savePageToMemory(result)
	}

	output, _ := json.Marshal(map[string]interface{}{
		"title":      result.Title,
		"content":    result.Content,
		"word_count": result.WordCount,
		"url":        result.URL,
	})
	return string(output), nil
}

func (p *Proxy) savePageToMemory(result *reader.Result) {
	dbPath := p.Brain.Mycelium + "/index.db"
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		return
	}
	defer db.Close()

	content := result.TextOnly
	if len(content) > 2000 {
		content = content[:2000]
	}

	_, err = db.Exec(`
		INSERT INTO memory_facts (entity, attribute, value, fact_type, confidence, tier, entropy, created_at, updated_at)
		VALUES (?, 'content', ?, 'fact', 0.8, 1, 0.5, datetime('now'), datetime('now'))
		ON CONFLICT(entity, attribute, value) DO UPDATE SET updated_at = datetime('now')
	`, "web:"+result.URL, content)

	if err == nil {
		log.Printf("[reader] Saved to memory: %s (%d words)", result.URL, result.WordCount)
	}
}
