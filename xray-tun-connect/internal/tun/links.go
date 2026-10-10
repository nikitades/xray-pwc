package tun

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strings"

	"github.com/xtls/libxray/share"
)

// SplitLinks splits a blob of share links on newlines and commas. Blank lines
// and lines starting with "#" are dropped.
//
// Commas are only treated as separators when the next piece starts a new
// link: query values such as alpn=h2,http/1.1 legitimately contain commas.
func SplitLinks(blob string) []string {
	var out []string
	for line := range strings.SplitSeq(blob, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		for _, piece := range strings.Split(line, ",") {
			piece = strings.TrimSpace(piece)
			if piece == "" {
				continue
			}
			if len(out) > 0 && !looksLikeLink(piece) && sameLine(out[len(out)-1], line) {
				out[len(out)-1] += "," + piece
				continue
			}
			out = append(out, piece)
		}
	}
	return out
}

// ReadLinkBlock reads share links from r, one per line, up to the first empty
// line or EOF, and returns them as a blob for SplitLinks. The rest of r is left
// unread, so the caller can keep watching it for EOF.
func ReadLinkBlock(r *bufio.Reader) (string, error) {
	var b strings.Builder
	for {
		line, err := r.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return "", err
		}
		if strings.TrimSpace(line) == "" {
			return b.String(), nil
		}
		b.WriteString(strings.TrimRight(line, "\r\n") + "\n")
		if err != nil {
			return b.String(), nil
		}
	}
}

func sameLine(prev, line string) bool { return strings.Contains(line, prev) }

func looksLikeLink(s string) bool {
	i := strings.Index(s, "://")
	if i <= 0 {
		return false
	}
	for _, r := range s[:i] {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '+' || r == '-' || r == '.') {
			return false
		}
	}
	return true
}

// linkName returns the display name of a share link: its URL fragment,
// percent-decoded. Empty when the link carries none.
func linkName(link string) string {
	i := strings.LastIndex(link, "#")
	if i < 0 {
		return ""
	}
	name, err := url.PathUnescape(link[i+1:])
	if err != nil {
		return link[i+1:]
	}
	return name
}

// parseLink converts one share link into a raw xray outbound and reports its
// protocol. Links are parsed one at a time so a bad one is attributed to its
// own index instead of being silently dropped from a batch.
func parseLink(link string) (outbound json.RawMessage, protocol string, err error) {
	cfg, err := share.ConvertShareLinksToXrayJson(link, "")
	if err != nil {
		return nil, "", err
	}
	var doc struct {
		Outbounds []json.RawMessage `json:"outbounds"`
	}
	if err := json.Unmarshal(cfg, &doc); err != nil {
		return nil, "", fmt.Errorf("decode libxray output: %w", err)
	}
	if len(doc.Outbounds) != 1 {
		return nil, "", fmt.Errorf("expected exactly one outbound, got %d", len(doc.Outbounds))
	}
	var head struct {
		Protocol string `json:"protocol"`
	}
	if err := json.Unmarshal(doc.Outbounds[0], &head); err != nil || head.Protocol == "" {
		return nil, "", errors.New("outbound has no protocol")
	}
	return doc.Outbounds[0], head.Protocol, nil
}
