// myceliumd — persistent daemon for mycelium safety-net imports.
//
// Polls Hermes state.db for completed conversation pairs and imports
// them into mycelium automatically. Also runs the mycelium proxy
// (meshgate reverse-proxy) for context injection. Runs as a background service.
package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/naufalworks/mycelium/go/pkg/brain"
	"github.com/naufalworks/mycelium/go/pkg/daemon"
	"github.com/naufalworks/mycelium/go/pkg/proxy"
)

func main() {
	proxyPort := flag.String("proxy-port", proxy.DefaultPort, "Proxy listen port")
	proxyUpstream := flag.String("proxy-upstream", proxy.DefaultUpstream, "Proxy upstream URL")
	root := flag.String("root", "", "Mycelium root directory")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lshortfile)

	if *root == "" {
		home, _ := os.UserHomeDir()
		*root = home + "/Documents/mycelium"
	}

	b, err := brain.New(*root)
	if err != nil {
		log.Fatalf("Brain open: %v", err)
	}

	// Start the mycelium proxy (meshgate reverse-proxy)
	p := proxy.New(b)
	p.Port = *proxyPort
	p.Upstream = *proxyUpstream
	go func() {
		log.Printf("🧬 Starting mycelium proxy on :%s → %s", p.Port, p.Upstream)
		if err := p.Start(); err != nil {
			log.Printf("Proxy exited: %v", err)
		}
	}()

	d := daemon.New(b)
	log.Printf(`
🍄 Mycelium Daemon
   Poll:    %s
   Port:    %s
   Proxy:   :%s → %s
   Brain:   %s (%d entries)
`, d.Interval, d.Port, p.Port, p.Upstream, b.LogPath, b.Count())

	// Handle signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigCh
		log.Println("Shutting down...")
		p.Stop()
		d.Stop()
		os.Exit(0)
	}()

	if err := d.Start(); err != nil {
		log.Fatalf("Daemon error: %v", err)
	}
}
