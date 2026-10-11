// Command xray-tun-connect turns a list of share links into local tunnels, in
// one of two modes.
//
// All at once (default): link i is a SOCKS5 proxy on start-port+i. A link that
// fails to parse or build still reserves its port, so the port map is stable
// regardless of bad input.
//
// Forward (-forward host:port): one port, start-port, sends its TCP and UDP to
// host:port through one link at a time. A direct connection, without a VPN,
// goes after the last link (-disable-direct drops it). With more than one
// candidate a health check runs, and the port moves to the next one, round
// robin, when it fails.
//
// One JSON report line is printed to stdout; in forward mode one JSON event
// line follows for every switch. The process serves until it receives
// SIGINT/SIGTERM — or until stdin hits EOF when stdin is a pipe, which lets a
// parent process take the tunnels down just by exiting.
package main

import (
	"bufio"
	"context"
	_ "embed"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/url"
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
	startPort := fs.Int("start-port", 10000, "first port; link i listens on start-port+i (forward mode: the one port)")
	listen := fs.String("listen", "127.0.0.1", "address to bind the inbounds to")
	xrayLog := fs.String("xray-log-level", "warning", "xray-core log level: debug|info|warning|error|none (goes to stderr)")
	forward := fs.String("forward", "", "host:port: one port (start-port) forwards TCP and UDP there through one link at a time, instead of SOCKS ports")
	healthURL := fs.String("health-url", "", "forward mode, several links: URL fetched directly from here to check the link (below HTTP 400 passes); unset, "+tun.DefaultProbeURL+" is fetched through the link")
	healthInterval := fs.Duration("health-interval", 15*time.Second, "forward mode: time between health checks")
	healthTimeout := fs.Duration("health-timeout", 5*time.Second, "forward mode: timeout of one health check")
	healthFails := fs.Int("health-fails", 3, "forward mode: failed checks in a row before the next link takes the port")
	disableDirect := fs.Bool("disable-direct", false, "forward mode: no direct connection (no VPN) after the last link")
	readyTimeout := fs.Duration("ready-timeout", 10*time.Second, "how long to wait for the inbounds to start listening")
	showVersion := fs.Bool("version", false, "print version and exit")

	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Fprintln(stdout, version)
		return 0
	}

	var target *tun.Target
	if *forward != "" {
		var err error
		if target, err = tun.ParseTarget(*forward); err != nil {
			fmt.Fprintln(stderr, "xray-tun-connect: -forward:", err)
			return 2
		}
	}

	if *healthURL != "" {
		if u, err := url.Parse(*healthURL); err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			fmt.Fprintln(stderr, "xray-tun-connect: -health-url: not an http(s) URL:", *healthURL)
			return 2
		}
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

	// EOF on a piped stdin means the parent is gone.
	watchStdin := func() {
		if isPipe(stdin) {
			go func() {
				_, _ = io.Copy(io.Discard, stdinReader)
				cancel()
			}()
		}
	}

	if target != nil {
		if !*disableDirect {
			all = append(all, tun.DirectLink)
		}
		watchStdin()
		return runForward(ctx, tun.ForwardOptions{
			Options: tun.Options{
				Links:        all,
				StartPort:    *startPort,
				Listen:       *listen,
				XrayLogLevel: *xrayLog,
				ReadyTimeout: *readyTimeout,
				Forward:      target,
			},
			HealthURL:      *healthURL,
			HealthInterval: *healthInterval,
			HealthTimeout:  *healthTimeout,
			HealthFails:    *healthFails,
		}, stdout, stderr)
	}

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

	watchStdin()
	<-ctx.Done()
	return 0
}

func runForward(ctx context.Context, opts tun.ForwardOptions, stdout, stderr io.Writer) int {
	out := json.NewEncoder(stdout)
	tunnels := tun.Inspect(opts.Links, opts.StartPort)
	if err := out.Encode(report{Version: version, Listen: opts.Listen, Tunnels: tunnels}); err != nil {
		fmt.Fprintln(stderr, "xray-tun-connect:", err)
		return 1
	}
	var usable []string
	for i, t := range tunnels {
		if t.OK {
			usable = append(usable, opts.Links[i])
		}
	}
	if len(usable) == 0 {
		fmt.Fprintln(stderr, "xray-tun-connect: no usable links, exiting")
		return 1
	}
	// Event indexes are positions in the full list, as in the report.
	index := make([]int, 0, len(usable))
	for i, t := range tunnels {
		if t.OK {
			index = append(index, i)
		}
	}
	opts.Links = usable
	err := tun.Forward(ctx, opts, func(e tun.Event) {
		e.Index = index[e.Index]
		_ = out.Encode(e)
	})
	if err != nil {
		fmt.Fprintln(stderr, "xray-tun-connect:", err)
		return 1
	}
	return 0
}

func isPipe(f *os.File) bool {
	st, err := f.Stat()
	return err == nil && st.Mode()&os.ModeNamedPipe != 0
}
