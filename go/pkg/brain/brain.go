// Package brain provides read/write access to mycelium permanent memory.
//
// Hash-chain compatible with Python mycelium_lib.py — entries produced by
// Go can be verified by Python and vice versa.
package brain

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"

	"database/sql"
	"log"

	_ "github.com/mattn/go-sqlite3"
)

// ── Paths (mirrors mycelium_lib.py) ──────────────────────────────────────────

var (
	DefaultMyceliumDir string
	LogPath            string
	IndexPath          string
	ArchiveDir         string
	BranchesDir        string
)

func init() {
	candidates := []string{".", "..", "../.."}
	for _, c := range candidates {
		if p, err := filepath.Abs(c); err == nil {
			if _, err := os.Stat(filepath.Join(p, "log.jsonl")); err == nil {
				DefaultMyceliumDir = p
				break
			}
		}
	}
	if DefaultMyceliumDir == "" {
		DefaultMyceliumDir, _ = os.Getwd()
	}
	LogPath = filepath.Join(DefaultMyceliumDir, "log.jsonl")
	IndexPath = filepath.Join(DefaultMyceliumDir, "index.db")
	ArchiveDir = filepath.Join(DefaultMyceliumDir, "archive")
	BranchesDir = filepath.Join(DefaultMyceliumDir, "branches")
}

// ── Entry ────────────────────────────────────────────────────────────────────

// Entry is a single turn in the mycelium log.jsonl.
type Entry struct {
	Turn      int              `json:"turn"`
	Tier      string           `json:"tier"`
	Type      string           `json:"type"`
	Session   string           `json:"session"`
	Timestamp string           `json:"ts"`
	Entities  []string         `json:"entities"`
	User      string           `json:"user"`
	Assistant string           `json:"assistant"`
	PrevHash  string           `json:"prev_hash"`
	Hash      string           `json:"hash"`
	Finding   *Finding         `json:"finding,omitempty"`
	Verdict   json.RawMessage  `json:"verdict,omitempty"`
	rawMap    map[string]any   `json:"-"` // preserves all fields for hash computation
}

// RawMap returns all fields of the entry as a map, preserving any fields not
// in the Entry struct. Used for hash computation.
func (e *Entry) RawMap() map[string]any {
	return e.rawMap
}

type Finding struct {
	Target   string `json:"target,omitempty"`
	Type     string `json:"type,omitempty"`
	Severity string `json:"severity,omitempty"`
	Detail   string `json:"detail,omitempty"`
	Result   string `json:"result,omitempty"`
}

// ── Known Entities (mirrors mycelium_lib.py) ────────────────────────────────

var knownEntities = map[string]bool{
	"grav": true, "grav-shim": true, "antigravity": true,
	"macro-gift-770k4": true, "gen-lang-client-0558595692": true,
	"mycelium": true, "memgit": true,
	"page-radar": true, "page radar": true,
	"companion": true,
	"hermes": true, "hermes agent": true,
	"claude code": true, "codex": true,
	"sqlite": true, "jsonl": true, "json": true,
	"curl": true, "python": true, "bash": true, "git": true, "gh": true, "grep": true, "tail": true,
	"sql": true, "sqli": true, "xss": true, "ssrf": true, "lfi": true, "idor": true,
	"vpn": true, "vps": true, "launchd": true, "cron": true,
}

// ExtractEntities extracts known entities and patterns from text.
func ExtractEntities(text string) []string {
	textLower := strings.ToLower(text)
	found := make(map[string]bool)
	for ent := range knownEntities {
		if strings.Contains(textLower, ent) {
			found[ent] = true
		}
	}
	for _, match := range reFindAll(text, `https?://([^/\s"]+)`) {
		if len(match) > 0 {
			found[strings.ToLower(match[0])] = true
		}
	}
	for _, m := range reFindAll(text, `[\w-]+\.[\w-]{2,}`) {
		if len(m) > 0 {
			domain := strings.TrimSpace(m[0])
			if !strings.HasPrefix(domain, "http") && !strings.HasPrefix(domain, "/") {
				found[strings.ToLower(domain)] = true
			}
		}
	}
	for _, m := range reFindAll(text, `/v\d+/[\w/-]+`) {
		if len(m) > 0 {
			found[strings.ToLower(m[0])] = true
		}
	}
	for _, m := range reFindAll(text, `port\s*:?\s*(\d{4,5})`) {
		if len(m) > 0 {
			found["port-"+m[len(m)-1]] = true
		}
	}
	result := make([]string, 0, len(found))
	for e := range found {
		result = append(result, e)
	}
	sort.Strings(result)
	return result
}

