// Command xray-tun-connect turns a list of share links into local SOCKS5 tunnels.
//
// Link i listens on start-port+i. A link that fails to parse or build still
// reserves its port, so the port map is stable regardless of bad input. One
// JSON report line is printed to stdout, then the process serves until it
// receives SIGINT/SIGTERM — or until stdin hits EOF when stdin is a pipe,
// which lets a parent process take the tunnels down just by exiting.
package main

import (
	"bufio"
	"context"
	_ "embed"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"xray-tun-connect/internal/tun"
)

//go:embed VERSION
var versionFile string

var version = strings.TrimSpace(versionFile)

type stringList []string

func (s *stringList) String() string     { return strings.Join(*s, ",") }
func (s *stringList) Set(v string) error { *s = append(*s, v); return nil }

type report struct {
	Version string       `json:"version"`
	Listen  string       `json:"listen"`
	Tunnels []tun.Tunnel `json:"tunnels"`
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr, os.Stdin))
}

func run(args []string, stdout, stderr io.Writer, stdin *os.File) int {
	fs := flag.NewFlagSet("xray-tun-connect", flag.ContinueOnError)
	fs.SetOutput(stderr)

	var links stringList
	fs.Var(&links, "link", "share link; repeat the flag for several links")
	linksBlob := fs.String("links", "", "share links separated by newlines or commas; # starts a comment")
	stdinLinks := fs.Bool("stdin-links", false, "read share links from stdin, one per line, up to the first empty line; keeps them out of the process list")
	startPort := fs.Int("start-port", 10000, "first SOCKS port; link i listens on start-port+i")
	listen := fs.String("listen", "127.0.0.1", "address to bind the SOCKS inbounds to")
	xrayLog := fs.String("xray-log-level", "warning", "xray-core log level: debug|info|warning|error|none (goes to stderr)")
	readyTimeout := fs.Duration("ready-timeout", 10*time.Second, "how long to wait for the inbounds to start listening")
	showVersion := fs.Bool("version", false, "print version and exit")

	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Fprintln(stdout, version)
		return 0
	}

	stdinReader := bufio.NewReader(stdin)
	if *stdinLinks {
		blob, err := tun.ReadLinkBlock(stdinReader)
		if err != nil {
			fmt.Fprintln(stderr, "xray-tun-connect: reading links from stdin:", err)
			return 2
		}
		*linksBlob += "\n" + blob
	}

	all := append([]string(links), tun.SplitLinks(*linksBlob)...)
	if len(all) == 0 {
		fmt.Fprintln(stderr, "xray-tun-connect: no links given, use -link or -links")
		fs.Usage()
		return 2
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	ctx, stop := signal.NotifyContext(ctx, syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	res, err := tun.Start(ctx, tun.Options{
		Links:        all,
		StartPort:    *startPort,
		Listen:       *listen,
		XrayLogLevel: *xrayLog,
		ReadyTimeout: *readyTimeout,
	})
	if err != nil {
		fmt.Fprintln(stderr, "xray-tun-connect:", err)
		return 1
	}
	defer res.Close()

	if err := json.NewEncoder(stdout).Encode(report{Version: version, Listen: *listen, Tunnels: res.Tunnels}); err != nil {
		fmt.Fprintln(stderr, "xray-tun-connect:", err)
		return 1
	}
	if res.Alive() == 0 {
		fmt.Fprintln(stderr, "xray-tun-connect: no usable links, exiting")
		return 1
	}

	if isPipe(stdin) {
		go func() {
			_, _ = io.Copy(io.Discard, stdinReader)
			cancel()
		}()
	}

	<-ctx.Done()
	return 0
}

func isPipe(f *os.File) bool {
	st, err := f.Stat()
	return err == nil && st.Mode()&os.ModeNamedPipe != 0
}
