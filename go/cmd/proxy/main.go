// mycelium-proxy — Intercepts Claude Code API calls, logs to mycelium,
// injects past context, and preserves permanent memory.
//
// Usage:
//   export ANTHROPIC_BASE_URL=http://127.0.0.1:8443
//   mycelium-proxy &
//   claude
package main

import (
	"flag"
	"fmt"
	"log"
	"os"

	"github.com/naufalworks/mycelium/go/pkg/brain"
	"github.com/naufalworks/mycelium/go/pkg/proxy"
)

func main() {
	port := flag.String("port", proxy.DefaultPort, "Listen port")
	root := flag.String("root", "", "Mycelium root directory (auto-detect if empty)")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lshortfile)
	log.Printf("🧬 Mycelium Proxy starting...")

	b, err := brain.New(*root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Failed to open mycelium: %v\n", err)
		os.Exit(1)
	}

	count := b.Count()
	log.Printf("📊 Mycelium brain: %d entries at %s", count, b.LogPath)

	p := proxy.New(b)
	p.Port = *port

	fmt.Printf(`
🧬 Mycelium Proxy active on 127.0.0.1:%s
   Set ANTHROPIC_BASE_URL=http://127.0.0.1:%s to route Claude Code through it.
   Brain: %s (%d entries)

`, *port, *port, b.LogPath, count)

	if err := p.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "❌ Proxy error: %v\n", err)
		os.Exit(1)
	}
}
