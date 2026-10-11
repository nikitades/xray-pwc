package tun

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// DefaultProbeURL is fetched through the link itself when no health URL is
// given: it only proves that the link carries a connection.
const DefaultProbeURL = "https://www.gstatic.com/generate_204"

// ForwardOptions serves Options.Links one at a time on Options.StartPort, a
// forward to Options.Forward. With several links, a health check picks the
// next one, round robin, when the current one stops working. Put DirectLink
// last to fall back to no VPN when every link is down.
type ForwardOptions struct {
	Options
	// HealthURL, when set, is fetched directly from this machine: point it at
	// something reachable only through the forward. Unset, DefaultProbeURL is
	// fetched through the link.
	HealthURL string
	// ProbeURL is what goes through the link without a HealthURL;
	// DefaultProbeURL when empty.
	ProbeURL       string
	HealthInterval time.Duration
	HealthTimeout  time.Duration
	// HealthFails failures in a row move the port to the next link.
	HealthFails int
}

// Event is one line of the forward log: a link took the port ("active") or
// lost it ("failed", with the reason).
type Event struct {
	Event  string `json:"event"`
	Index  int    `json:"index"`
	Name   string `json:"name,omitempty"`
	Reason string `json:"reason,omitempty"`
}

// Inspect parses the links without starting anything, for the first report of
// a forward: every link reports the one shared port.
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

// Forward serves until ctx is done. The port stays the same: on a switch it is
// down only while the next link starts, so a program using it just sees a
// short gap. A link that does not start is skipped; when none starts, it waits
// one HealthInterval before the next round. A single link is never checked.
func Forward(ctx context.Context, opts ForwardOptions, emit func(Event)) error {
	if len(opts.Links) == 0 {
		return errors.New("no links")
	}
	if opts.Forward == nil {
		return errors.New("no forward target")
	}
	if opts.HealthInterval <= 0 {
		opts.HealthInterval = 15 * time.Second
	}
	if opts.HealthTimeout <= 0 {
		opts.HealthTimeout = 5 * time.Second
	}
	if opts.HealthFails <= 0 {
		opts.HealthFails = 3
	}
	n := len(opts.Links)
	checked := n > 1

	notStarted := 0
	for i := 0; ; i = (i + 1) % n {
		link := opts.Links[i]
		one := opts.Options
		one.Links = []string{link}
		one.CheckPort = 0
		if checked && opts.HealthURL == "" {
			port, err := freePort()
			if err != nil {
				return err
			}
			one.CheckPort = port
		}

		res, err := Start(ctx, one)
		if ctx.Err() != nil {
			if res != nil {
				_ = res.Close()
			}
			return nil
		}
		if err == nil && res.Alive() == 0 {
			err = errors.New(res.Tunnels[0].Error)
		}
		if err != nil {
			emit(Event{Event: "failed", Index: i, Name: linkName(link), Reason: err.Error()})
			if notStarted++; notStarted >= n {
				notStarted = 0
				if !sleep(ctx, opts.HealthInterval) {
					return nil
				}
			}
			continue
		}
		notStarted = 0

		emit(Event{Event: "active", Index: i, Name: linkName(link)})
		reason := ""
		if checked {
			reason = watch(ctx, healthClient(opts, one.CheckPort), healthURL(opts), opts)
		} else {
			<-ctx.Done()
		}
		_ = res.Close()
		if reason == "" {
			return nil
		}
		emit(Event{Event: "failed", Index: i, Name: linkName(link), Reason: reason})
	}
}

func healthURL(opts ForwardOptions) string {
	if opts.HealthURL != "" {
		return opts.HealthURL
	}
	if opts.ProbeURL != "" {
		return opts.ProbeURL
	}
	return DefaultProbeURL
}

// healthClient goes direct for a health URL, or through the link's own SOCKS
// check port for the default probe.
func healthClient(opts ForwardOptions, checkPort int) *http.Client {
	transport := &http.Transport{DisableKeepAlives: true}
	if checkPort != 0 {
		transport.Proxy = http.ProxyURL(&url.URL{Scheme: "socks5", Host: net.JoinHostPort("127.0.0.1", strconv.Itoa(checkPort))})
	}
	return &http.Client{
		Timeout:   opts.HealthTimeout,
		Transport: transport,
		// A redirect is an answer too: the far end is reachable.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

// watch returns why the link should go, or "" when ctx is done.
func watch(ctx context.Context, client *http.Client, target string, opts ForwardOptions) string {
	tick := time.NewTicker(opts.HealthInterval)
	defer tick.Stop()
	fails := 0
	for {
		select {
		case <-ctx.Done():
			return ""
		case <-tick.C:
		}
		err := check(ctx, client, target)
		if ctx.Err() != nil {
			return ""
		}
		if err == nil {
			fails = 0
			continue
		}
		if fails++; fails >= opts.HealthFails {
			return fmt.Sprintf("health check failed %d times in a row, last: %v", fails, err)
		}
	}
}

func check(ctx context.Context, client *http.Client, target string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return nil
}

func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
