// Package tasks implements an async task queue for long-running LLM operations.
//
// Tasks are stored in SQLite (index.db alongside mycelium) with state machine:
//   pending → processing → done | failed
// A background worker processes pending tasks by calling the LLM endpoint.
// Results are stored as artifacts for later retrieval.
//
// Usage:
//   queue := tasks.New("/path/to/mycelium")
//   id, err := queue.Create("analyze this codebase for security issues")
//   status := queue.Status(id)  // "pending" | "processing" | "done" | "failed"
//   result := queue.GetResult(id) // artifact ID if done
package tasks

import (
	"bytes"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
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

// Task status constants.
const (
	StatusPending   = "pending"
	StatusProcessing = "processing"
	StatusDone      = "done"
	StatusFailed    = "failed"
)

// Schema for the tasks table.
const schema = `
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    prompt            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    result_artifact   TEXT,
    error_msg         TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    started_at        TEXT,
    completed_at      TEXT,
    retry_count       INTEGER DEFAULT 0,
    max_retries       INTEGER DEFAULT 3,
    estimated_cost    REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
`

// Queue manages asynchronous tasks.
type Queue struct {
	dbPath    string
	llmURL    string
	mu        sync.Mutex
	workers   int
	stopCh    chan struct{}
}

// Task represents a queued LLM operation.
type Task struct {
	ID             string `json:"id"`
	Prompt         string `json:"prompt"`
	Status         string `json:"status"`
	ResultArtifact string `json:"result_artifact,omitempty"`
	ErrorMsg       string `json:"error_msg,omitempty"`
	CreatedAt      string `json:"created_at"`
	UpdatedAt      string `json:"updated_at"`
	StartedAt      string `json:"started_at,omitempty"`
	CompletedAt    string `json:"completed_at,omitempty"`
	RetryCount     int    `json:"retry_count"`
	MaxRetries     int    `json:"max_retries"`
}

// New creates a task queue backed by the mycelium index.db.
// llmURL is the endpoint for LLM calls (e.g. "http://127.0.0.1:8443/v1/chat/completions").
func New(myceliumRoot, llmURL string) *Queue {
	q := &Queue{
		dbPath:  filepath.Join(myceliumRoot, "index.db"),
		llmURL:  llmURL,
		workers: 2,
		stopCh:  make(chan struct{}),
	}
	q.init()
	return q
}

func (q *Queue) init() {
	db, err := sql.Open("sqlite3", q.dbPath)
	if err != nil {
		return
	}
	defer db.Close()
	db.Exec(schema)
}

func (q *Queue) db() (*sql.DB, error) {
	return sql.Open("sqlite3", q.dbPath)
}

func generateID() string {
	b := make([]byte, 8)
	rand.Read(b)
	return fmt.Sprintf("task_%s", hex.EncodeToString(b))
}

func now() string {
	return time.Now().UTC().Format(time.RFC3339)
}

// Create enqueues a new task and returns its ID.
// The task starts as "pending" and will be processed by the background worker.
func (q *Queue) Create(prompt string) (string, error) {
	if strings.TrimSpace(prompt) == "" {
		return "", fmt.Errorf("prompt cannot be empty")
	}

	id := generateID()
	ts := now()

	db, err := q.db()
	if err != nil {
		return "", fmt.Errorf("tasks: db open: %w", err)
	}
	defer db.Close()

	_, err = db.Exec(
		`INSERT INTO tasks (id, prompt, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)`,
		id, prompt, StatusPending, ts, ts,
	)
	if err != nil {
		return "", fmt.Errorf("tasks: insert: %w", err)
	}

	log.Printf("[tasks] Created: %s (%d chars)", id, len(prompt))
	return id, nil
}

// Get retrieves a task by ID.
func (q *Queue) Get(id string) (*Task, error) {
	db, err := q.db()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	row := db.QueryRow(
		`SELECT id, prompt, status, COALESCE(result_artifact,''), COALESCE(error_msg,''),
		        created_at, updated_at, COALESCE(started_at,''), COALESCE(completed_at,''),
		        retry_count, max_retries
		 FROM tasks WHERE id = ?`, id,
	)

	t := &Task{}
	err = row.Scan(&t.ID, &t.Prompt, &t.Status, &t.ResultArtifact, &t.ErrorMsg,
		&t.CreatedAt, &t.UpdatedAt, &t.StartedAt, &t.CompletedAt,
		&t.RetryCount, &t.MaxRetries)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("tasks: get: %w", err)
	}
	return t, nil
}