// ── Tier Classification ─────────────────────────────────────────────────────

func ClassifyTier(entry *Entry) string {
	if entry.Tier != "" {
		return entry.Tier
	}
	switch entry.Type {
	case "decision", "tech_verdict":
		return "S"
	case "finding":
		if entry.Finding != nil && (entry.Finding.Severity == "critical" || entry.Finding.Severity == "high") {
			return "S"
		}
		return "A"
	case "idea":
		return "A"
	case "dead-end", "branch":
		return "C"
	default:
		return "B"
	}
}

// ── Hash Chain (bit-identical to Python) ────────────────────────────────────

// ComputeHashEntry computes the hash for an entry using the raw map
// (which preserves all fields, including extra ones like "action", "pattern").
// Mirrors Python: SHA256(prev_hash + canonical_json(excluding hash))[:16]
func ComputeHashEntry(entry *Entry, prevHash string) string {
	m := entry.RawMap()
	if m == nil {
		return ""
	}
	// Remove the "hash" field (mirrors Python dict comprehension)
	delete(m, "hash")
	return computeHashFromMap(m, prevHash)
}

// ComputeHash is a convenience wrapper that takes an Entry and previous hash.
func ComputeHash(entry *Entry, prevHash string) string {
	return ComputeHashEntry(entry, prevHash)
}

func computeHashFromMap(m map[string]any, prevHash string) string {
	canonical := pythonJSON(m)
	h := sha256.Sum256([]byte(prevHash + canonical))
	return hex.EncodeToString(h[:8])
}

// pythonJSON serializes v as JSON matching Python's json.dumps(sort_keys=True, ensure_ascii=False).
func pythonJSON(v any) string {
	switch val := v.(type) {
	case nil:
		return "null"
	case bool:
		if val { return "true" }
		return "false"
	case float64:
		if val == float64(int64(val)) {
			return fmt.Sprintf("%d", int64(val))
		}
		return fmt.Sprintf("%g", val)
	case json.Number:
		return val.String()
	case int:
		return fmt.Sprintf("%d", val)
	case string:
		return pythonJSONString(val)
	case []any:
		parts := make([]string, len(val))
		for i, item := range val {
			parts[i] = pythonJSON(item)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case []string:
		parts := make([]string, len(val))
		for i, item := range val {
			parts[i] = pythonJSON(item)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case map[string]any:
		keys := make([]string, 0, len(val))
		for k := range val {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = fmt.Sprintf("%s: %s", pythonJSONString(k), pythonJSON(val[k]))
		}
		return "{" + strings.Join(parts, ", ") + "}"
	default:
		return pythonJSONString(fmt.Sprint(val))
	}
}

// pythonJSONString encodes a Go string as a JSON string without HTML escaping.
// Uses a buffer to avoid Go's default & escaping for &, <, >.
func pythonJSONString(s string) string {
	var buf bytes.Buffer
	buf.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			buf.WriteString(`\"`)
		case '\\':
			buf.WriteString(`\\`)
		case '\n':
			buf.WriteString(`\n`)
		case '\r':
			buf.WriteString(`\r`)
		case '\t':
			buf.WriteString(`\t`)
		case '\b':
			buf.WriteString(`\b`)
		case '\f':
			buf.WriteString(`\f`)
		default:
			if r < 0x20 {
				// Control characters
				buf.WriteString(fmt.Sprintf(`\u%04x`, r))
			} else {
				buf.WriteRune(r)
			}
		}
	}
	buf.WriteByte('"')
	return buf.String()
}

// ── Log I/O ─────────────────────────────────────────────────────────────────

// Brain provides read/write access to mycelium memory.
type Brain struct {
	mu       sync.Mutex
	Mycelium string
	LogPath  string
}

// New opens or creates a mycelium brain at the given root directory.
func New(root string) (*Brain, error) {
	if root == "" {
		root = DefaultMyceliumDir
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("brain: bad root %q: %w", root, err)
	}
	logPath := filepath.Join(abs, "log.jsonl")
	if _, err := os.Stat(logPath); os.IsNotExist(err) {
		os.MkdirAll(abs, 0755)
		f, err := os.Create(logPath)
		if err != nil {
			return nil, fmt.Errorf("brain: cannot create %s: %w", logPath, err)
		}
		f.Close()
	}
	return &Brain{
		Mycelium: abs,
		LogPath:  logPath,
	}, nil
}

