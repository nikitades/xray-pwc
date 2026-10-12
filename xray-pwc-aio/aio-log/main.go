// Command aio-log puts the time and a service name in front of every line it
// reads, and appends the lines to the shared log file and to stdout:
//
//	2026-10-12T15:41:23.500Z xtc-forward WARN link_unusable index=0 ...
//
// Every service of the container runs with its output piped through it:
//
//	program 2>&1 | aio-log <service>
//
// The file is opened for appending on every line, so logrotate's copytruncate
// and a removed file are both fine.
package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"time"
)

const defaultFile = "/app/logs/aio.log"

func main() {
	if len(os.Args) != 2 || os.Args[1] == "" {
		fmt.Fprintln(os.Stderr, "usage: aio-log <service>  (AIO_LOG_FILE sets the file, default "+defaultFile+")")
		os.Exit(2)
	}
	file := os.Getenv("AIO_LOG_FILE")
	if file == "" {
		file = defaultFile
	}
	copyLines(os.Stdin, os.Stdout, file, os.Args[1], time.Now)
}

func copyLines(in io.Reader, out io.Writer, file, service string, now func() time.Time) {
	r := bufio.NewReaderSize(in, 64*1024)
	for {
		line, err := r.ReadString('\n')
		if line != "" {
			if line[len(line)-1] != '\n' {
				line += "\n"
			}
			stamped := now().UTC().Format("2006-01-02T15:04:05.000Z") + " " + service + " " + line
			_, _ = io.WriteString(out, stamped)
			appendTo(file, stamped)
		}
		if err != nil {
			return
		}
	}
}

// appendTo never fails the service: a log line that cannot be written is
// still on stdout.
func appendTo(file, line string) {
	f, err := os.OpenFile(file, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return
	}
	_, _ = f.WriteString(line)
	_ = f.Close()
}
