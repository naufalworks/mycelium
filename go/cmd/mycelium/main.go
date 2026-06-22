// mycelium — unified CLI for mycelium permanent memory.
//
// Commands:
//   status            Brain stats summary
//   verify            Integrity chain check
//   search <q>        Search memory
//   resume            Recent session context
//   precheck          Health checks
//   backup            Create full backup
//   restore <a>       Restore from backup
//   backups           List all backups
//   reindex           Rebuild SQLite index (delegates to Python)
//   workflow <sub>    Workflow management (list, run, status, log)
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/naufalworks/mycelium/go/pkg/brain"
	"github.com/naufalworks/mycelium/go/pkg/cli"
	"github.com/naufalworks/mycelium/go/pkg/workflow"
)

func main() {
	log.SetPrefix("")
	log.SetFlags(0)
	flag.Parse()

	// Auto-detect mycelium root
	root := os.Getenv("MYCELIUM_ROOT")
	if root == "" {
		root = brain.DefaultMyceliumDir
	}

	b, err := brain.New(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Cannot open brain: %v\n", err)
		os.Exit(1)
	}

	args := flag.Args()
	if len(args) == 0 {
		printUsage()
		return
	}

	cmd := args[0]
	switch cmd {
	case "status", "stats":
		cli.HandleStatus(b)

	case "verify":
		cli.HandleVerify(b)

	case "search":
		query := ""
		limit := 10
		if len(args) > 1 {
			query = args[1]
		}
		if len(args) > 2 {
			if n, err := strconv.Atoi(args[2]); err == nil {
				limit = n
			}
		}
		cli.HandleSearch(b, query, limit)

	case "resume":
		session := ""
		if len(args) > 1 {
			session = args[1]
		}
		cli.HandleResume(b, session)

	case "precheck", "health":
		cli.HandlePrecheck(b)

	case "backup":
		outDir := ""
		if len(args) > 1 {
			outDir = args[1]
		}
		if err := cli.HandleBackup(b, outDir); err != nil {
			fmt.Fprintf(os.Stderr, "❌ Backup failed: %v\n", err)
			os.Exit(1)
		}

	case "restore":
		if len(args) < 2 {
			fmt.Println("Usage: mycelium restore <backup.tar.gz>")
			os.Exit(1)
		}
		if err := cli.HandleRestore(args[1]); err != nil {
			fmt.Fprintf(os.Stderr, "❌ Restore failed: %v\n", err)
			os.Exit(1)
		}

	case "backups":
		dir := ""
		if len(args) > 1 {
			dir = args[1]
		}
		cli.HandleListBackups(dir)

	case "reindex":
		cli.HandleReindex(b)

	case "workflow":
		handleWorkflow(b, args[1:])

	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n\n", cmd)
		printUsage()
		os.Exit(1)
	}
}

