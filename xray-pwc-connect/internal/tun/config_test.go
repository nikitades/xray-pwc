package tun

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestFailingOutbound(t *testing.T) {
	tests := []struct {
		msg  string
		want int
	}{
		{"app/proxyman/outbound: failed to build outbound config with tag out_3 > bad key", 3},
		{"failed to build outbound config with tag out_0", 0},
		{"failed to build outbound config with tag proxy > x", -1},
		{"something else entirely", -1},
	}
	for _, tt := range tests {
		if got := failingOutbound(errors.New(tt.msg)); got != tt.want {
			t.Errorf("failingOutbound(%q) = %d, want %d", tt.msg, got, tt.want)
		}
	}
}

func TestBuildReason(t *testing.T) {
	err := errors.New("a > b > final reason ")
	if got := buildReason(err); got != "final reason" {
		t.Errorf("buildReason = %q", got)
	}
}

func TestLogConfigNeverUsesStdout(t *testing.T) {
	for _, level := range []string{"debug", "warning", "none"} {
		cfg := logConfig(level)
		if cfg["error"] == "" {
			t.Errorf("level %s: empty error path means stdout, which must stay JSON-only", level)
		}
	}
}

func TestForwardInbound(t *testing.T) {
	tunnels := []Tunnel{{Index: 0, Port: 51974, OK: true}}
	outbounds := []json.RawMessage{json.RawMessage(`{"protocol":"freedom"}`)}
	cfg, err := buildConfig(tunnels, outbounds, Options{Listen: "127.0.0.1", Forward: &Target{Host: "example.com", Port: 51973}})
	if err != nil {
		t.Fatal(err)
	}
	if err := validateBuild(cfg); err != nil {
		t.Fatalf("xray rejects the forward config: %v", err)
	}
	var got struct {
		Inbounds []struct {
			Protocol string         `json:"protocol"`
			Settings map[string]any `json:"settings"`
		} `json:"inbounds"`
	}
	if err := json.Unmarshal(cfg, &got); err != nil {
		t.Fatal(err)
	}
	in := got.Inbounds[0]
	if in.Protocol != "tunnel" || in.Settings["address"] != "example.com" || in.Settings["port"] != float64(51973) {
		t.Errorf("inbound = %+v", in)
	}
}

func TestParseTarget(t *testing.T) {
	for _, bad := range []string{"", "host", "host:0", ":53", "host:70000"} {
		if _, err := ParseTarget(bad); err == nil {
			t.Errorf("ParseTarget(%q) accepted", bad)
		}
	}
	if got, err := ParseTarget("vpnc-mon.example.com:51973"); err != nil || got.Host != "vpnc-mon.example.com" || got.Port != 51973 {
		t.Errorf("ParseTarget = %+v, %v", got, err)
	}
}
