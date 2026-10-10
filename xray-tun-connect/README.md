# xray-tun-connect

Turns a list of Xray share links into local SOCKS5 tunnels, one port per link,
and reports the port map as a single JSON line. Built for programmatic use:
a parent process starts it, reads one line, uses the ports, and exits — the
tunnels go down with it.

Internally, links are parsed by libXray and served by one in-process
xray-core instance.

## Usage

```
xray-tun-connect -link <share-link> [-link ...]
xray-tun-connect -links "<link1>,<link2>"        # commas or newlines; # starts a comment
printf '%s\n\n' "$LINKS" | xray-tun-connect -stdin-links   # links stay out of `ps`
xray-tun-connect -version
```

| flag | default | meaning |
|---|---|---|
| `-link` | | share link, repeatable |
| `-links` | | newline- or comma-separated links (commas inside `alpn=h2,http/1.1` are safe) |
| `-stdin-links` | | read links from stdin, one per line, up to the first empty line |
| `-start-port` | `10000` | link *i* listens on `start-port + i` |
| `-listen` | `127.0.0.1` | bind address |
| `-xray-log-level` | `warning` | xray-core log level, written to stderr |
| `-ready-timeout` | `10s` | wait for inbounds to start listening |
| `-version` | | print semver and exit |

## Output contract

Exactly one JSON line on stdout, everything else on stderr:

```json
{"version":"0.1.0","listen":"127.0.0.1","tunnels":[
  {"index":0,"port":10000,"name":"MSK","protocol":"vless","ok":true},
  {"index":1,"port":10001,"ok":false,"error":"unsupported share format"},
  {"index":2,"port":10002,"name":"TRJ","protocol":"trojan","ok":true}
]}
```

A link that fails to parse or that xray-core rejects **keeps its index and
port** with `ok:false` — the port map never shifts because of one bad line.

Lifecycle: after the report the process serves until SIGINT/SIGTERM. When stdin
is a pipe, EOF on it also shuts the process down, so a parent that dies takes
the tunnels with it. Run it with `stdin=PIPE` and keep the pipe open.

With `-stdin-links` the same pipe carries the links first: write them one per
line, then an empty line, then keep the pipe open. Command-line arguments are
visible to every user via `ps`; stdin is not.

Exit codes: `0` clean shutdown, `1` core failed to start or no link was usable
(the report is still printed), `2` usage error.

## Build

Requires Go ≥ 1.27.1 (demanded by libXray; `go` auto-downloads the toolchain).

```
make install               # go install into $GOBIN / $GOPATH/bin (~/go/bin)
make build                 # ./xray-tun-connect for the current platform
make dist                  # static linux/amd64, linux/arm64, darwin/arm64
make test
```

The version lives in `cmd/xray-tun-connect/VERSION` and is embedded into the binary,
so plain `go install ./cmd/xray-tun-connect` reports it too.
