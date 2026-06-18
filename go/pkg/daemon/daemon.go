// Package daemon provides the mycelium safety-net daemon.
// Polls Hermes state.db for completed user↔assistant pairs and imports them.
package daemon

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/naufalworks/mycelium/go/pkg/brain"
)

const (
	DefaultPort      = "20151"
	DefaultInterval  = 15 * time.Second
	DefaultStateFile = "state.json"
)

// Daemon manages the mycelium import daemon.
type Daemon struct {
	Brain      *brain.Brain
	HermesDB   string
	StatePath  string
	Port       string
	Interval   time.Duration
	State      *DaemonState
	httpServer *http.Server
	stop       chan struct{}
}

// DaemonState is the persistent daemon state.
type DaemonState struct {
	LastAssistantID int    `json:"last_assistant_id"`
	LastVerifyHour  string `json:"last_verify_hour"`
	Imports         int    `json:"imports"`
}

// Pair represents a user↔assistant message pair from Hermes.
type Pair struct {
	AssistantID    int
	SessionID      string
	UserContent    string
	AssistantContent string
}

// New creates a new daemon.
func New(b *brain.Brain) *Daemon {
	home, _ := os.UserHomeDir()
	hermesDir := filepath.Join(home, ".hermes")

	d := &Daemon{
		Brain:     b,
		HermesDB:  filepath.Join(hermesDir, "state.db"),
		StatePath: filepath.Join(hermesDir, "myceliumd", "state.json"),
		Port:      DefaultPort,
		Interval:  DefaultInterval,
		State: &DaemonState{
			LastAssistantID: 0,
			LastVerifyHour:  "",
			Imports:         0,
		},
		stop: make(chan struct{}),
	}
	d.loadState()
	return d
}

// Start begins the daemon loop.
func (d *Daemon) Start() error {
	log.Printf("🍄 Mycelium daemon starting (poll=%s, port=%s)", d.Interval, d.Port)

	// Start health HTTP server
	mux := http.NewServeMux()
	mux.HandleFunc("/health", d.handleHealth)
	d.httpServer = &http.Server{
		Addr:    fmt.Sprintf("127.0.0.1:%s", d.Port),
		Handler: mux,
	}
	go d.httpServer.ListenAndServe()

	// Main loop
	ticker := time.NewTicker(d.Interval)
	defer ticker.Stop()

	// Run once immediately
	d.runOnce()

	for {
		select {
		case <-ticker.C:
			d.runOnce()
		case <-d.stop:
			log.Println("🍄 Daemon stopping")
			return nil
		}
	}
}

// Stop gracefully shuts down the daemon.
func (d *Daemon) Stop() {
	close(d.stop)
	if d.httpServer != nil {
		d.httpServer.Close()
	}
}

func (d *Daemon) runOnce() {
	pairs, err := d.fetchNewPairs()
	if err != nil {
		log.Printf("⚠️  Fetch pairs: %v", err)
		return
	}

	for _, pair := range pairs {
		if err := d.importPair(pair); err != nil {
			log.Printf("⚠️  Import pair %d: %v", pair.AssistantID, err)
			continue
		}
		d.State.LastAssistantID = pair.AssistantID
		d.State.Imports++
		d.saveState()
		log.Printf("📝 Imported assistant_id=%d session=%s", pair.AssistantID, pair.SessionID)
	}

	// Hourly verify
	hour := time.Now().UTC().Format("2006-01-02T15")
	if hour != d.State.LastVerifyHour && len(pairs) > 0 {
		d.State.LastVerifyHour = hour
		d.saveState()
	}
}

