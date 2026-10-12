package tun

import (
	"context"
	"errors"
	"io"
	"net"
	"net/netip"
	"strconv"
	"sync"
	"testing"
	"time"
)

func namedLink(name string) string {
	return "trojan://pw@198.51.100.12:443?security=tls&sni=t.example.com#" + name
}

// links decides which links pass the check, by name; the test changes it on the fly.
type links struct {
	mu   sync.Mutex
	good map[string]bool
}

func (l *links) set(name string, good bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.good[name] = good
}

func (l *links) check(_ context.Context, link, _ string) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.good[linkName(link)] {
		return nil
	}
	return errors.New("dns timeout")
}

func forwardOptions(t *testing.T, good *links, names ...string) ForwardOptions {
	var all []string
	for _, n := range names {
		all = append(all, namedLink(n))
	}
	return ForwardOptions{
		Options: Options{
			Links:        all,
			StartPort:    freePortBase(t),
			ReadyTimeout: 5 * time.Second,
			Forward:      &Target{Host: "127.0.0.1", Port: 9},
		},
		Direct:         true,
		CheckInterval:  50 * time.Millisecond,
		CheckTimeout:   300 * time.Millisecond,
		CheckFails:     2,
		NoTunnelsEvery: 200 * time.Millisecond,
		check:          good.check,
	}
}

func newLinks(good ...string) *links {
	l := &links{good: map[string]bool{}}
	for _, n := range good {
		l.good[n] = true
	}
	return l
}

// runForward starts Forward in the background and returns its events.
func runForward(t *testing.T, opts ForwardOptions) <-chan Event {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	events := make(chan Event, 1000)
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := Forward(ctx, opts, func(e Event) { events <- e }); err != nil {
			t.Errorf("Forward: %v", err)
		}
	}()
	t.Cleanup(func() { cancel(); <-done })
	return events
}

// waitFor skips events until one matches, and fails after 10 s.
func waitFor(t *testing.T, events <-chan Event, match func(Event) bool) Event {
	t.Helper()
	deadline := time.After(10 * time.Second)
	for {
		select {
		case e := <-events:
			if match(e) {
				return e
			}
		case <-deadline:
			t.Fatal("no matching event")
			return Event{}
		}
	}
}

func kind(k string) func(Event) bool { return func(e Event) bool { return e.Kind == k } }

// quiet fails if a switch-like event comes within d.
func quiet(t *testing.T, events <-chan Event, d time.Duration) {
	t.Helper()
	deadline := time.After(d)
	for {
		select {
		case e := <-events:
			if e.Kind == "switch" || e.Kind == "active" {
				t.Fatalf("unexpected %+v", e)
			}
		case <-deadline:
			return
		}
	}
}

func served(t *testing.T, port int) bool {
	t.Helper()
	c, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), 200*time.Millisecond)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

func TestForwardTakesTheFirstUsableLink(t *testing.T) {
	opts := forwardOptions(t, newLinks("b", "c"), "a", "b", "c")
	events := runForward(t, opts)

	if e := waitFor(t, events, kind("active")); e.Name != "b" || e.Index != 1 {
		t.Fatalf("active = %+v, want b", e)
	}
	if !served(t, opts.StartPort) {
		t.Fatal("the port is not served")
	}
}

func TestForwardKeepsTheLinkWhenAnEarlierOneRecovers(t *testing.T) {
	good := newLinks("b")
	events := runForward(t, forwardOptions(t, good, "a", "b"))

	if e := waitFor(t, events, kind("active")); e.Name != "b" {
		t.Fatalf("active = %+v", e)
	}
	good.set("a", true)
	waitFor(t, events, func(e Event) bool { return e.Kind == "link_usable" && e.Name == "a" })
	quiet(t, events, 300*time.Millisecond)
}

func TestForwardMovesToTheFirstUsableLink(t *testing.T) {
	good := newLinks("a", "b", "c")
	opts := forwardOptions(t, good, "a", "b", "c")
	events := runForward(t, opts)

	if e := waitFor(t, events, kind("active")); e.Name != "a" {
		t.Fatalf("active = %+v", e)
	}
	good.set("a", false)
	e := waitFor(t, events, kind("link_unusable"))
	if e.Name != "a" || e.Fails != 2 || e.Reason == "" {
		t.Fatalf("link_unusable = %+v", e)
	}
	e = waitFor(t, events, kind("switch"))
	if e.From != "a" || e.To != "b" || e.Reason != "unusable" {
		t.Fatalf("switch = %+v", e)
	}
	if !served(t, opts.StartPort) {
		t.Fatal("the port is not served after a switch")
	}
}

// Direct is the last resort: the port leaves it as soon as a link is usable.
func TestForwardLeavesDirectWhenALinkRecovers(t *testing.T) {
	good := newLinks()
	events := runForward(t, forwardOptions(t, good, "a", "b"))

	if e := waitFor(t, events, kind("active")); e.Name != "direct" {
		t.Fatalf("active = %+v", e)
	}
	good.set("b", true)
	e := waitFor(t, events, kind("switch"))
	if e.From != "direct" || e.To != "b" || e.Reason != "link_recovered" {
		t.Fatalf("switch = %+v", e)
	}
}

