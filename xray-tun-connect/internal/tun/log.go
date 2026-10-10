package tun

import (
	"fmt"
	"os"
	"strings"

	"github.com/xtls/xray-core/common/log"
)

// stderrHandler receives xray-core messages emitted before the core's own log
// app takes over (config building, protocol registration). The default handler
// prints to stdout, which is reserved for the JSON report. Deprecation notices
// are dropped: they repeat on every validation pass and are not actionable here.
type stderrHandler struct{}

func (stderrHandler) Handle(msg log.Message) {
	s := msg.String()
	if strings.Contains(s, "deprecated") {
		return
	}
	fmt.Fprintln(os.Stderr, "xray:", s)
}

func init() {
	log.RegisterHandler(stderrHandler{})
}