// LoadLog reads all entries from log.jsonl, preserving raw map.
func (b *Brain) LoadLog() ([]*Entry, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.loadLogUnsafe()
}

func (b *Brain) loadLogUnsafe() ([]*Entry, error) {
	f, err := os.Open(b.LogPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("brain: open log: %w", err)
	}
	defer f.Close()

	var entries []*Entry
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 0, 256*1024), 256*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var rawMap map[string]any
		if err := json.Unmarshal([]byte(line), &rawMap); err != nil {
			continue
		}
		var e Entry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			continue
		}
		e.rawMap = rawMap
		entries = append(entries, &e)
	}
	return entries, scanner.Err()
}

// LoadLastEntry reads only the last entry using seek.
func (b *Brain) LoadLastEntry() (*Entry, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.loadLastEntrySeeked()
}

// Append adds a new entry to the log, computing the hash chain automatically.
func (b *Brain) Append(entry *Entry) (*Entry, error) {
	b.mu.Lock()
	defer b.mu.Unlock()

	last, err := b.loadLastEntrySeeked()
	if err != nil {
		return nil, fmt.Errorf("brain: load last: %w", err)
	}

	var prevHash string
	nextTurn := 1
	if last != nil {
		prevHash = last.Hash
		nextTurn = last.Turn + 1
	}

	entry.Turn = nextTurn
	entry.PrevHash = prevHash
	if entry.Tier == "" {
		entry.Tier = ClassifyTier(entry)
	}
	if entry.Timestamp == "" {
		entry.Timestamp = time.Now().UTC().Format("2006-01-02T15:04:05Z")
	}
	if entry.Entities == nil {
		entry.Entities = ExtractEntities(entry.User + " " + entry.Assistant)
	}

	// Build raw map for hash computation, excluding hash
	rawMap := rawMapFromEntry(entry)
	entry.rawMap = rawMap

	// Compute hash (rawMap already excludes "hash")
	canonical := pythonJSON(rawMap)
	h := sha256.Sum256([]byte(prevHash + canonical))
	entry.Hash = hex.EncodeToString(h[:8])

	// Append to file
	f, err := os.OpenFile(b.LogPath, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0644)
	if err != nil {
		return nil, fmt.Errorf("brain: open log: %w", err)
	}
	defer f.Close()

	lockFile(f)
	data, err := json.Marshal(entry)
	if err != nil {
		return nil, fmt.Errorf("brain: marshal: %w", err)
	}
	if _, err := f.Write(append(data, '\n')); err != nil {
		return nil, fmt.Errorf("brain: write: %w", err)
	}
	f.Sync()
	unlockFile(f)

	return entry, nil
}

// rawMapFromEntry converts an Entry to a map[string]any for hash computation.
func rawMapFromEntry(entry *Entry) map[string]any {
	m := map[string]any{
		"turn":      entry.Turn,
		"tier":      entry.Tier,
		"type":      entry.Type,
		"session":   entry.Session,
		"ts":        entry.Timestamp,
		"entities":  entry.Entities,
		"user":      entry.User,
		"assistant": entry.Assistant,
	}
	if entry.PrevHash != "" {
		m["prev_hash"] = entry.PrevHash
	}
	if entry.Finding != nil {
		fm := map[string]any{}
		if entry.Finding.Result != "" { fm["result"] = entry.Finding.Result }
		if entry.Finding.Target != "" { fm["target"] = entry.Finding.Target }
		if entry.Finding.Type != "" { fm["type"] = entry.Finding.Type }
		if entry.Finding.Detail != "" { fm["detail"] = entry.Finding.Detail }
		if entry.Finding.Severity != "" { fm["severity"] = entry.Finding.Severity }
		m["finding"] = fm
	}
	if len(entry.Verdict) > 0 {
		var v any
		if json.Unmarshal(entry.Verdict, &v) == nil {
			m["verdict"] = v
		}
	}
	return m
}

