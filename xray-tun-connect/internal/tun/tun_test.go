package tun

import (
	"context"
	"net"
	"strconv"
	"testing"
	"time"
)

const okLink = "trojan://pw@198.51.100.12:443?security=tls&sni=t.example.com#ok"

func TestStartReservesPortsForBadLinks(t *testing.T) {
	res, err := Start(context.Background(), Options{
		Links:        []string{okLink, "garbage", okLink},
		StartPort:    freePortBase(t),
		ReadyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer res.Close()

	if got := len(res.Tunnels); got != 3 {
		t.Fatalf("want 3 tunnels, got %d", got)
	}
	for i, tun := range res.Tunnels {
		if tun.Index != i || tun.Port != res.Tunnels[0].Port+i {
			t.Errorf("tunnel %d: index/port not positional: %+v", i, tun)
		}
	}
	if res.Tunnels[1].OK || res.Tunnels[1].Error == "" {
		t.Errorf("garbage link should fail with a reason: %+v", res.Tunnels[1])
	}
	if res.Alive() != 2 {
		t.Errorf("Alive = %d, want 2", res.Alive())
	}
	for _, tun := range res.Tunnels {
		addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(tun.Port))
		conn, err := net.DialTimeout("tcp", addr, time.Second)
		if tun.OK != (err == nil) {
			t.Errorf("port %d: ok=%v but dial err=%v", tun.Port, tun.OK, err)
		}
		if conn != nil {
			conn.Close()
		}
	}
}

func TestStartAllBadLinksDoesNotBootCore(t *testing.T) {
	res, err := Start(context.Background(), Options{Links: []string{"garbage", "nope"}, StartPort: freePortBase(t)})
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	if res.Alive() != 0 || res.instance != nil {
		t.Errorf("expected no live tunnels and no core instance, got alive=%d", res.Alive())
	}
}

// freePortBase returns a port such that base..base+9 are very likely unused.
func freePortBase(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port + 100
}
