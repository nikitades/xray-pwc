package tun

import (
	"bufio"
	"io"
	"reflect"
	"strings"
	"testing"
)

func TestSplitLinks(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want []string
	}{
		{"empty", "", nil},
		{"newlines with comments and blanks", "# top\n\nvless://a@h:1#x\n  \n# mid\ntrojan://b@h:2#y\n", []string{"vless://a@h:1#x", "trojan://b@h:2#y"}},
		{"commas", "vless://a@h:1#x, trojan://b@h:2#y", []string{"vless://a@h:1#x", "trojan://b@h:2#y"}},
		{"comma inside alpn is not a separator", "trojan://b@h:2?alpn=h2,http/1.1#y,ss://c@h:3#z", []string{"trojan://b@h:2?alpn=h2,http/1.1#y", "ss://c@h:3#z"}},
		{"mixed newlines and commas", "vless://a@h:1#x,vless://b@h:2#y\nvless://c@h:3#z", []string{"vless://a@h:1#x", "vless://b@h:2#y", "vless://c@h:3#z"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := SplitLinks(tt.in); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("SplitLinks(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestLinkName(t *testing.T) {
	tests := map[string]string{
		"vless://a@h:1#MSK-reality":              "MSK-reality",
		"vless://a@h:1#%C3%A9t%C3%A9":            "été",
		"vless://a@h:1":                          "",
		"vless://a@h:1?x=1#name%20with%20spaces": "name with spaces",
	}
	for link, want := range tests {
		if got := linkName(link); got != want {
			t.Errorf("linkName(%q) = %q, want %q", link, got, want)
		}
	}
}

func TestParseLink(t *testing.T) {
	ob, proto, err := parseLink("trojan://pw@198.51.100.12:443?security=tls&sni=t.example.com#x")
	if err != nil {
		t.Fatalf("parseLink: %v", err)
	}
	if proto != "trojan" || len(ob) == 0 {
		t.Errorf("got proto=%q, %d bytes of outbound", proto, len(ob))
	}
	if _, _, err := parseLink("garbage"); err == nil {
		t.Error("garbage link parsed without error")
	}
}

func TestReadLinkBlock(t *testing.T) {
	tests := []struct {
		name, in, want, rest string
	}{
		{"stops at the empty line", "vless://a@h:1#x\ntrojan://b@h:2#y\n\nleft for EOF watch", "vless://a@h:1#x\ntrojan://b@h:2#y\n", "left for EOF watch"},
		{"stops at EOF", "vless://a@h:1#x\ntrojan://b@h:2#y", "vless://a@h:1#x\ntrojan://b@h:2#y\n", ""},
		{"empty input", "", "", ""},
		{"immediately empty line", "\nrest", "", "rest"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := bufio.NewReader(strings.NewReader(tt.in))
			got, err := ReadLinkBlock(r)
			if err != nil {
				t.Fatal(err)
			}
			if got != tt.want {
				t.Errorf("ReadLinkBlock() = %q, want %q", got, tt.want)
			}
			rest, _ := io.ReadAll(r)
			if string(rest) != tt.rest {
				t.Errorf("left unread %q, want %q", rest, tt.rest)
			}
		})
	}
}

func TestDirectLink(t *testing.T) {
	ob, proto, err := parseLink(DirectLink)
	if err != nil || proto != "direct" || string(ob) != `{"protocol":"freedom"}` {
		t.Errorf("parseLink(DirectLink) = %s, %q, %v", ob, proto, err)
	}
	if got := linkName(DirectLink); got != "direct" {
		t.Errorf("linkName(DirectLink) = %q", got)
	}
}