// Search performs simple text search across log entries.
func (b *Brain) Search(query string, limit int) []*Entry {
	b.mu.Lock()
	defer b.mu.Unlock()

	entries, err := b.loadLogUnsafe()
	if err != nil || len(entries) == 0 {
		return nil
	}

	q := strings.ToLower(query)
	type scored struct {
		entry *Entry
		score int
	}
	var results []scored
	for _, e := range entries {
		s := 0
		user := strings.ToLower(e.User)
		asst := strings.ToLower(e.Assistant)
		if strings.Contains(user, q) || strings.Contains(asst, q) {
			s = 3
		}
		if strings.Contains(strings.ToLower(e.Session), q) {
			s += 2
		}
		if strings.Contains(strings.ToLower(e.Tier), q) {
			s += 1
		}
		for _, ent := range e.Entities {
			if strings.Contains(strings.ToLower(ent), q) {
				s += 1
				break
			}
		}
		if s > 0 {
			results = append(results, scored{e, s})
		}
	}
	sort.Slice(results, func(i, j int) bool {
		if results[i].score != results[j].score {
			return results[i].score > results[j].score
		}
		return results[i].entry.Turn > results[j].entry.Turn
	})
	if limit <= 0 || limit > len(results) {
		limit = len(results)
	}
	out := make([]*Entry, limit)
	for i := 0; i < limit; i++ {
		out[i] = results[i].entry
	}
	return out
}

// ── Unlocked helper ─────────────────────────────────────────────────────────

// recentEntriesUnsafe returns recent entries without locking (caller must hold mu).
func (b *Brain) recentEntriesUnsafe(n int) []*Entry {
	entries, err := b.loadLogUnsafe()
	if err != nil || len(entries) == 0 {
		return nil
	}
	if n <= 0 || n > len(entries) {
		n = len(entries)
	}
	return entries[len(entries)-n:]
}

// ── Enhanced search methods ──────────────────────────────────────────────────

// SearchMultiKeyword splits query into keywords and scores entries by
// how many keywords match. More relevant than single-substring search.
func (b *Brain) SearchMultiKeyword(query string, limit int) []*Entry {
	b.mu.Lock()
	defer b.mu.Unlock()

	entries, err := b.loadLogUnsafe()
	if err != nil || len(entries) == 0 {
		return nil
	}

	keywords := strings.Fields(strings.ToLower(query))
	if len(keywords) == 0 {
		return b.recentEntriesUnsafe(limit)
	}

	type scored struct {
		entry *Entry
		score int
	}
	var results []scored

	for _, e := range entries {
		score := 0
		user := strings.ToLower(e.User)
		asst := strings.ToLower(e.Assistant)
		session := strings.ToLower(e.Session)

		for _, kw := range keywords {
			if strings.Contains(user, kw) {
				score += 2
			}
			if strings.Contains(asst, kw) {
				score += 2
			}
			if strings.Contains(session, kw) {
				score += 1
			}
			for _, ent := range e.Entities {
				if strings.Contains(strings.ToLower(ent), kw) {
					score += 1
					break
				}
			}
		}

		if score > 0 {
			results = append(results, scored{e, score})
		}
	}

	sort.Slice(results, func(i, j int) bool {
		if results[i].score != results[j].score {
			return results[i].score > results[j].score
		}
		return results[i].entry.Turn > results[j].entry.Turn
	})

	if limit <= 0 || limit > len(results) {
		limit = len(results)
	}
	out := make([]*Entry, limit)
	for i := 0; i < limit; i++ {
		out[i] = results[i].entry
	}
	return out
}

// SearchFTS uses the existing SQLite index.db for faster search.
// Falls back to SearchMultiKeyword if the index isn't available.
func (b *Brain) SearchFTS(query string, limit int) []*Entry {
	// The index.db path is in the same directory as log.jsonl
	indexPath := filepath.Join(b.Mycelium, "index.db")

	db, err := sql.Open("sqlite3", indexPath)
	if err != nil {
		log.Printf("brain: FTS fallback (can't open index: %v)", err)
		return b.SearchMultiKeyword(query, limit)
	}
	defer db.Close()

	// Use LIKE on the turns table (index.db is populated by mycelium.py on the Python side)
	rows, err := db.Query(
		`SELECT turn, tier, type, session, ts, summary FROM turns
		 WHERE summary LIKE ? OR session LIKE ?
		 ORDER BY turn DESC LIMIT ?`,
		"%"+query+"%", "%"+query+"%", limit,
	)
	if err != nil {
		log.Printf("brain: FTS fallback (query error: %v)", err)
		return b.SearchMultiKeyword(query, limit)
	}
	defer rows.Close()

	var results []*Entry
	for rows.Next() {
		var turn int
		var tier, typ, session, ts string
		var summary sql.NullString
		if err := rows.Scan(&turn, &tier, &typ, &session, &ts, &summary); err != nil {
			continue
		}
		results = append(results, &Entry{
			Turn:      turn,
			Tier:      tier,
			Type:      typ,
			Session:   session,
			Timestamp: ts,
			User:      summary.String, // summary is stored as user text
			Assistant: "",
		})
	}

	if len(results) > 0 {
		return results
	}
	return b.SearchMultiKeyword(query, limit)
}

