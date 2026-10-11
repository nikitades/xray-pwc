# xray-tun-connect

Turns a list of Xray share links into local tunnels, in one of two modes:

- **All at once** (default): every link is a SOCKS5 proxy on its own port.
  The checker uses this mode.
- **Forward** (`-forward host:port`): one port forwards TCP and UDP to one
  address, through one link at a time, and last through no VPN at all. With
  more than one candidate it checks the connection and moves the port to the
  next one when it fails.

Built for programmatic use: a parent process starts it, reads the JSON
report, uses the ports, and exits — the tunnels go down with it.

Internally, links are parsed by libXray and served by one in-process
xray-core instance.

## Usage

```
xray-tun-connect -link <share-link> [-link ...]
xray-tun-connect -links "<link1>,<link2>"        # commas or newlines; # starts a comment
printf '%s\n\n' "$LINKS" | xray-tun-connect -stdin-links   # links stay out of `ps`
xray-tun-connect -forward vpn.example.com:51973 -start-port 51974 -links "<link1>,<link2>" \
  -health-url http://10.213.97.1:9090/-/healthy
xray-tun-connect -version
```

| flag | default | meaning |
|---|---|---|
| `-link` | | share link, repeatable |
| `-links` | | newline- or comma-separated links (commas inside `alpn=h2,http/1.1` are safe) |
| `-stdin-links` | | read links from stdin, one per line, up to the first empty line |
| `-start-port` | `10000` | link *i* listens on `start-port + i`; in forward mode, the one port |
| `-listen` | `127.0.0.1` | bind address |
| `-forward` | | `host:port`: forward mode, see below |
| `-health-url` | | forward mode: URL to check, fetched directly from this machine; unset, `https://www.gstatic.com/generate_204` through the link |
| `-health-interval` | `15s` | forward mode: time between checks |
| `-health-timeout` | `5s` | forward mode: timeout of one check |
| `-health-fails` | `3` | forward mode: failed checks in a row before the next link takes the port |
| `-disable-direct` | | forward mode: no direct connection after the last link |
| `-xray-log-level` | `warning` | xray-core log level, written to stderr |
| `-ready-timeout` | `10s` | wait for inbounds to start listening |
| `-version` | | print semver and exit |

## Forward mode

With `-forward host:port`, there is one port, `-start-port`, and it is not a
SOCKS proxy: whatever comes to it, TCP or UDP, goes through the current link
to that one address. The far end sees the connection come from the VPN
server. The link's protocol must support UDP for UDP traffic, as VLESS,
VMess, Trojan and Shadowsocks do, and the server must allow it.

The links take the port one at a time, in order. After the last link comes
`direct`: a connection from this machine without any VPN, for the day every
link is down but the target is reachable anyway. `-disable-direct` drops it.
It shows in the report and the events as the last entry, named `direct`.

- **One candidate** (one link with `-disable-direct`): it just serves,
  nothing is checked.
- **Several:** every `-health-interval` a check runs. After
  `-health-fails` failures in a row, the next link takes the port, and after
  the last one, the first again. A link that does not start is skipped.
- **The check.** With `-health-url`, a plain GET from this machine; any
  status below 400 passes. Point it at something you can reach only through
  the forward, and it checks the whole way. On the probes this is Prometheus
  on the vpnc-net address: the request goes into WireGuard, through the
  forward and the link, and back. Without `-health-url`, a GET of
  `https://www.gstatic.com/generate_204` goes through the link itself: it
  proves the link carries a connection, not that the target answers.
- **The port stays.** On a switch the port is gone only while the next link
  starts, under a second. The program using it sees a short gap and goes on.

After the report, forward mode prints one JSON line per switch. `index` is
the link's position in the report:

```json
{"event":"active","index":0,"name":"MSK"}
{"event":"failed","index":0,"name":"MSK","reason":"health check failed 3 times in a row, last: ... timeout"}
{"event":"active","index":1,"name":"AMS"}
```

## Output contract

One JSON report line on stdout (in forward mode, followed by the switch
lines above), everything else on stderr:

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

Under systemd, stderr is a journald socket, and xray-core cannot open it for
its log: start fails with `open /dev/stderr: no such device or address`. Use
`-xray-log-level none` there; xray-tun-connect's own errors still go to
stderr.

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
