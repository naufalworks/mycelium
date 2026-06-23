// Package prompts provides a typed prompt registry + schema validator.
// Prompts are stored in memory_facts table with fact_type="prompt".
// Schema validation is pure Go — no external JSON Schema library needed.
package prompts

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// Prompt defines a compiled, typed prompt template.
type Prompt struct {
	Name        string `json:"name"`
	Template    string `json:"template"`
	InputShape  string `json:"input_schema"`  // JSON Schema string
	OutputShape string `json:"output_schema"` // JSON Schema string
	Description string `json:"description"`
	Model       string `json:"model"`
	CreatedAt   string `json:"created_at"`
	Version     int    `json:"version"`
}

// Registry manages compiled prompts in the mycelium index.db.
type Registry struct {
	dbPath string
	mu     sync.RWMutex
}

// NewRegistry opens the prompt registry backed by SQLite memory_facts.
func NewRegistry(myceliumRoot string) *Registry {
	return &Registry{
		dbPath: filepath.Join(myceliumRoot, "index.db"),
	}
}

func (r *Registry) db() (*sql.DB, error) {
	return sql.Open("sqlite3", r.dbPath)
}

// Define creates or updates a compiled prompt.
func (r *Registry) Define(p Prompt) error {
	if p.Name == "" || p.Template == "" {
		return fmt.Errorf("name and template are required")
	}
	if p.OutputShape == "" {
		return fmt.Errorf("output_schema is required for validation")
	}

	now := time.Now().UTC().Format(time.RFC3339)
	data, _ := json.Marshal(p)

	db, err := r.db()
	if err != nil {
		return err
	}
	defer db.Close()

	// Store in memory_facts with fact_type="prompt"
	_, err = db.Exec(`
		INSERT INTO memory_facts (entity, attribute, value, fact_type, confidence, tier, entropy, created_at, updated_at)
		VALUES ('prompt', ?, ?, 'prompt', 1.0, 0, 0.8, ?, ?)
		ON CONFLICT(entity, attribute, value) DO UPDATE SET
			value = ?, updated_at = ?
	`, p.Name, string(data), now, now, string(data), now)
	return err
}

// Get retrieves a compiled prompt by name.
func (r *Registry) Get(name string) (*Prompt, error) {
	db, err := r.db()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	var value string
	err = db.QueryRow(
		`SELECT value FROM memory_facts
		 WHERE entity='prompt' AND attribute=? AND fact_type='prompt'
		 ORDER BY updated_at DESC LIMIT 1`,
		name,
	).Scan(&value)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}

	var p Prompt
	if err := json.Unmarshal([]byte(value), &p); err != nil {
		return nil, err
	}
	return &p, nil
}

// List returns all compiled prompts.
func (r *Registry) List() ([]Prompt, error) {
	db, err := r.db()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	rows, err := db.Query(
		`SELECT value FROM memory_facts
		 WHERE entity='prompt' AND fact_type='prompt'
		 ORDER BY updated_at DESC`,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var prompts []Prompt
	for rows.Next() {
		var value string
		if err := rows.Scan(&value); err != nil {
			continue
		}
		var p Prompt
		if json.Unmarshal([]byte(value), &p) == nil {
			prompts = append(prompts, p)
		}
	}
	return prompts, nil
}

// Delete removes a compiled prompt.
func (r *Registry) Delete(name string) error {
	db, err := r.db()
	if err != nil {
		return err
	}
	defer db.Close()

	_, err = db.Exec(
		`DELETE FROM memory_facts WHERE entity='prompt' AND attribute=? AND fact_type='prompt'`,
		name,
	)
	return err
}

// ── Schema Validation (pure Go, no external lib) ──────────────

// Validate checks if a JSON value matches a simple JSON Schema.
// Supports: type, properties, required, items for arrays, enum.
// Returns nil on success, error with details on mismatch.
func Validate(data []byte, schema string) error {
	if schema == "" {
		return nil // no schema = always valid
	}

	var sch map[string]interface{}
	if err := json.Unmarshal([]byte(schema), &sch); err != nil {
		return fmt.Errorf("invalid schema JSON: %w", err)
	}

	var val interface{}
	if err := json.Unmarshal(data, &val); err != nil {
		return fmt.Errorf("output is not valid JSON: %w", err)
	}

	return validateValue(val, sch, "$")
}

func validateValue(val interface{}, sch map[string]interface{}, path string) error {
	if sch == nil {
		return nil
	}

	// type check
	if expectedType, ok := sch["type"].(string); ok {
		actual := goTypeToSchemaType(val)
		if actual != expectedType {
			return fmt.Errorf("%s: expected type %q, got %q (value: %v)", path, expectedType, actual, val)
		}
	}

	// enum check
	if enumVals, ok := sch["enum"].([]interface{}); ok {
		found := false
		for _, e := range enumVals {
			if jsonString(val) == jsonString(e) {
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("%s: value %v not in enum %v", path, val, enumVals)
		}
	}

	// object properties
	if props, ok := sch["properties"].(map[string]interface{}); ok {
		obj, ok := val.(map[string]interface{})
		if !ok {
			return fmt.Errorf("%s: expected object, got %T", path, val)
		}

		// required fields
		if req, ok := sch["required"].([]interface{}); ok {
			for _, r := range req {
				field, ok := r.(string)
				if !ok {
					continue
				}
				if _, exists := obj[field]; !exists {
					return fmt.Errorf("%s: missing required field %q", path, field)
				}
			}
		}

		// validate each property
		for fieldName, fieldSchema := range props {
			if fieldVal, exists := obj[fieldName]; exists {
				fs, ok := fieldSchema.(map[string]interface{})
				if !ok {
					continue
				}
				if err := validateValue(fieldVal, fs, path+"."+fieldName); err != nil {
					return err
				}
			}
		}
	}

	// array items
	if items, ok := sch["items"].(map[string]interface{}); ok {
		arr, ok := val.([]interface{})
		if !ok {
			return fmt.Errorf("%s: expected array, got %T", path, val)
		}
		for i, item := range arr {
			if err := validateValue(item, items, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}

	return nil
}

func goTypeToSchemaType(v interface{}) string {
	switch v.(type) {
	case string:
		return "string"
	case float64:
		return "number"
	case bool:
		return "boolean"
	case nil:
		return "null"
	case []interface{}:
		return "array"
	case map[string]interface{}:
		return "object"
	default:
		return fmt.Sprintf("%T", v)
	}
}

func jsonString(v interface{}) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// ── Prompt Execution ──────────────────────────────────────────

// Execute renders a prompt template with input data, returns the compiled prompt string.
// The actual LLM call happens at the proxy level — this just prepares the prompt.
func (p *Prompt) Execute(inputJSON json.RawMessage) (string, error) {
	if p.InputShape != "" && len(inputJSON) > 0 {
		if err := Validate(inputJSON, p.InputShape); err != nil {
			return "", fmt.Errorf("input validation failed: %w", err)
		}
	}

	// Simple template rendering: replace {{key}} with values from input
	var input map[string]interface{}
	json.Unmarshal(inputJSON, &input)

	result := p.Template
	for k, v := range input {
		valStr := fmt.Sprintf("%v", v)
		result = strings.ReplaceAll(result, "{{"+k+"}}", valStr)
	}

	// Validate output schema exists
	if p.OutputShape == "" {
		return "", fmt.Errorf("prompt %q has no output_schema", p.Name)
	}

	return result, nil
}