// List returns tasks ordered by creation time, newest first.
func (q *Queue) List(limit int, status string) ([]*Task, error) {
	db, err := q.db()
	if err != nil {
		return nil, err
	}
	defer db.Close()

	var rows *sql.Rows
	if status != "" {
		rows, err = db.Query(
			`SELECT id, prompt, status, COALESCE(result_artifact,''), COALESCE(error_msg,''),
			        created_at, updated_at, COALESCE(started_at,''), COALESCE(completed_at,''),
			        retry_count, max_retries
			 FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?`,
			status, limit,
		)
	} else {
		rows, err = db.Query(
			`SELECT id, prompt, status, COALESCE(result_artifact,''), COALESCE(error_msg,''),
			        created_at, updated_at, COALESCE(started_at,''), COALESCE(completed_at,''),
			        retry_count, max_retries
			 FROM tasks ORDER BY created_at DESC LIMIT ?`,
			limit,
		)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var tasks []*Task
	for rows.Next() {
		t := &Task{}
		if err := rows.Scan(&t.ID, &t.Prompt, &t.Status, &t.ResultArtifact, &t.ErrorMsg,
			&t.CreatedAt, &t.UpdatedAt, &t.StartedAt, &t.CompletedAt,
			&t.RetryCount, &t.MaxRetries); err != nil {
			continue
		}
		tasks = append(tasks, t)
	}
	return tasks, nil
}

// StartWorkers launches background goroutines that process pending tasks.
// Each worker runs in its own goroutine. Call StopWorkers to shut down.
func (q *Queue) StartWorkers() {
	for i := 0; i < q.workers; i++ {
		go q.workerLoop(i)
	}
	log.Printf("[tasks] Started %d workers", q.workers)
}

// StopWorkers signals all workers to stop after current task completes.
func (q *Queue) StopWorkers() {
	close(q.stopCh)
	log.Printf("[tasks] Workers stopping")
}

func (q *Queue) workerLoop(id int) {
	for {
		select {
		case <-q.stopCh:
			return
		default:
			task := q.dequeue()
			if task == nil {
				time.Sleep(2 * time.Second)
				continue
			}
			q.processTask(task)
		}
	}
}

// dequeue picks the oldest pending task and marks it processing.
func (q *Queue) dequeue() *Task {
	db, err := q.db()
	if err != nil {
		return nil
	}
	defer db.Close()

	tx, err := db.Begin()
	if err != nil {
		return nil
	}
	defer tx.Rollback()

	row := tx.QueryRow(
		`SELECT id, prompt, status, COALESCE(result_artifact,''), COALESCE(error_msg,''),
		        created_at, updated_at, COALESCE(started_at,''), COALESCE(completed_at,''),
		        retry_count, max_retries
		 FROM tasks WHERE status = ? ORDER BY created_at ASC LIMIT 1`,
		StatusPending,
	)

	t := &Task{}
	err = row.Scan(&t.ID, &t.Prompt, &t.Status, &t.ResultArtifact, &t.ErrorMsg,
		&t.CreatedAt, &t.UpdatedAt, &t.StartedAt, &t.CompletedAt,
		&t.RetryCount, &t.MaxRetries)
	if err != nil {
		return nil
	}

	ts := now()
	_, err = tx.Exec(`UPDATE tasks SET status = ?, started_at = ?, updated_at = ? WHERE id = ?`,
		StatusProcessing, ts, ts, t.ID)
	if err != nil {
		return nil
	}

	tx.Commit()
	return t
}

// processTask calls the LLM and stores the result.
func (q *Queue) processTask(t *Task) {
	log.Printf("[tasks] Processing: %s", t.ID)

	result, err := q.callLLM(t.Prompt)
	if err != nil {
		q.markFailed(t.ID, err.Error())
		return
	}

	// Store result as an artifact
	artifactID, err := q.storeAsArtifact(t.ID, result)
	if err != nil {
		q.markFailed(t.ID, fmt.Sprintf("artifact store: %v", err))
		return
	}

	q.markDone(t.ID, artifactID)
}

// callLLM sends the prompt to the configured LLM endpoint.
func (q *Queue) callLLM(prompt string) (string, error) {
	payload := map[string]interface{}{
		"model": "kimi-k2.6",
		"messages": []map[string]string{
			{"role": "user", "content": prompt},
		},
		"max_tokens": 4096,
	}
	body, _ := json.Marshal(payload)

	req, err := http.NewRequest("POST", q.llmURL, bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("request create: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("llm call: %w", err)
	}
	defer resp.Body.Close()

	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("response decode: %w", err)
	}

	// Extract text from OpenAI-compatible response
	choices, _ := result["choices"].([]interface{})
	if len(choices) == 0 {
		return "", fmt.Errorf("llm returned empty choices")
	}
	msg, _ := choices[0].(map[string]interface{})
	content, _ := msg["message"].(map[string]interface{})
	text, _ := content["content"].(string)

	return text, nil
}

// storeAsArtifact saves the LLM result as a mycelium artifact.
func (q *Queue) storeAsArtifact(taskID, result string) (string, error) {
	db, err := sql.Open("sqlite3", q.dbPath)
	if err != nil {
		return "", err
	}
	defer db.Close()

	artifactID := fmt.Sprintf("task_%s", taskID[5:])
	ts := now()

	// Store directly in artifacts table
	_, err = db.Exec(
		`INSERT INTO artifacts (id, type, name, data, prompt, created_at)
		 VALUES (?, 'task-result', ?, ?, ?, ?)`,
		artifactID, taskID, result, taskID, ts,
	)
	if err != nil {
		return "", fmt.Errorf("store artifact: %w", err)
	}

	return artifactID, nil
}

func (q *Queue) markDone(id, artifactID string) {
	db, _ := q.db()
	if db == nil {
		return
	}
	defer db.Close()

	ts := now()
	db.Exec(`UPDATE tasks SET status = ?, result_artifact = ?, completed_at = ?, updated_at = ? WHERE id = ?`,
		StatusDone, artifactID, ts, ts, id)
	log.Printf("[tasks] Done: %s → artifact %s", id, artifactID)
}

func (q *Queue) markFailed(id, errMsg string) {
	db, _ := q.db()
	if db == nil {
		return
	}
	defer db.Close()

	ts := now()
	db.Exec(`UPDATE tasks SET status = ?, error_msg = ?, retry_count = retry_count + 1, updated_at = ? WHERE id = ?`,
		StatusFailed, errMsg, ts, id)
	log.Printf("[tasks] Failed: %s: %s", id, errMsg[:min(len(errMsg), 200)])
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