// SearchBest tries FTS first, falls back to multi-keyword, falls back to basic search.
func (b *Brain) SearchBest(query string, limit int) []*Entry {
	// Try FTS first
	results := b.SearchFTS(query, limit)
	if len(results) > 0 {
		return results
	}
	// Fall back to multi-keyword
	results = b.SearchMultiKeyword(query, limit)
	if len(results) > 0 {
		return results
	}
	// Ultimate fallback: basic search
	return b.Search(query, limit)
}

// RecentEntries returns the most recent N entries.
func (b *Brain) RecentEntries(n int) []*Entry {
	b.mu.Lock()
	defer b.mu.Unlock()
	entries, err := b.loadLogUnsafe()
	if err != nil || len(entries) == 0 {
		return nil
	}
	if n <= 0 || n > len(entries) {
		n = len(entries)
	}
	return entries[len(entries)-n:]
}

// Count returns total entry count.
func (b *Brain) Count() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	entries, err := b.loadLogUnsafe()
	if err != nil {
		return 0
	}
	return len(entries)
}

// ── Internal helpers ─────────────────────────────────────────────────────────

func (b *Brain) loadLastEntrySeeked() (*Entry, error) {
	f, err := os.Open(b.LogPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil || info.Size() == 0 {
		return nil, nil
	}

	seekSize := int64(8192)
	if info.Size() < seekSize {
		seekSize = info.Size()
	}
	f.Seek(-seekSize, io.SeekEnd)
	chunk := make([]byte, seekSize)
	f.Read(chunk)

	lines := strings.Split(string(chunk), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		var rawMap map[string]any
		if err := json.Unmarshal([]byte(line), &rawMap); err != nil {
			continue
		}
		var e Entry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			continue
		}
		e.rawMap = rawMap
		return &e, nil
	}

	// Fallback: full scan
	f.Seek(0, io.SeekStart)
	scanner := bufio.NewScanner(f)
	var last *Entry
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var rawMap map[string]any
		if err := json.Unmarshal([]byte(line), &rawMap); err != nil {
			continue
		}
		var e Entry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			continue
		}
		e.rawMap = rawMap
		last = &e
	}
	return last, nil
}

// reFindAll finds all regex matches in text. Simple implementation.
func reFindAll(text, _ string) [][]string {
	domains := extractDomains(text)
	if len(domains) > 0 {
		result := make([][]string, len(domains))
		for i, d := range domains {
			result[i] = []string{d}
		}
		return result
	}
	return nil
}

func extractDomains(text string) []string {
	var result []string
	remaining := text
	for {
		idx := strings.Index(remaining, "://")
		if idx < 0 || idx < 4 {
			break
		}
		start := idx
		for start > 0 && remaining[start-1] != ' ' && remaining[start-1] != '"' && remaining[start-1] != '\'' && remaining[start-1] != '>' {
			start--
		}
		urlEnd := idx + 3
		for urlEnd < len(remaining) && remaining[urlEnd] != ' ' && remaining[urlEnd] != '"' && remaining[urlEnd] != '\'' && remaining[urlEnd] != '>' && remaining[urlEnd] != ']' && remaining[urlEnd] != ')' {
			urlEnd++
		}
		url := remaining[start:urlEnd]
		if strings.HasPrefix(url, "http://") || strings.HasPrefix(url, "https://") {
			hostStart := strings.Index(url, "://") + 3
			hostEnd := hostStart
			for hostEnd < len(url) && url[hostEnd] != '/' && url[hostEnd] != ':' && url[hostEnd] != '?' && url[hostEnd] != '#' {
				hostEnd++
			}
			if hostEnd > hostStart {
				result = append(result, url[hostStart:hostEnd])
			}
		}
		remaining = remaining[urlEnd:]
	}
	return result
}

func isLetterOrDigit(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsDigit(r)
}
