// mycelium — unified CLI for mycelium permanent memory.
//
// Commands:
//   status        Brain stats summary
//   verify        Integrity chain check
//   search <q>    Search memory
//   resume        Recent session context
//   precheck      Health checks
//   backup        Create full backup
//   restore <a>   Restore from backup
//   backups       List all backups
//   reindex       Rebuild SQLite index (delegates to Python)
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"

	"github.com/naufalworks/mycelium/go/pkg/brain"
	"github.com/naufalworks/mycelium/go/pkg/cli"
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
		fmt.Fprintf(os.Stderr, "❌ Cannot open mycelium at %s: %v\n", root, err)
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

	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n\n", cmd)
		printUsage()
		os.Exit(1)
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

Environment:
  MYCELIUM_ROOT       Mycelium data directory (auto-detected if not set)
  ANTHROPIC_BASE_URL  Set to http://127.0.0.1:8443 to use the proxy`)
}