func (d *Daemon) fetchNewPairs() ([]Pair, error) {
	if _, err := os.Stat(d.HermesDB); os.IsNotExist(err) {
		return nil, nil
	}

	db, err := sql.Open("sqlite3", d.HermesDB)
	if err != nil {
		return nil, fmt.Errorf("open hermes: %w", err)
	}
	defer db.Close()

	query := `
		SELECT a.id, a.session_id, a.content, b.content
		FROM messages a
		JOIN messages b ON b.session_id = a.session_id AND b.role = 'assistant' AND b.id > a.id
		WHERE a.role = 'user'
		  AND a.id > ?
		  AND NOT EXISTS (
			SELECT 1 FROM messages c
			WHERE c.session_id = a.session_id
			  AND c.role = 'assistant'
			  AND c.id > a.id AND c.id < b.id
		  )
		ORDER BY a.id
		LIMIT 10
	`

	rows, err := db.Query(query, d.State.LastAssistantID)
	if err != nil {
		return nil, fmt.Errorf("query: %w", err)
	}
	defer rows.Close()

	var pairs []Pair
	for rows.Next() {
		var p Pair
		if err := rows.Scan(&p.AssistantID, &p.SessionID, &p.UserContent, &p.AssistantContent); err != nil {
			continue
		}
		pairs = append(pairs, p)
	}
	return pairs, nil
}

func (d *Daemon) importPair(pair Pair) error {
	// Condense content (same heuristic as Python)
	userText := condenseText(pair.UserContent)
	assistantText := condenseText(pair.AssistantContent)
	session := sessionName(pair.SessionID, userText)
	turnType := classifyContent(userText, assistantText)

	entry := &brain.Entry{
		Type:      turnType,
		Session:   session,
		User:      truncate(userText, 500),
		Assistant: truncate(assistantText, 2000),
	}

	_, err := d.Brain.Append(entry)
	return err
}

func (d *Daemon) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"ok":                true,
		"last_assistant_id": d.State.LastAssistantID,
		"imports":           d.State.Imports,
		"last_verify_hour":  d.State.LastVerifyHour,
	})
}

func (d *Daemon) loadState() {
	data, err := os.ReadFile(d.StatePath)
	if err != nil {
		return
	}
	json.Unmarshal(data, d.State)
}

func (d *Daemon) saveState() {
	os.MkdirAll(filepath.Dir(d.StatePath), 0755)
	data, _ := json.MarshalIndent(d.State, "", "  ")
	os.WriteFile(d.StatePath, data, 0644)
}

// ── Helpers (mirror Python heuristic) ──────────────────────────────────────

func condenseText(text string) string {
	if len(text) <= 2000 {
		return text
	}
	return text[:2000] + "..."
}

func sessionName(sessionID, userText string) string {
	text := stringsToLower(userText)
	if stringsContains(text, "mycelium") {
		return "mycelium-auto"
	}
	if stringsContains(text, "grav") {
		return "grav-auto"
	}
	if stringsContains(text, "page radar") || stringsContains(text, "page-radar") {
		return "page-radar-auto"
	}
	if len(sessionID) >= 8 {
		return "session-" + sessionID[:8]
	}
	return "session-" + sessionID
}

func classifyContent(user, assistant string) string {
	text := stringsToLower(user + " " + assistant)
	if stringsContains(text, "finding") || stringsContains(text, "vulnerability") ||
		stringsContains(text, "bug") || stringsContains(text, "exploit") {
		return "finding"
	}
	if stringsContains(text, "decide") || stringsContains(text, "choice") ||
		stringsContains(text, "prefer") {
		return "decision"
	}
	if stringsContains(text, "idea") || stringsContains(text, "think about") ||
		stringsContains(text, "what if") {
		return "idea"
	}
	return "talk"
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

// avoid importing strings just for Contains/ToLower
func stringsContains(s, substr string) bool { return len(s) >= len(substr) && contains(s, substr) }
func stringsToLower(s string) string {
	b := make([]byte, len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c >= 'A' && c <= 'Z' {
			b[i] = c + 32
		} else {
			b[i] = c
		}
	}
	return string(b)
}
func contains(s, sub string) bool {
	return len(s) >= len(sub) && searchString(s, sub)
}
func searchString(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
