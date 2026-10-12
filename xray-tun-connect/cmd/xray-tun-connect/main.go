// Command xray-tun-connect turns a list of share links into local tunnels, in
// one of two modes.
//
// All at once (default): link i is a SOCKS5 proxy on start-port+i. A link that
// fails to parse or build still reserves its port, so the port map is stable
// regardless of bad input.
//
// Forward (-forward host:port): one port, start-port, sends its TCP and UDP to
// host:port through one link at a time. Every link is checked in the
// background with a DNS question over UDP through it; the port stays on its
// link while that one is usable, and otherwise moves to the first usable link
// in the order given. A direct connection, without a VPN, is the last resort
// (-disable-direct drops it).
//
// The default mode prints one JSON report line to stdout, for the program
// that started it. Everything else is logged to stderr, one line each:
// "LEVEL event key=value ...". The process serves until it receives
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
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"xray-tun-connect/internal/logfmt"
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
	checkInterval := fs.Duration("check-interval", 15*time.Second, "forward mode: time between checks of every link")
	checkTimeout := fs.Duration("check-timeout", 5*time.Second, "forward mode: timeout of one check")
	checkFails := fs.Int("check-fails", 3, "forward mode: failed checks in a row that make a link unusable")
	disableDirect := fs.Bool("disable-direct", false, "forward mode: no direct connection (no VPN) as the last resort")
	readyTimeout := fs.Duration("ready-timeout", 10*time.Second, "how long to wait for the inbounds to start listening")
	showVersion := fs.Bool("version", false, "print version and exit")

	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Fprintln(stdout, version)
		return 0
	}
	log := logfmt.New(stderr)

	var target *tun.Target
	if *forward != "" {
		var err error
		if target, err = tun.ParseTarget(*forward); err != nil {
			log.Log(logfmt.Error, "usage", "flag", "-forward", "err", err)
			return 2
		}
	}

	stdinReader := bufio.NewReader(stdin)
	if *stdinLinks {
		blob, err := tun.ReadLinkBlock(stdinReader)
		if err != nil {
			log.Log(logfmt.Error, "usage", "flag", "-stdin-links", "err", err)
			return 2
		}
		*linksBlob += "\n" + blob
	}

	all := append([]string(links), tun.SplitLinks(*linksBlob)...)
	if len(all) == 0 && (target == nil || *disableDirect) {
		log.Log(logfmt.Error, "usage", "err", "no links given, use -link, -links or -stdin-links")
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
			Direct:        !*disableDirect,
			CheckInterval: *checkInterval,
			CheckTimeout:  *checkTimeout,
			CheckFails:    *checkFails,
		}, log)
	}

	res, err := tun.Start(ctx, tun.Options{
		Links:        all,
		StartPort:    *startPort,
		Listen:       *listen,
		XrayLogLevel: *xrayLog,
		ReadyTimeout: *readyTimeout,
	})
	if err != nil {
		log.Log(logfmt.Error, "start_failed", "err", err)
		return 1
	}
	defer res.Close()

	if err := json.NewEncoder(stdout).Encode(report{Version: version, Listen: *listen, Tunnels: res.Tunnels}); err != nil {
		log.Log(logfmt.Error, "report_failed", "err", err)
		return 1
	}
	for _, t := range res.Tunnels {
		if !t.OK {
			log.Log(logfmt.Warn, "link_rejected", "index", t.Index, "name", t.Name, "reason", t.Error)
		}
	}
	if res.Alive() == 0 {
		log.Log(logfmt.Error, "no_tunnels", "err", "no usable links, exiting")
		return 1
	}
	log.Log(logfmt.Info, "start", "version", version, "listen", *listen, "links", len(res.Tunnels), "alive", res.Alive())

	watchStdin()
	<-ctx.Done()
	return 0
}

func runForward(ctx context.Context, opts tun.ForwardOptions, log *logfmt.Logger) int {
	log.Log(logfmt.Info, "start", "version", version, "listen", opts.Listen, "port", opts.StartPort,
		"forward", net.JoinHostPort(opts.Forward.Host, strconv.Itoa(opts.Forward.Port)), "links", len(opts.Links), "direct", opts.Direct)
	err := tun.Forward(ctx, opts, func(e tun.Event) { log.Log(e.Level(), e.Kind, e.KV()...) })
	if err != nil {
		log.Log(logfmt.Error, "forward_failed", "err", err)
		return 1
	}
	return 0
}

func isPipe(f *os.File) bool {
	st, err := f.Stat()
	return err == nil && st.Mode()&os.ModeNamedPipe != 0
}
