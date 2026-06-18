// myceliumd — persistent daemon for mycelium safety-net imports.
//
// Polls Hermes state.db for completed conversation pairs and imports
// them into mycelium automatically. Runs as a background service.
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/naufalworks/mycelium/go/pkg/brain"
	"github.com/naufalworks/mycelium/go/pkg/daemon"
)

func main() {
	port := flag.String("port", daemon.DefaultPort, "Health HTTP port")
	interval := flag.Duration("interval", daemon.DefaultInterval, "Poll interval")
	root := flag.String("root", "", "Mycelium root directory")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lshortfile)

	b, err := brain.New(*root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Cannot open mycelium: %v\n", err)
		os.Exit(1)
	}

	d := daemon.New(b)
	d.Port = *port
	d.Interval = *interval

	fmt.Printf(`
🍄 Mycelium Daemon
   Poll:    %s
   Port:    %s
   Brain:   %s (%d entries)

`, d.Interval, d.Port, b.LogPath, b.Count())

	// Handle signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigCh
		log.Println("Shutting down...")
		d.Stop()
		os.Exit(0)
	}()

	if err := d.Start(); err != nil {
		log.Fatalf("Daemon error: %v", err)
	}
}