func TestForwardWithoutDirectStopsAndRepeatsNoTunnels(t *testing.T) {
	good := newLinks()
	opts := forwardOptions(t, good, "a")
	opts.Direct = false
	events := runForward(t, opts)

	start := time.Now()
	waitFor(t, events, kind("no_tunnels"))
	waitFor(t, events, kind("no_tunnels"))
	if time.Since(start) < opts.NoTunnelsEvery {
		t.Error("no_tunnels repeats faster than NoTunnelsEvery")
	}
	if served(t, opts.StartPort) {
		t.Fatal("the port is served with no usable link")
	}
	good.set("a", true)
	if e := waitFor(t, events, kind("active")); e.Name != "a" {
		t.Fatalf("active = %+v", e)
	}
}

func TestForwardRejectsLinksThatDoNotParse(t *testing.T) {
	good := newLinks("b")
	opts := forwardOptions(t, good, "b")
	opts.Links = append([]string{"garbage"}, opts.Links...)
	events := runForward(t, opts)

	if e := waitFor(t, events, kind("link_rejected")); e.Index != 0 || e.Reason == "" {
		t.Fatalf("link_rejected = %+v", e)
	}
	if e := waitFor(t, events, kind("active")); e.Index != 1 {
		t.Fatalf("active = %+v", e)
	}
}

func TestInspectSharesOnePort(t *testing.T) {
	got := Inspect([]string{namedLink("a"), "garbage"}, 51974)
	if len(got) != 2 || got[0].Port != 51974 || got[1].Port != 51974 || !got[0].OK || got[1].OK || got[1].Error == "" {
		t.Errorf("Inspect = %+v", got)
	}
}

// echo answers TCP and UDP on one port, like the far end of a forward.
func echo(t *testing.T) *Target {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	pc, err := net.ListenPacket("udp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close(); pc.Close() })
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			go func() { defer c.Close(); buf := make([]byte, 64); n, _ := c.Read(buf); _, _ = c.Write(buf[:n]) }()
		}
	}()
	go func() {
		buf := make([]byte, 64)
		for {
			n, addr, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			_, _ = pc.WriteTo(buf[:n], addr)
		}
	}()
	return &Target{Host: "127.0.0.1", Port: port}
}

func roundTrip(t *testing.T, network string, port int) {
	t.Helper()
	c, err := net.DialTimeout(network, net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatalf("%s dial: %v", network, err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(3 * time.Second))
	if _, err := c.Write([]byte("ping")); err != nil {
		t.Fatalf("%s write: %v", network, err)
	}
	buf := make([]byte, 4)
	if _, err := io.ReadFull(c, buf); err != nil || string(buf) != "ping" {
		t.Fatalf("%s echo = %q, %v", network, buf, err)
	}
}

// The port really carries TCP and UDP to the target, here without a VPN.
func TestForwardCarriesTCPAndUDP(t *testing.T) {
	opts := forwardOptions(t, newLinks())
	opts.Forward = echo(t)
	events := runForward(t, opts)

	if e := waitFor(t, events, kind("active")); e.Name != "direct" {
		t.Fatalf("active = %+v", e)
	}
	roundTrip(t, "tcp", opts.StartPort)
	roundTrip(t, "udp", opts.StartPort)
}

// fakeResolver answers DNS questions on a local UDP port with the same ID.
func fakeResolver(t *testing.T) netip.AddrPort {
	t.Helper()
	pc, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { pc.Close() })
	go func() {
		buf := make([]byte, 512)
		for {
			n, addr, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			if n >= 12 {
				answer := append([]byte{}, buf[:n]...)
				answer[2] |= 0x80
				_, _ = pc.WriteTo(answer, addr)
			}
		}
	}()
	return netip.MustParseAddrPort(pc.LocalAddr().String())
}

// The real check: a DNS question over UDP through a SOCKS proxy of xray.
func TestDNSCheckThroughALink(t *testing.T) {
	port := freePortBase(t)
	res, err := Start(context.Background(), Options{Links: []string{DirectLink}, StartPort: port, ReadyTimeout: 5 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	defer res.Close()
	socks := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))

	saved := checkResolvers
	t.Cleanup(func() { checkResolvers = saved })

	// One resolver that never answers and one that does: one answer is enough.
	closed, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	silent := netip.MustParseAddrPort(closed.LocalAddr().String())
	closed.Close()
	checkResolvers = []netip.AddrPort{silent, fakeResolver(t)}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := dnsCheck(ctx, socks); err != nil {
		t.Fatalf("dnsCheck with an answering resolver: %v", err)
	}

	checkResolvers = []netip.AddrPort{silent}
	ctx, cancel = context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	if err := dnsCheck(ctx, socks); err == nil {
		t.Fatal("dnsCheck passed with no resolver answering")
	}
}
