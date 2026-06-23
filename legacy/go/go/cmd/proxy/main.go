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
	upstream := flag.String("upstream", proxy.DefaultUpstream, "Upstream API URL")
	root := flag.String("root", "", "Mycelium root directory")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lshortfile)

	b, err := brain.New(*root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Failed to open mycelium: %v\n", err)
		os.Exit(1)
	}

	p := proxy.New(b)
	p.Port = *port
	p.Upstream = *upstream

	fmt.Printf("🧬 Mycelium Proxy → %s\n", p.Upstream)
	fmt.Printf("   Listening on :%s\n", p.Port)
	fmt.Printf("   Brain: %s (%d entries)\n", b.LogPath, b.Count())
	fmt.Println()

	if err := p.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "❌ Proxy error: %v\n", err)
		os.Exit(1)
	}
}
