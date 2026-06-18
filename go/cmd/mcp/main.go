// mycelium-mcp — MCP server for mycelium permanent memory.
// Register as a tool in Claude Code settings to query memory from any session.
//
// Usage:
//   mycelium-mcp
//   # In ~/.claude/settings.json:
//   {"mcpServers": {"mycelium": {"command": "mycelium-mcp"}}}
package main

import (
	"flag"
	"log"
	"os"

	"github.com/naufalworks/mycelium/go/pkg/brain"
	"github.com/naufalworks/mycelium/go/pkg/mcp"
)

func main() {
	root := flag.String("root", "", "Mycelium root directory (auto-detect if empty)")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lshortfile)

	b, err := brain.New(*root)
	if err != nil {
		log.Fatalf("❌ Cannot open mycelium: %v", err)
	}

	server := mcp.New(b)
	log.Printf("🧬 Mycelium MCP server starting (brain: %d entries)", b.Count())

	if err := server.ServeStdio(); err != nil {
		log.Fatalf("❌ MCP server error: %v", err)
	}
	os.Exit(0)
}
