package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCopyLinesStampsAndAppends(t *testing.T) {
	file := filepath.Join(t.TempDir(), "aio.log")
	if err := os.WriteFile(file, []byte("old\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	at := time.Date(2026, 10, 12, 15, 41, 23, 500_000_000, time.FixedZone("x", 3600))
	var out bytes.Buffer
	copyLines(strings.NewReader("INFO start a=1\nWARN no newline"), &out, file, "probe", func() time.Time { return at })

	want := "2026-10-12T14:41:23.500Z probe INFO start a=1\n2026-10-12T14:41:23.500Z probe WARN no newline\n"
	if out.String() != want {
		t.Errorf("stdout = %q, want %q", out.String(), want)
	}
	got, _ := os.ReadFile(file)
	if string(got) != "old\n"+want {
		t.Errorf("file = %q", got)
	}
}

// A truncated or removed file (logrotate) gets the next lines from the start.
func TestCopyLinesSurvivesRotation(t *testing.T) {
	file := filepath.Join(t.TempDir(), "aio.log")
	now := func() time.Time { return time.Unix(0, 0) }
	copyLines(strings.NewReader("a\n"), &bytes.Buffer{}, file, "s", now)
	if err := os.Remove(file); err != nil {
		t.Fatal(err)
	}
	copyLines(strings.NewReader("b\n"), &bytes.Buffer{}, file, "s", now)
	got, _ := os.ReadFile(file)
	if string(got) != "1970-01-01T00:00:00.000Z s b\n" {
		t.Errorf("file = %q", got)
	}
}
