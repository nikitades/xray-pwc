package tun

import (
	"bytes"
	"encoding/json"
	"fmt"
	"runtime"
	"strings"

	"github.com/xtls/xray-core/infra/conf/serial"
)

func inboundTag(i int) string  { return fmt.Sprintf("in_%d", i) }
func outboundTag(i int) string { return fmt.Sprintf("out_%d", i) }

const checkTag = "check"

func firstOK(tunnels []Tunnel) int {
	for i, t := range tunnels {
		if t.OK {
			return i
		}
	}
	return -1
}

// buildConfig assembles one xray config: an inbound per live tunnel on
// listen:port, its outbound, and a routing rule pinning the two together.
// The inbound is SOCKS, or with opts.Forward a plain TCP and UDP forward.
func buildConfig(tunnels []Tunnel, outbounds []json.RawMessage, opts Options) ([]byte, error) {
	var inbounds, outs, rules []any
	for i, t := range tunnels {
		if !t.OK {
			continue
		}
		in := map[string]any{
			"tag":      inboundTag(i),
			"listen":   opts.Listen,
			"port":     t.Port,
			"protocol": "socks",
			"settings": map[string]any{"auth": "noauth", "udp": true},
			// Sniff for routing only, never rewrite the destination.
			"sniffing": map[string]any{"enabled": true, "destOverride": []string{"http", "tls", "quic"}, "routeOnly": true},
		}
		if opts.Forward != nil {
			in["protocol"] = "tunnel"
			in["settings"] = map[string]any{"address": opts.Forward.Host, "port": opts.Forward.Port, "network": "tcp,udp"}
			delete(in, "sniffing")
		}
		inbounds = append(inbounds, in)
		if opts.CheckPort != 0 && i == firstOK(tunnels) {
			inbounds = append(inbounds, map[string]any{
				"tag":      checkTag,
				"listen":   "127.0.0.1",
				"port":     opts.CheckPort,
				"protocol": "socks",
				"settings": map[string]any{"auth": "noauth"},
			})
			rules = append(rules, map[string]any{
				"type":        "field",
				"inboundTag":  []string{checkTag},
				"outboundTag": outboundTag(i),
			})
		}
		var ob map[string]any
		if err := json.Unmarshal(outbounds[i], &ob); err != nil {
			return nil, fmt.Errorf("outbound %d: %w", i, err)
		}
		ob["tag"] = outboundTag(i)
		outs = append(outs, ob)
		rules = append(rules, map[string]any{
			"type":        "field",
			"inboundTag":  []string{inboundTag(i)},
			"outboundTag": outboundTag(i),
		})
	}

	cfg := map[string]any{
		"log":       logConfig(opts.XrayLogLevel),
		"inbounds":  inbounds,
		"outbounds": outs,
		"routing":   map[string]any{"domainStrategy": "AsIs", "rules": rules},
	}
	return json.Marshal(cfg)
}

// logConfig keeps xray-core's own output off stdout, which is reserved for
// the JSON report. An empty error path would mean "console", i.e. stdout.
func logConfig(level string) map[string]any {
	errPath := "/dev/stderr"
	if runtime.GOOS == "windows" || level == "none" {
		errPath = "none"
	}
	return map[string]any{"loglevel": level, "access": "none", "error": errPath}
}

// validateBuild runs the config through the same decode+build path the core
// uses at startup, so a rejected outbound is caught before anything listens.
func validateBuild(cfg []byte) error {
	decoded, err := serial.DecodeJSONConfig(bytes.NewReader(cfg))
	if err != nil {
		return err
	}
	_, err = decoded.Build()
	return err
}

// failingOutbound extracts the index from a build error shaped like
// "... failed to build outbound config with tag out_3 > reason". Returns -1
// when the error cannot be pinned on a single outbound.
func failingOutbound(err error) int {
	const marker = "with tag "
	msg := err.Error()
	i := strings.Index(msg, marker)
	if i < 0 {
		return -1
	}
	tag := msg[i+len(marker):]
	if j := strings.Index(tag, " > "); j >= 0 {
		tag = tag[:j]
	}
	var idx int
	if _, err := fmt.Sscanf(strings.TrimSpace(tag), "out_%d", &idx); err != nil {
		return -1
	}
	return idx
}

// buildReason returns the leaf of xray's ">"-chained error message.
func buildReason(err error) string {
	parts := strings.Split(err.Error(), " > ")
	return strings.TrimSpace(parts[len(parts)-1])
}
