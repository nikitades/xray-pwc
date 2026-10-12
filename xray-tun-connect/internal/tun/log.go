package tun

import (
	"io"
	"os"
	"strings"

	"github.com/xtls/xray-core/common/log"

	"xray-tun-connect/internal/logfmt"
)

// stderrHandler receives xray-core messages emitted before the core's own log
// app takes over (config building, protocol registration). The default handler
// prints to stdout, which is reserved for the JSON report; these go to stderr
// as log lines. Deprecation notices
// are dropped: they repeat on every validation pass and are not actionable here.
type stderrHandler struct{}

func (stderrHandler) Handle(msg log.Message) {
	s := msg.String()
	if strings.Contains(s, "deprecated") {
		return
	}
	_, _ = io.WriteString(os.Stderr, logfmt.Line(logfmt.Warn, "xray", "msg", s)+"\n")
}

func init() {
	log.RegisterHandler(stderrHandler{})
}
