// Package artifacts provides structured artifact storage for mycelium.
// Artifacts are large structured outputs from prompts (expense reports, configs, etc.)
// stored as JSON blobs in SQLite, separate from lightweight memory_facts.
package artifacts

import (
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// Schema for the artifacts table.
const SCHEMA = `
CREATE TABLE IF NOT EXISTS artifacts (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    name           TEXT,
    data           TEXT NOT NULL,
    prompt         TEXT,
    prompt_version TEXT,
    input_summary  TEXT,
    token_cost     INTEGER DEFAULT 0,
    tags           TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    hash           TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(type);
CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(name);
`

// Artifact represents a stored structured output.
type Artifact struct {
	ID            string            `json:"id"`
	Type          string            `json:"type"`
	Name          string            `json:"name,omitempty"`
	Data          json.RawMessage   `json:"data"`
	Prompt        string            `json:"prompt,omitempty"`
	PromptVersion string            `json:"prompt_version,omitempty"`
	InputSummary  string            `json:"input_summary,omitempty"`
	TokenCost     int               `json:"token_cost,omitempty"`
	Tags          map[string]string `json:"tags,omitempty"`
	CreatedAt     string            `json:"created_at"`
	UpdatedAt     string            `json:"updated_at,omitempty"`
	Hash          string            `json:"hash,omitempty"`
}

// Store provides CRUD operations on artifacts.
type Store struct {
	dbPath string
}

// New opens an artifact store backed by the mycelium index.db.
func New(myceliumRoot string) *Store {
	s := &Store{dbPath: filepath.Join(myceliumRoot, "index.db")}
	s.init()
	return s
}

func (s *Store) db() (*sql.DB, error) {
	return sql.Open("sqlite3", s.dbPath)
}

func (s *Store) init() {
	db, err := s.db()
	if err != nil {
		return
	}
	defer db.Close()
	db.Exec(SCHEMA)
}

func generateID(artifactType string) string {
	now := time.Now().UTC().Format(time.RFC3339Nano)
	raw := fmt.Sprintf("%s|%s|%d", artifactType, now, time.Now().UnixNano())
	h := sha256.Sum256([]byte(raw))
	return fmt.Sprintf("%s_%x", artifactType[:min(8, len(artifactType))], h[:8])
}

// Store saves an artifact. If ID is empty, generates one.
// data must be valid JSON.
func (s *Store) Store(a *Artifact) error {
	if a.ID == "" {
		a.ID = generateID(a.Type)
	}
	if a.CreatedAt == "" {
		a.CreatedAt = time.Now().UTC().Format(time.RFC3339)
	}
	a.UpdatedAt = time.Now().UTC().Format(time.RFC3339)

	tagsJSON, _ := json.Marshal(a.Tags)
	raw := fmt.Sprintf("%s|%s|%s", a.ID, a.Type, string(a.Data))
	sum := sha256.Sum256([]byte(raw))
	a.Hash = fmt.Sprintf("%x", sum[:8])

	db, err := s.db()
	if err != nil {
		return err
	}
	defer db.Close()

	_, err = db.Exec(`
		INSERT INTO artifacts (id, type, name, data, prompt, prompt_version,
		                       input_summary, token_cost, tags, created_at, updated_at, hash)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			data = excluded.data,
			updated_at = excluded.updated_at,
			token_cost = excluded.token_cost,
			name = excluded.name
	`, a.ID, a.Type, a.Name, string(a.Data), a.Prompt, a.PromptVersion,
		a.InputSummary, a.TokenCost, string(tagsJSON), a.CreatedAt, a.UpdatedAt, a.Hash)
	return err
}

// Get retrieves an artifact by ID.
func (s *Store) Get(id string) (*Artifact, error) {
	db, err := s.db()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	row := db.QueryRow(`
		SELECT id, type, COALESCE(name,''), data, COALESCE(prompt,''),
		       COALESCE(prompt_version,''), COALESCE(input_summary,''),
		       COALESCE(token_cost,0), COALESCE(tags,'{}'), created_at,
		       COALESCE(updated_at,''), COALESCE(hash,'')
		FROM artifacts WHERE id = ?
	`, id)

	return scanArtifact(row)
}

// List returns artifacts of a given type, newest first.
func (s *Store) List(artifactType string, limit, offset int) ([]*Artifact, error) {
	db, err := s.db()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	var rows *sql.Rows
	if artifactType == "" {
		rows, err = db.Query(`
			SELECT id, type, COALESCE(name,''), data, COALESCE(prompt,''),
			       COALESCE(prompt_version,''), COALESCE(input_summary,''),
			       COALESCE(token_cost,0), COALESCE(tags,'{}'), created_at,
			       COALESCE(updated_at,''), COALESCE(hash,'')
			FROM artifacts ORDER BY created_at DESC LIMIT ? OFFSET ?
		`, limit, offset)
	} else {
		rows, err = db.Query(`
			SELECT id, type, COALESCE(name,''), data, COALESCE(prompt,''),
			       COALESCE(prompt_version,''), COALESCE(input_summary,''),
			       COALESCE(token_cost,0), COALESCE(tags,'{}'), created_at,
			       COALESCE(updated_at,''), COALESCE(hash,'')
			FROM artifacts WHERE type = ? ORDER BY created_at DESC LIMIT ? OFFSET ?
		`, artifactType, limit, offset)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []*Artifact
	for rows.Next() {
		a, err := scanArtifact(rows)
		if err != nil {
			continue
		}
		results = append(results, a)
	}
	return results, nil
}

// Query runs a raw SQL query over artifacts and returns the results.
// Only SELECT queries are allowed. Dangerous keywords are blocked.
func (s *Store) Query(sqlQuery string) ([]string, []map[string]interface{}, error) {
	sqlUpper := strings.TrimSpace(strings.ToUpper(sqlQuery))
	if !strings.HasPrefix(sqlUpper, "SELECT") {
		return nil, nil, fmt.Errorf("only SELECT queries allowed")
	}

	// Block dangerous operations
	blocked := []string{"DROP ", "DELETE ", "INSERT ", "UPDATE ", "ALTER ", "CREATE ",
		"ATTACH ", "DETACH ", "REINDEX ", "REPLACE ", "VACUUM"}
	for _, b := range blocked {
		if strings.Contains(sqlUpper, b) {
			return nil, nil, fmt.Errorf("blocked keyword: %s", b)
		}
	}

	db, err := s.db()
	if err != nil {
		return nil, nil, err
	}
	defer db.Close()

	rows, err := db.Query(sqlQuery)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()

	cols, _ := rows.Columns()
	var results []map[string]interface{}

	for rows.Next() {
		vals := make([]interface{}, len(cols))
		valPtrs := make([]interface{}, len(cols))
		for i := range vals {
			valPtrs[i] = &vals[i]
		}
		if err := rows.Scan(valPtrs...); err != nil {
			continue
		}
		row := make(map[string]interface{})
		for i, col := range cols {
			val := vals[i]
			b, ok := val.([]byte)
			if ok {
				row[col] = string(b)
			} else {
				row[col] = val
			}
		}
		results = append(results, row)
	}
	return cols, results, nil
}

// Stats returns aggregate statistics about stored artifacts.
func (s *Store) Stats() map[string]interface{} {
	stats := map[string]interface{}{
		"total":       0,
		"by_type":     map[string]int{},
		"total_tokens": 0,
		"total_cost":   0,
	}

	db, err := s.db()
	if err != nil {
		return stats
	}
	defer db.Close()

	var total int64
	var totalTokens int64
	db.QueryRow("SELECT COUNT(*) FROM artifacts").Scan(&total)
	stats["total"] = total

	rows, _ := db.Query("SELECT type, COUNT(*) as c FROM artifacts GROUP BY type ORDER BY c DESC")
	if rows != nil {
		defer rows.Close()
		byType := stats["by_type"].(map[string]int)
		for rows.Next() {
			var t string
			var c int
			rows.Scan(&t, &c)
			byType[t] = c
		}
	}

	db.QueryRow("SELECT COALESCE(SUM(token_cost),0) FROM artifacts").Scan(&totalTokens)
	stats["total_tokens"] = totalTokens
	// Rough cost: $3 per million tokens
	stats["total_cost"] = float64(totalTokens) * 3.0 / 1_000_000

	return stats
}

// Delete removes an artifact by ID.
func (s *Store) Delete(id string) error {
	db, err := s.db()
	if err != nil {
		return err
	}
	defer db.Close()
	_, err = db.Exec("DELETE FROM artifacts WHERE id = ?", id)
	return err
}

func scanArtifact(row interface{}) (*Artifact, error) {
	var a Artifact
	var name, prompt, promptVer, inputSum, tagsStr, updated, hash, dataStr sql.NullString
	var tokenCost sql.NullInt64

	err := row.(interface {
		Scan(dest ...interface{}) error
	}).Scan(&a.ID, &a.Type, &name, &dataStr, &prompt,
		&promptVer, &inputSum, &tokenCost, &tagsStr, &a.CreatedAt,
		&updated, &hash)
	if err != nil {
		return nil, err
	}

	a.Name = name.String
	a.Prompt = prompt.String
	a.PromptVersion = promptVer.String
	a.InputSummary = inputSum.String
	if tokenCost.Valid {
		a.TokenCost = int(tokenCost.Int64)
	}
	a.UpdatedAt = updated.String
	a.Hash = hash.String

	// Convert data string to json.RawMessage
	if dataStr.Valid && dataStr.String != "" {
		a.Data = json.RawMessage(dataStr.String)
	}

	if tagsStr.Valid && tagsStr.String != "" && tagsStr.String != "{}" {
		json.Unmarshal([]byte(tagsStr.String), &a.Tags)
	}

	return &a, nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
