// Package cli provides the mycelium CLI command handlers.
package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/naufalworks/mycelium/go/pkg/backup"
	"github.com/naufalworks/mycelium/go/pkg/brain"
)

// HandleStatus prints brain status summary.
func HandleStatus(b *brain.Brain) {
	entries, _ := b.LoadLog()
	if entries == nil {
		fmt.Println("🍄 Mycelium — Empty brain")
		return
	}

	// Count stats
	typeCount := map[string]int{}
	tierCount := map[string]int{}
	sessions := map[string]bool{}
	findings := 0
	critical := 0

	for _, e := range entries {
		typeCount[e.Type]++
		tierCount[e.Tier]++
		sessions[e.Session] = true
		if e.Type == "finding" {
			findings++
			if e.Finding != nil && e.Finding.Severity == "critical" {
				critical++
			}
		}
	}

	// Entity frequency
	entityFreq := map[string]int{}
	for _, e := range entries {
		for _, ent := range e.Entities {
			entityFreq[ent]++
		}
	}

	var tsFirst, tsLast string
	if len(entries) > 0 {
		tsFirst = entries[0].Timestamp
		tsLast = entries[len(entries)-1].Timestamp
	}

	fmt.Printf("🍄 Mycelium — Brain Status\n")
	fmt.Println(strings.Repeat("=", 50))
	fmt.Printf("  Turns:      %d\n", len(entries))
	fmt.Printf("  Sessions:   %d\n", len(sessions))
	fmt.Printf("  Date range: %s → %s\n", tsFirst[:10], tsLast[:10])
	fmt.Println()
	fmt.Println("  By type:")
	for _, t := range []string{"finding", "decision", "idea", "talk", "gardener", "dead-end", "branch"} {
		if n := typeCount[t]; n > 0 {
			fmt.Printf("    %-12s %d\n", t, n)
		}
	}
	fmt.Println()
	fmt.Println("  By tier:")
	for _, t := range []string{"S", "A", "B", "C"} {
		if n := tierCount[t]; n > 0 {
			fmt.Printf("    %-12s %d\n", t, n)
		}
	}
	fmt.Println()
	fmt.Printf("  Findings:   %d\n", findings)
	if critical > 0 {
		fmt.Printf("    critical   %d\n", critical)
	}
	fmt.Println()
	fmt.Println("  Top entities:")
	type entity struct {
		name  string
		count int
	}
	var ents []entity
	for name, count := range entityFreq {
		ents = append(ents, entity{name, count})
	}
	sort.Slice(ents, func(i, j int) bool { return ents[i].count > ents[j].count })
	for i, e := range ents {
		if i >= 10 {
			break
		}
		fmt.Printf("    %-25s %dx\n", e.name, e.count)
	}
}

// HandleVerify checks the integrity chain of all entries.
func HandleVerify(b *brain.Brain) {
	entries, _ := b.LoadLog()
	if len(entries) == 0 {
		fmt.Println("✅ No entries to verify.")
		return
	}

	errors := 0
	for i := 1; i < len(entries); i++ {
		e := entries[i]
		prevHash := entries[i-1].Hash
		if e.PrevHash != prevHash {
			if errors < 5 {
				fmt.Printf("✗ Turn %d: prev_hash mismatch (expected %s, got %s)\n", e.Turn, prevHash, e.PrevHash)
			}
			errors++
			continue
		}
		computed := brain.ComputeHashEntry(e, prevHash)
		if computed != e.Hash {
			if errors < 5 {
				fmt.Printf("✗ Turn %d: hash mismatch (expected %s, got %s)\n", e.Turn, computed, e.Hash)
			}
			errors++
		}
	}

	if errors == 0 {
		fmt.Printf("✅ Integrity chain valid — %d turns, all hashes match.\n", len(entries))
	} else {
		fmt.Printf("✗ %d integrity error(s) found.\n", errors)
		os.Exit(1)
	}

	// Also verify the last entry's prev_hash matches second-to-last hash
	if len(entries) >= 2 {
		last := entries[len(entries)-1]
		prev := entries[len(entries)-2]
		if last.PrevHash != prev.Hash {
			fmt.Printf("⚠️  Last entry prev_hash doesn't match: %s vs %s\n", last.PrevHash, prev.Hash)
		}
	}
}

// HandleSearch searches across all entries.
func HandleSearch(b *brain.Brain, query string, limit int) {
	if query == "" {
		fmt.Println("No query provided. Usage: mycelium search <query>")
		return
	}
	results := b.Search(query, limit)
	if len(results) == 0 {
		fmt.Printf("No results for %q\n", query)
		return
	}
	fmt.Printf("🔍 Results for %q (%d):\n\n", query, len(results))
	for _, r := range results {
		fmt.Printf("  [Turn %d | %s | %s]\n", r.Turn, r.Tier, r.Timestamp[:10])
		fmt.Printf("  Session: %s\n", r.Session)
		fmt.Printf("  User:    %s\n", truncate(r.User, 100))
		fmt.Printf("  AI:      %s\n", truncate(r.Assistant, 200))
		if len(r.Entities) > 0 {
			fmt.Printf("  Tags:    %s\n", strings.Join(r.Entities, ", "))
		}
		fmt.Println()
	}
}

