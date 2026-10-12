package tun

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"time"
)

// checkResolvers get the DNS question of a link check. One answer is enough,
// so a VPN server that blocks one of them still passes.
var checkResolvers = []netip.AddrPort{
	netip.MustParseAddrPort("1.1.1.1:53"),
	netip.MustParseAddrPort("8.8.8.8:53"),
}

// dnsCheck asks the resolvers a DNS question over UDP through the SOCKS5
// proxy at socks, i.e. through one link: an answer proves the link carries
// UDP. The deadline comes from ctx.
func dnsCheck(ctx context.Context, socks string) error {
	var id [2]byte
	_, _ = rand.Read(id[:])
	return socksUDPExchange(ctx, socks, checkResolvers, dnsQuery(id), func(answer []byte) bool {
		return len(answer) >= 12 && answer[0] == id[0] && answer[1] == id[1] && answer[2]&0x80 != 0
	})
}

// dnsQuery is "NS of the root zone", the smallest question every resolver answers.
func dnsQuery(id [2]byte) []byte {
	return []byte{
		id[0], id[1],
		0x01, 0x00, // recursion desired
		0x00, 0x01, // one question
		0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0x00,       // the root name
		0x00, 0x02, // NS
		0x00, 0x01, // IN
	}
}

// socksUDPExchange sends payload to every target through a SOCKS5 UDP
// association (RFC 1928) and waits for one reply that ok accepts.
func socksUDPExchange(ctx context.Context, socks string, targets []netip.AddrPort, payload []byte, ok func([]byte) bool) error {
	deadline, has := ctx.Deadline()
	if !has {
		deadline = time.Now().Add(5 * time.Second)
	}
	var d net.Dialer
	ctrl, err := d.DialContext(ctx, "tcp", socks)
	if err != nil {
		return err
	}
	// The association lives as long as this connection.
	defer ctrl.Close()
	_ = ctrl.SetDeadline(deadline)

	if _, err := ctrl.Write([]byte{5, 1, 0}); err != nil {
		return err
	}
	var greet [2]byte
	if _, err := io.ReadFull(ctrl, greet[:]); err != nil {
		return fmt.Errorf("socks greeting: %w", err)
	}
	if greet != [2]byte{5, 0} {
		return fmt.Errorf("socks greeting: method %d refused", greet[1])
	}
	if _, err := ctrl.Write([]byte{5, 3, 0, 1, 0, 0, 0, 0, 0, 0}); err != nil {
		return err
	}
	relay, err := readSocksReply(ctrl)
	if err != nil {
		return fmt.Errorf("socks udp associate: %w", err)
	}
	if relay.Addr().IsUnspecified() {
		host, err := netip.ParseAddrPort(ctrl.RemoteAddr().String())
		if err != nil {
			return err
		}
		relay = netip.AddrPortFrom(host.Addr(), relay.Port())
	}

	conn, err := net.DialUDP("udp", nil, net.UDPAddrFromAddrPort(relay))
	if err != nil {
		return err
	}
	defer conn.Close()
	_ = conn.SetDeadline(deadline)
	for _, target := range targets {
		if _, err := conn.Write(append(socksUDPHeader(target), payload...)); err != nil {
			return err
		}
	}

	buf := make([]byte, 2048)
	for {
		n, err := conn.Read(buf)
		if err != nil {
			if errors.Is(err, net.ErrClosed) || isTimeout(err) {
				return errors.New("dns timeout")
			}
			return err
		}
		if data, err := socksUDPPayload(buf[:n]); err == nil && ok(data) {
			return nil
		}
	}
}

func readSocksReply(r io.Reader) (netip.AddrPort, error) {
	var head [4]byte
	if _, err := io.ReadFull(r, head[:]); err != nil {
		return netip.AddrPort{}, err
	}
	if head[0] != 5 || head[1] != 0 {
		return netip.AddrPort{}, fmt.Errorf("reply code %d", head[1])
	}
	var size int
	switch head[3] {
	case 1:
		size = 4
	case 4:
		size = 16
	default:
		return netip.AddrPort{}, fmt.Errorf("unexpected address type %d", head[3])
	}
	buf := make([]byte, size+2)
	if _, err := io.ReadFull(r, buf); err != nil {
		return netip.AddrPort{}, err
	}
	addr, _ := netip.AddrFromSlice(buf[:size])
	return netip.AddrPortFrom(addr.Unmap(), binary.BigEndian.Uint16(buf[size:])), nil
}

func socksUDPHeader(target netip.AddrPort) []byte {
	h := []byte{0, 0, 0}
	if target.Addr().Is4() {
		h = append(h, 1)
	} else {
		h = append(h, 4)
	}
	h = append(h, target.Addr().AsSlice()...)
	return binary.BigEndian.AppendUint16(h, target.Port())
}

func socksUDPPayload(p []byte) ([]byte, error) {
	if len(p) < 4 || p[2] != 0 {
		return nil, errors.New("short or fragmented datagram")
	}
	var skip int
	switch p[3] {
	case 1:
		skip = 4 + 4 + 2
	case 4:
		skip = 4 + 16 + 2
	case 3:
		if len(p) < 5 {
			return nil, errors.New("short datagram")
		}
		skip = 4 + 1 + int(p[4]) + 2
	default:
		return nil, errors.New("bad address type")
	}
	if len(p) < skip {
		return nil, errors.New("short datagram")
	}
	return p[skip:], nil
}

func isTimeout(err error) bool {
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout()
}
