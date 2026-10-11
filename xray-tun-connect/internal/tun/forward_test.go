package tun

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"
)

func namedLink(name string) string {
	return "trojan://pw@198.51.100.12:443?security=tls&sni=t.example.com#" + name
}

// runForward starts Forward in the background and returns its events.
func runForward(t *testing.T, opts ForwardOptions) (<-chan Event, context.CancelFunc) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	events := make(chan Event, 100)
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := Forward(ctx, opts, func(e Event) { events <- e }); err != nil {
			t.Errorf("Forward: %v", err)
		}
	}()
	t.Cleanup(func() { cancel(); <-done })
	return events, cancel
}

func next(t *testing.T, events <-chan Event) Event {
	t.Helper()
	select {
	case e := <-events:
		return e
	case <-time.After(10 * time.Second):
		t.Fatal("no event")
		return Event{}
	}
}

func health(status int) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(status) }))
}

func forwardOptions(t *testing.T, links ...string) ForwardOptions {
	return ForwardOptions{
		Options: Options{
			Links:        links,
			StartPort:    freePortBase(t),
			ReadyTimeout: 5 * time.Second,
			Forward:      &Target{Host: "127.0.0.1", Port: 9},
		},
		HealthInterval: 50 * time.Millisecond,
		HealthTimeout:  300 * time.Millisecond,
		HealthFails:    2,
	}
}

func TestForwardRotatesOnTheSamePort(t *testing.T) {
	srv := health(http.StatusInternalServerError)
	defer srv.Close()
	opts := forwardOptions(t, namedLink("a"), namedLink("b"))
	opts.HealthURL = srv.URL
	events, _ := runForward(t, opts)

	want := []Event{
		{Event: "active", Index: 0, Name: "a"},
		{Event: "failed", Index: 0, Name: "a"},
		{Event: "active", Index: 1, Name: "b"},
	}
	for _, w := range want {
		got := next(t, events)
		if got.Event != w.Event || got.Index != w.Index || got.Name != w.Name {
			t.Fatalf("event = %+v, want %+v", got, w)
		}
		if got.Event == "failed" && got.Reason == "" {
			t.Errorf("failed without a reason: %+v", got)
		}
	}
	conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(opts.StartPort)), time.Second)
	if err != nil {
		t.Fatalf("the port is not served after a switch: %v", err)
	}
	conn.Close()
	// Round robin: back to the first link.
	if e := next(t, events); e.Event != "failed" || e.Index != 1 {
		t.Fatalf("event = %+v", e)
	}
	if e := next(t, events); e.Event != "active" || e.Index != 0 {
		t.Fatalf("event = %+v", e)
	}
}

func TestForwardKeepsHealthyLink(t *testing.T) {
	srv := health(http.StatusOK)
	defer srv.Close()
	opts := forwardOptions(t, namedLink("a"), namedLink("b"))
	opts.HealthURL = srv.URL
	events, _ := runForward(t, opts)

	if e := next(t, events); e.Event != "active" || e.Index != 0 {
		t.Fatalf("event = %+v", e)
	}
	select {
	case e := <-events:
		t.Fatalf("a healthy link was replaced: %+v", e)
	case <-time.After(500 * time.Millisecond):
	}
}

func TestForwardDoesNotCheckASingleLink(t *testing.T) {
	srv := health(http.StatusInternalServerError)
	defer srv.Close()
	opts := forwardOptions(t, namedLink("only"))
	opts.HealthURL = srv.URL
	events, _ := runForward(t, opts)

	if e := next(t, events); e.Event != "active" {
		t.Fatalf("event = %+v", e)
	}
	select {
	case e := <-events:
		t.Fatalf("a single link was checked: %+v", e)
	case <-time.After(500 * time.Millisecond):
	}
}

// Without a health URL the probe goes through the link; this one leads nowhere.
func TestForwardProbesThroughTheLink(t *testing.T) {
	events, _ := runForward(t, forwardOptions(t, namedLink("a"), namedLink("b")))

	if e := next(t, events); e.Event != "active" || e.Index != 0 {
		t.Fatalf("event = %+v", e)
	}
	if e := next(t, events); e.Event != "failed" || e.Index != 0 {
		t.Fatalf("event = %+v", e)
	}
	if e := next(t, events); e.Event != "active" || e.Index != 1 {
		t.Fatalf("event = %+v", e)
	}
}

func TestForwardSkipsLinksThatDoNotStart(t *testing.T) {
	srv := health(http.StatusOK)
	defer srv.Close()
	opts := forwardOptions(t, "garbage", namedLink("b"))
	opts.HealthURL = srv.URL
	events, _ := runForward(t, opts)

	if e := next(t, events); e.Event != "failed" || e.Index != 0 || e.Reason == "" {
		t.Fatalf("event = %+v", e)
	}
	if e := next(t, events); e.Event != "active" || e.Index != 1 {
		t.Fatalf("event = %+v", e)
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
	opts := forwardOptions(t, DirectLink)
	opts.Forward = echo(t)
	events, _ := runForward(t, opts)

	if e := next(t, events); e.Event != "active" || e.Name != "direct" {
		t.Fatalf("event = %+v", e)
	}
	roundTrip(t, "tcp", opts.StartPort)
	roundTrip(t, "udp", opts.StartPort)
}

// A dead link fails the probe through it; the direct connection passes and stays.
func TestForwardFallsBackToDirect(t *testing.T) {
	srv := health(http.StatusNoContent)
	defer srv.Close()
	opts := forwardOptions(t, namedLink("dead"), DirectLink)
	opts.ProbeURL = srv.URL
	events, _ := runForward(t, opts)

	for _, w := range []Event{{Event: "active", Name: "dead"}, {Event: "failed", Name: "dead"}, {Event: "active", Name: "direct"}} {
		if e := next(t, events); e.Event != w.Event || e.Name != w.Name {
			t.Fatalf("event = %+v, want %+v", e, w)
		}
	}
	select {
	case e := <-events:
		t.Fatalf("the working direct connection was replaced: %+v", e)
	case <-time.After(500 * time.Millisecond):
	}
}
