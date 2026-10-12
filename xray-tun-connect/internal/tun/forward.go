package tun

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strconv"
	"time"

	"xray-tun-connect/internal/logfmt"
)

// ForwardOptions serves one of Options.Links at a time on Options.StartPort,
// a forward to Options.Forward. A background check keeps the list of usable
// links; the port stays on its link while that one is usable, and otherwise
// moves to the first usable link in the order given.
type ForwardOptions struct {
	Options
	// Direct adds the connection without a VPN after the links. It is never
	// checked: it is the last resort, used only while no link is usable.
	Direct bool
	// CheckInterval is the time between check rounds; every link is checked
	// in every round.
	CheckInterval time.Duration
	CheckTimeout  time.Duration
	// CheckFails failed checks in a row make a link unusable.
	CheckFails int
	// NoTunnelsEvery repeats the no_tunnels event while nothing serves.
	NoTunnelsEvery time.Duration

	// check proves one link through its SOCKS proxy; dnsCheck by default.
	check func(ctx context.Context, link, socks string) error
}

// Event is one line of the forward log.
type Event struct {
	// Kind: link_rejected, link_usable, link_unusable, link_failed, active,
	// switch, no_tunnels.
	Kind   string
	Index  int
	Name   string
	From   string
	To     string
	Reason string
	Fails  int
}

// Level is how much the event matters.
func (e Event) Level() string {
	switch e.Kind {
	case "link_usable", "active":
		return logfmt.Info
	case "switch":
		if e.Reason == "link_recovered" {
			return logfmt.Info
		}
		return logfmt.Warn
	case "no_tunnels":
		return logfmt.Error
	default:
		return logfmt.Warn
	}
}

// KV is the event's fields for logfmt.
func (e Event) KV() []any {
	switch e.Kind {
	case "switch":
		return []any{"from", e.From, "to", e.To, "reason", e.Reason}
	case "no_tunnels":
		return []any{"usable", 0, "direct", false}
	case "link_unusable":
		return []any{"index", e.Index, "name", e.Name, "fails", e.Fails, "reason", e.Reason}
	case "link_usable", "active":
		return []any{"index", e.Index, "name", e.Name}
	default:
		return []any{"index", e.Index, "name", e.Name, "reason", e.Reason}
	}
}

// Inspect parses the links without starting anything, for the start report.
func Inspect(links []string, port int) []Tunnel {
	out := make([]Tunnel, len(links))
	for i, link := range links {
		t := Tunnel{Index: i, Port: port, Name: linkName(link)}
		if _, proto, err := parseLink(link); err != nil {
			t.Error = err.Error()
		} else {
			t.Protocol, t.OK = proto, true
		}
		out[i] = t
	}
	return out
}

type linkState int

const (
	unknown linkState = iota
	usable
	unusable
)

type forwarder struct {
	opts   ForwardOptions
	emit   func(Event)
	names  []string
	ok     []bool // the link parsed and the checker runs it
	ports  []int  // the checker's SOCKS port per link
	state  []linkState
	fails  []int
	direct int // index of direct, -1 without it
	// active is the index on the port; -1 when nothing serves.
	active  int
	serving *Result
	// lastNoTunnels is when no_tunnels was last emitted.
	lastNoTunnels time.Time
}

// Forward serves until ctx is done. The port stays the same: on a switch it
// is down only while the next link starts, so a program using it sees a
// short gap. With no usable link and no direct, the port is closed and
// no_tunnels repeats until a link becomes usable.
func Forward(ctx context.Context, opts ForwardOptions, emit func(Event)) error {
	if len(opts.Links) == 0 && !opts.Direct {
		return errors.New("no links")
	}
	if opts.Forward == nil {
		return errors.New("no forward target")
	}
	if opts.CheckInterval <= 0 {
		opts.CheckInterval = 15 * time.Second
	}
	if opts.CheckTimeout <= 0 {
		opts.CheckTimeout = 5 * time.Second
	}
	if opts.CheckFails <= 0 {
		opts.CheckFails = 3
	}
	if opts.NoTunnelsEvery <= 0 {
		opts.NoTunnelsEvery = time.Minute
	}
	if opts.check == nil {
		opts.check = func(ctx context.Context, _, socks string) error { return dnsCheck(ctx, socks) }
	}

	n := len(opts.Links)
	f := &forwarder{
		opts:   opts,
		emit:   emit,
		names:  make([]string, n),
		ok:     make([]bool, n),
		ports:  make([]int, n),
		state:  make([]linkState, n),
		fails:  make([]int, n),
		direct: -1,
		active: -1,
	}
	for i, link := range opts.Links {
		f.names[i] = linkName(link)
	}
	if opts.Direct {
		f.direct = n
	}
	defer f.stop()

	if n > 0 {
		checker, err := f.startChecker(ctx)
		if err != nil {
			return err
		}
		defer checker.Close()
	}

	tick := time.NewTicker(opts.CheckInterval)
	defer tick.Stop()
	for {
		f.checkRound(ctx)
		if ctx.Err() != nil {
			return nil
		}
		f.pick(ctx)
		if ctx.Err() != nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return nil
		case <-tick.C:
		}
	}
}

