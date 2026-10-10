package tun

import (
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
