// Package logfmt writes log lines shared by every xray-pwc service:
//
//	LEVEL event key=value key="value with spaces"
//
// The time and the service name are added in front by whatever collects the
// output (s6 on a probe), so a program only writes what it knows.
package logfmt

import (
	"fmt"
	"io"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	Debug = "DEBUG"
	Info  = "INFO"
	Warn  = "WARN"
	Error = "ERROR"
)

// Logger writes one line per call. Safe for concurrent use.
type Logger struct {
	mu sync.Mutex
	w  io.Writer
}

func New(w io.Writer) *Logger { return &Logger{w: w} }

// Log writes level, event and key/value pairs: kv is key, value, key, value...
func (l *Logger) Log(level, event string, kv ...any) {
	line := Line(level, event, kv...)
	l.mu.Lock()
	defer l.mu.Unlock()
	_, _ = io.WriteString(l.w, line+"\n")
}

// Line formats one line without the newline.
func Line(level, event string, kv ...any) string {
	var b strings.Builder
	b.WriteString(level)
	b.WriteByte(' ')
	b.WriteString(event)
	for i := 0; i+1 < len(kv); i += 2 {
		b.WriteByte(' ')
		b.WriteString(fmt.Sprint(kv[i]))
		b.WriteByte('=')
		b.WriteString(value(kv[i+1]))
	}
	return b.String()
}

func value(v any) string {
	var s string
	switch v := v.(type) {
	case string:
		s = v
	case error:
		s = v.Error()
	case time.Duration:
		s = v.String()
	default:
		s = fmt.Sprint(v)
	}
	if s == "" || strings.ContainsAny(s, " =\"\t\n\r") {
		return strconv.Quote(s)
	}
	return s
}