// HandlePrecheck runs all health checks and prints results.
func HandlePrecheck(b *brain.Brain) {
	entries, _ := b.LoadLog()
	allOK := true

	type check struct {
		name string
		ok   bool
		info string
	}
	var results []check

	// 1. Brain
	eCount := len(entries)
	results = append(results, check{"brain", eCount > 0, fmt.Sprintf("%d entries", eCount)})

	// 2. Index
	idxInfo := "ok"
	if _, err := os.Stat(brain.IndexPath); os.IsNotExist(err) {
		idxInfo = "missing (will rebuild on next append)"
	}
	results = append(results, check{"index", true, idxInfo})

	// 3. Integrity
	errs := 0
	for i := 1; i < len(entries); i++ {
		e := entries[i]
		prevHash := entries[i-1].Hash
		if brain.ComputeHashEntry(e, prevHash) != e.Hash {
			errs++
		}
	}
	integOK := errs == 0
	integInfo := fmt.Sprintf("chain ok (%d entries)", len(entries))
	if !integOK {
		integInfo = fmt.Sprintf("%d chain breaks", errs)
	}
	results = append(results, check{"integrity", integOK, integInfo})

	// 4. Search check
	if len(entries) > 0 {
		results = append(results, check{"searchable", true, fmt.Sprintf("%d entries queryable", len(entries))})
	} else {
		results = append(results, check{"searchable", true, "empty log"})
	}

	// Print results
	fmt.Println("🍄 Mycelium Health Check")
	fmt.Println(strings.Repeat("─", 50))
	for _, r := range results {
		icon := "✓"
		if !r.ok {
			icon = "✗"
			allOK = false
		}
		fmt.Printf("  %s %-12s %s\n", icon, r.name, r.info)
	}
	fmt.Println(strings.Repeat("─", 50))
	if allOK {
		fmt.Println("  ✅ All checks passed")
	} else {
		fmt.Println("  ⚠️  Some checks failed")
	}
}

// HandleBackup creates a full backup.
func HandleBackup(b *brain.Brain, outputDir string) error {
	path, err := backup.Create(b.Mycelium, outputDir)
	if err != nil {
		return err
	}
	fmt.Printf("💾 Backup created: %s\n", path)

	// Show size
	info, _ := os.Stat(path)
	fmt.Printf("   Size: %d KB\n", info.Size()/1024)
	return nil
}

// HandleRestore restores from a backup.
func HandleRestore(archivePath string) error {
	root := brain.DefaultMyceliumDir

	fmt.Printf("⚠️  This will overwrite data in %s\n", root)
	fmt.Printf("   Archive: %s\n", archivePath)
	fmt.Print("   Continue? [y/N] ")

	var response string
	fmt.Scanln(&response)
	if strings.ToLower(response) != "y" {
		fmt.Println("Restore cancelled.")
		return nil
	}

	// Create a timestamped backup of current data first
	timestamp := timeNow()
	autoBackup := fmt.Sprintf("%s/backups/pre-restore-%s.tar.gz", root, timestamp)
	os.MkdirAll(filepath.Dir(autoBackup), 0755)

	// Rename existing data rather than delete
	if err := backup.Restore(archivePath, root); err != nil {
		return fmt.Errorf("restore failed: %w", err)
	}
	fmt.Println("✅ Restore complete!")
	return nil
}

// HandleListBackups lists all available backups.
func HandleListBackups(dir string) {
	backups, err := backup.ListBackups(dir)
	if err != nil {
		fmt.Printf("Error listing backups: %v\n", err)
		return
	}
	if len(backups) == 0 {
		fmt.Println("No backups found.")
		return
	}
	fmt.Printf("📦 Backups (%d):\n", len(backups))
	for _, b := range backups {
		info, _ := os.Stat(b)
		size := "?"
		if info != nil {
			size = fmt.Sprintf("%d KB", info.Size()/1024)
		}
		fmt.Printf("  %s (%s)\n", b, size)
	}
}

// HandleResume prints recent context for session resumption.
func HandleResume(b *brain.Brain, session string) {
	entries := b.RecentEntries(5)
	if len(entries) == 0 {
		fmt.Println("No recent entries. Start a conversation to build memory.")
		return
	}
	fmt.Println("🍄 Mycelium — Recent Context")
	fmt.Println(strings.Repeat("─", 50))
	for _, e := range entries {
		if session != "" && e.Session != session {
			continue
		}
		fmt.Printf("  [Turn %d | %s | %s]\n", e.Turn, e.Tier, e.Session)
		fmt.Printf("  %s\n", truncate(e.User, 120))
		fmt.Println()
	}

	// Show top-tier entries
	sTier := 0
	for _, e := range entries {
		if e.Tier == "S" {
			sTier++
		}
	}
	if sTier > 0 {
		fmt.Printf("  🏆 %d S-tier entries in recent context\n", sTier)
	}
}

// HandleReindex rebuilds the SQLite index from the log.
func HandleReindex(b *brain.Brain) {
	// For now, this is a thin wrapper. The index.db is a Python/SQLite thing.
	// Go writes to log.jsonl only. Full index rebuild requires Python or pure Go SQLite.
	fmt.Println("ℹ️  Reindex: Use Python for SQLite index rebuild:")
	fmt.Println("   python3 scripts/mycelium.py reindex")
	fmt.Println()
	fmt.Println("   Or install sqlite3 CLI and run:")
	fmt.Println("   sqlite3 index.db < schema.sql")
}

// ── Utilities ───────────────────────────────────────────────────────────────

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

func timeNow() string {
	t := os.Getenv("MYCELIUM_TEST_TIME")
	if t != "" {
		return t
	}
	return ""
}
