package logfmt

import (
	"bytes"
	"errors"
	"testing"
	"time"
)

func TestLine(t *testing.T) {
	cases := []struct {
		kv   []any
		want string
	}{
		{nil, "INFO start"},
		{[]any{"links", 2, "direct", true}, "INFO start links=2 direct=true"},
		{[]any{"reason", "dns timeout"}, `INFO start reason="dns timeout"`},
		{[]any{"name", ""}, `INFO start name=""`},
		{[]any{"q", `a"b`}, `INFO start q="a\"b"`},
		{[]any{"err", errors.New("x=y")}, `INFO start err="x=y"`},
		{[]any{"took", 1500 * time.Millisecond}, "INFO start took=1.5s"},
		{[]any{"dangling"}, "INFO start"},
	}
	for _, c := range cases {
		if got := Line(Info, "start", c.kv...); got != c.want {
			t.Errorf("Line(%v) = %q, want %q", c.kv, got, c.want)
		}
	}
}

func TestLoggerWritesLines(t *testing.T) {
	var buf bytes.Buffer
	l := New(&buf)
	l.Log(Warn, "a", "k", 1)
	l.Log(Error, "b")
	if got, want := buf.String(), "WARN a k=1\nERROR b\n"; got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
