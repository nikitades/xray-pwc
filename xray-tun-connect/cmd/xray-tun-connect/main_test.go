package main

import (
	"bytes"
	"encoding/json"
	"net"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

const okLink = "trojan://pw@198.51.100.12:443?security=tls&sni=t.example.com#ok"

// runCLI runs the command with links on a piped stdin that closes right away,
// so a serving process stops after its report.
func runCLI(t *testing.T, args ...string) (int, string, string) {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	_, _ = w.WriteString(okLink + "\n\n")
	w.Close()
	defer r.Close()
	var stdout, stderr bytes.Buffer
	done := make(chan int, 1)
	go func() {
		done <- run(append([]string{"-stdin-links", "-ready-timeout", "5s"}, args...), &stdout, &stderr, r)
	}()
	select {
	case code := <-done:
		return code, stdout.String(), stderr.String()
	case <-time.After(20 * time.Second):
		t.Fatal("run did not stop on stdin EOF")
		return 0, "", ""
	}
}

func reportNames(t *testing.T, stdout string) []string {
	t.Helper()
	var rep report
	line, _, _ := strings.Cut(stdout, "\n")
	if err := json.Unmarshal([]byte(line), &rep); err != nil {
		t.Fatalf("first line is not the report: %q: %v", line, err)
	}
	var names []string
	for _, tun := range rep.Tunnels {
		names = append(names, tun.Name)
	}
	return names
}

func freePort(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return strconv.Itoa(l.Addr().(*net.TCPAddr).Port)
}

func TestVersion(t *testing.T) {
	var stdout bytes.Buffer
	if code := run([]string{"-version"}, &stdout, &bytes.Buffer{}, os.Stdin); code != 0 || strings.TrimSpace(stdout.String()) != version {
		t.Errorf("code %d, output %q", code, stdout.String())
	}
}

func TestUsageErrors(t *testing.T) {
	for _, args := range [][]string{
		{"-forward", "nohost"},
		{"-forward", "h:1", "-health-url", "ftp://x"},
		{"-forward", "h:1", "-health-url", "not a url"},
		{"-no-such-flag"},
	} {
		if code, _, _ := runCLI(t, args...); code != 2 {
			t.Errorf("%v: code %d, want 2", args, code)
		}
	}
}

func TestDefaultModeHasNoDirect(t *testing.T) {
	code, stdout, stderr := runCLI(t, "-start-port", freePort(t))
	if code != 0 {
		t.Fatalf("code %d: %s", code, stderr)
	}
	if names := reportNames(t, stdout); len(names) != 1 || names[0] != "ok" {
		t.Errorf("tunnels = %q", names)
	}
}

func TestForwardAddsDirectLast(t *testing.T) {
	code, stdout, stderr := runCLI(t, "-forward", "127.0.0.1:9", "-start-port", freePort(t))
	if code != 0 {
		t.Fatalf("code %d: %s", code, stderr)
	}
	if names := reportNames(t, stdout); len(names) != 2 || names[0] != "ok" || names[1] != "direct" {
		t.Errorf("tunnels = %q", names)
	}
}

func TestForwardDisableDirect(t *testing.T) {
	code, stdout, stderr := runCLI(t, "-forward", "127.0.0.1:9", "-start-port", freePort(t), "-disable-direct")
	if code != 0 {
		t.Fatalf("code %d: %s", code, stderr)
	}
	if names := reportNames(t, stdout); len(names) != 1 || names[0] != "ok" {
		t.Errorf("tunnels = %q", names)
	}
}