// startChecker runs every link as a SOCKS proxy on a free port, for the checks.
func (f *forwarder) startChecker(ctx context.Context) (*Result, error) {
	for i := range f.ports {
		port, err := freePort()
		if err != nil {
			return nil, err
		}
		f.ports[i] = port
	}
	res, err := Start(ctx, Options{
		Links:        f.opts.Links,
		Ports:        f.ports,
		Listen:       "127.0.0.1",
		XrayLogLevel: f.opts.XrayLogLevel,
		ReadyTimeout: f.opts.ReadyTimeout,
	})
	if err != nil {
		return nil, fmt.Errorf("start the link checker: %w", err)
	}
	for i, t := range res.Tunnels {
		f.ok[i] = t.OK
		if !t.OK {
			f.emit(Event{Kind: "link_rejected", Index: i, Name: f.names[i], Reason: t.Error})
		}
	}
	return res, nil
}

// checkRound checks every link at once and updates the usable list.
func (f *forwarder) checkRound(ctx context.Context) {
	errs := make([]error, len(f.ok))
	done := make(chan struct{})
	pending := 0
	for i, ok := range f.ok {
		if !ok {
			continue
		}
		pending++
		go func() {
			defer func() { done <- struct{}{} }()
			cctx, cancel := context.WithTimeout(ctx, f.opts.CheckTimeout)
			defer cancel()
			errs[i] = f.opts.check(cctx, f.opts.Links[i], net.JoinHostPort("127.0.0.1", strconv.Itoa(f.ports[i])))
		}()
	}
	for ; pending > 0; pending-- {
		<-done
	}
	if ctx.Err() != nil {
		return
	}
	for i, ok := range f.ok {
		if !ok {
			continue
		}
		if errs[i] == nil {
			f.fails[i] = 0
			if f.state[i] != usable {
				f.state[i] = usable
				f.emit(Event{Kind: "link_usable", Index: i, Name: f.names[i]})
			}
			continue
		}
		f.fails[i]++
		if f.fails[i] >= f.opts.CheckFails && f.state[i] != unusable {
			f.state[i] = unusable
			f.emit(Event{Kind: "link_unusable", Index: i, Name: f.names[i], Fails: f.fails[i], Reason: errs[i].Error()})
		}
	}
}

// want is where the port should be now: the active link while it is usable,
// otherwise the first usable link, then direct, then nothing (-1).
func (f *forwarder) want() int {
	if f.active >= 0 && f.active != f.direct && f.state[f.active] == usable {
		return f.active
	}
	for i, s := range f.state {
		if s == usable {
			return i
		}
	}
	return f.direct
}

// pick moves the port to want(). A link that does not start there is
// unusable until a check passes again.
func (f *forwarder) pick(ctx context.Context) {
	prev := f.active
	for {
		next := f.want()
		if next == f.active {
			break
		}
		f.stop()
		if next < 0 {
			break
		}
		if err := f.serve(ctx, next); err != nil {
			if ctx.Err() != nil {
				return
			}
			f.emit(Event{Kind: "link_failed", Index: next, Name: f.name(next), Reason: err.Error()})
			if next == f.direct {
				break
			}
			f.state[next] = unusable
			continue
		}
		f.active = next
		if prev < 0 {
			f.emit(Event{Kind: "active", Index: next, Name: f.name(next)})
		} else {
			reason := "unusable"
			if prev == f.direct {
				reason = "link_recovered"
			}
			f.emit(Event{Kind: "switch", From: f.name(prev), To: f.name(next), Reason: reason})
		}
		break
	}
	if f.active >= 0 {
		f.lastNoTunnels = time.Time{}
		return
	}
	if time.Since(f.lastNoTunnels) >= f.opts.NoTunnelsEvery {
		f.lastNoTunnels = time.Now()
		f.emit(Event{Kind: "no_tunnels"})
	}
}

func (f *forwarder) serve(ctx context.Context, i int) error {
	link := DirectLink
	if i != f.direct {
		link = f.opts.Links[i]
	}
	one := f.opts.Options
	one.Links = []string{link}
	one.Ports = nil
	res, err := Start(ctx, one)
	if err == nil && res.Alive() == 0 {
		err = errors.New(res.Tunnels[0].Error)
	}
	if err != nil {
		if res != nil {
			_ = res.Close()
		}
		return err
	}
	f.serving = res
	return nil
}

// stop closes the port.
func (f *forwarder) stop() {
	if f.serving != nil {
		_ = f.serving.Close()
		f.serving = nil
	}
	f.active = -1
}

func (f *forwarder) name(i int) string {
	if i == f.direct {
		return "direct"
	}
	return f.names[i]
}

func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}
