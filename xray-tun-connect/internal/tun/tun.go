// Package tun starts one xray-core instance that exposes every share link as
// a local SOCKS5 inbound (or a forward to one address) on a predictable port.
package tun

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strconv"
	"time"

	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf/serial"

	// Registers every protocol, transport and feature xray-core knows about;
	// without this import the core refuses even a plain SOCKS inbound.
	_ "github.com/xtls/xray-core/main/distro/all"
)

type Options struct {
	Links        []string
	StartPort    int
	Listen       string
	XrayLogLevel string
	ReadyTimeout time.Duration
	// Forward, when set, makes every inbound send everything it gets to this
	// one address through its link, instead of being a SOCKS proxy.
	Forward *Target
	// CheckPort, when set, adds a SOCKS inbound on 127.0.0.1 for the first
	// link, for Forward's health probe through the link.
	CheckPort int
}

// Target is a host and port at the far end of a forward.
type Target struct {
	Host string
	Port int
}

// ParseTarget reads host:port.
func ParseTarget(s string) (*Target, error) {
	host, portStr, err := net.SplitHostPort(s)
	if err != nil {
		return nil, err
	}
	port, err := strconv.Atoi(portStr)
	if err != nil || port < 1 || port > 65535 || host == "" {
		return nil, fmt.Errorf("%q is not host:port", s)
	}
	return &Target{Host: host, Port: port}, nil
}

// Tunnel is one entry of the report. A link that could not be used keeps its
// index and port with OK=false, so ports never shift because of bad input.
type Tunnel struct {
	Index    int    `json:"index"`
	Port     int    `json:"port"`
	Name     string `json:"name,omitempty"`
	Protocol string `json:"protocol,omitempty"`
	OK       bool   `json:"ok"`
	Error    string `json:"error,omitempty"`
}

type Result struct {
	Tunnels  []Tunnel
	instance *core.Instance
}

// Alive returns how many tunnels are actually serving.
func (r *Result) Alive() int {
	n := 0
	for _, t := range r.Tunnels {
		if t.OK {
			n++
		}
	}
	return n
}

func (r *Result) Close() error {
	if r.instance == nil {
		return nil
	}
	err := r.instance.Close()
	r.instance = nil
	return err
}

// Start parses the links, drops the ones xray-core rejects, boots the core
// and waits until every surviving inbound accepts connections.
func Start(ctx context.Context, opts Options) (*Result, error) {
	if len(opts.Links) == 0 {
		return nil, errors.New("no links")
	}
	if opts.Listen == "" {
		opts.Listen = "127.0.0.1"
	}
	if opts.XrayLogLevel == "" {
		opts.XrayLogLevel = "warning"
	}
	if opts.ReadyTimeout <= 0 {
		opts.ReadyTimeout = 10 * time.Second
	}

	tunnels := make([]Tunnel, len(opts.Links))
	outbounds := make([]json.RawMessage, len(opts.Links))
	for i, link := range opts.Links {
		t := Tunnel{Index: i, Port: opts.StartPort + i, Name: linkName(link)}
		ob, proto, err := parseLink(link)
		if err != nil {
			t.Error = err.Error()
		} else {
			t.Protocol, t.OK = proto, true
			outbounds[i] = ob
		}
		tunnels[i] = t
	}

	// Rebuild until xray-core accepts the config, excluding one offender per round.
	var cfg []byte
	for {
		var err error
		cfg, err = buildConfig(tunnels, outbounds, opts)
		if err != nil {
			return nil, err
		}
		buildErr := validateBuild(cfg)
		if buildErr == nil {
			break
		}
		idx := failingOutbound(buildErr)
		if idx < 0 || idx >= len(tunnels) || !tunnels[idx].OK {
			return nil, fmt.Errorf("xray config rejected: %w", buildErr)
		}
		tunnels[idx].OK = false
		tunnels[idx].Error = buildReason(buildErr)
	}

	res := &Result{Tunnels: tunnels}
	if res.Alive() == 0 {
		return res, nil
	}

	decoded, err := serial.DecodeJSONConfig(bytes.NewReader(cfg))
	if err != nil {
		return nil, err
	}
	coreCfg, err := decoded.Build()
	if err != nil {
		return nil, err
	}
	inst, err := core.New(coreCfg)
	if err != nil {
		return nil, fmt.Errorf("create xray instance: %w", err)
	}
	if err := inst.Start(); err != nil {
		return nil, fmt.Errorf("start xray: %w", err)
	}
	res.instance = inst

	if err := waitReady(ctx, res, opts); err != nil {
		_ = res.Close()
		return nil, err
	}
	return res, nil
}

func waitReady(ctx context.Context, res *Result, opts Options) error {
	deadline := time.Now().Add(opts.ReadyTimeout)
	for i := range res.Tunnels {
		t := &res.Tunnels[i]
		if !t.OK {
			continue
		}
		addr := net.JoinHostPort(opts.Listen, strconv.Itoa(t.Port))
		for {
			conn, err := net.DialTimeout("tcp", addr, 200*time.Millisecond)
			if err == nil {
				conn.Close()
				break
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if time.Now().After(deadline) {
				return fmt.Errorf("inbound %s did not start listening within %s", addr, opts.ReadyTimeout)
			}
			time.Sleep(50 * time.Millisecond)
		}
	}
	return nil
}