func handleWorkflow(b *brain.Brain, args []string) {
	if len(args) == 0 {
		fmt.Println(`Usage:
  mycelium workflow list
  mycelium workflow run <name>
  mycelium workflow status <run_id>
  mycelium workflow log <run_id>`)
		return
	}

	eng := workflow.New(b.LogPath)
	if eng == nil {
		fmt.Fprintf(os.Stderr, "❌ Failed to create workflow engine\n")
		os.Exit(1)
	}

	sub := args[0]

	switch sub {
	case "list":
		workflows, err := eng.List()
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Error: %v\n", err)
			return
		}
		if len(workflows) == 0 {
			fmt.Println("  No workflows defined.")
			return
		}
		fmt.Printf("\n  %-25s %-6s %s\n", "Name", "Steps", "Description")
		fmt.Printf("  %s\n", strings.Repeat("─", 76))
		for _, w := range workflows {
			desc := w.Description
			if len(desc) > 45 {
				desc = desc[:45]
			}
			fmt.Printf("  %-25s %-6d %s\n", w.Name, len(w.Steps), desc)
		}

	case "run":
		if len(args) < 2 {
			fmt.Println("Usage: mycelium workflow run <name>")
			return
		}
		name := args[1]

		// Set live progress callback
		eng.ProgressFn = func(runID, wfName, stepName string, stepIdx, total int, status string, durMs int) {
			icon := map[string]string{"passed": "✅", "failed": "❌", "running": "▶"}[status]
			dur := ""
			if durMs > 0 {
				dur = fmt.Sprintf(" \x1b[90m(%dms)\x1b[0m", durMs)
			}
			fmt.Printf("  %s %s%s\n", icon, stepName, dur)
		}

		runID, err := eng.Start(name)
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Error: %v\n", err)
			return
		}
		fmt.Printf("  ▶ Workflow '%s' started  (run: %s)\n\n", name, runID[:16])

		// Poll for completion
		for {
			time.Sleep(1 * time.Second)
			state, err := eng.Status(runID)
			if err != nil {
				continue
			}
			if state.Status == "done" {
				passed := 0
				for _, sr := range state.StepResults {
					if sr.Status == "passed" {
						passed++
					}
				}
				fmt.Printf("\n  \x1b[32m✅ All %d steps passed\x1b[0m\n", passed)
				return
			}
			if state.Status == "failed" {
				fmt.Printf("\n  \x1b[31m❌ Workflow failed: %s\x1b[0m\n", state.Error)
				return
			}
		}

	case "status":
		if len(args) < 2 {
			fmt.Println("Usage: mycelium workflow status <run_id>")
			return
		}
		state, err := eng.Status(args[1])
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Error: %v\n", err)
			return
		}
		fmt.Printf("\n  \x1b[1mWorkflow:\x1b[0m %s\n", state.Workflow)
		fmt.Printf("  \x1b[1mRun ID:\x1b[0m   %s\n", state.ID)
		fmt.Printf("  \x1b[1mStatus:\x1b[0m   ")
		switch state.Status {
		case "done":
			fmt.Println("\x1b[32m✅ done\x1b[0m")
		case "running":
			fmt.Println("\x1b[36m▶ running\x1b[0m")
		case "failed":
			fmt.Println("\x1b[31m❌ failed\x1b[0m")
		default:
			fmt.Println(state.Status)
		}
		if state.Error != "" {
			fmt.Printf("  \x1b[31mError: %s\x1b[0m\n", state.Error)
		}
		fmt.Printf("\n  \x1b[1mSteps (%d/%d):\x1b[0m\n", state.CurrentStep, len(state.StepResults))
		for _, sr := range state.StepResults {
			icon := map[string]string{"passed": "✅", "failed": "❌", "running": "▶", "pending": "☐"}[sr.Status]
			dur := ""
			if sr.DurationMs > 0 {
				dur = fmt.Sprintf(" \x1b[90m(%dms)\x1b[0m", sr.DurationMs)
			}
			fmt.Printf("  %s %s%s\n", icon, sr.Name, dur)
		}

	case "log":
		if len(args) < 2 {
			fmt.Println("Usage: mycelium workflow log <run_id>")
			return
		}
		logStr, err := eng.Log(args[1])
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Error: %v\n", err)
			return
		}
		fmt.Println(logStr)

	default:
		fmt.Printf("Unknown workflow subcommand: %s\n", sub)
		fmt.Println("Try: list, run, status, log")
	}
}

func printUsage() {
	fmt.Println(`🍄 mycelium — permanent memory management

Usage:
  mycelium <command> [options]

Commands:
  status              Brain stats summary
  verify              Integrity chain check
  search <query>      Search across all memory
  resume [session]    Recent context for session resumption
  precheck            Run health checks
  backup [dir]        Create full backup
  restore <archive>   Restore from backup archive
  backups [dir]       List available backups
  reindex             Rebuild SQLite index (delegates to Python)
  workflow <sub>      Workflow management (list, run, status, log)

Environment:
  MYCELIUM_ROOT       Mycelium data directory (auto-detected if not set)
  ANTHROPIC_BASE_URL  Set to http://127.0.0.1:8443 to use the proxy`)
}
