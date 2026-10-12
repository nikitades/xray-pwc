# xray-tun-connect

Turns a list of Xray share links into local tunnels, in one of two modes:

- **All at once** (default): every link is a SOCKS5 proxy on its own port.
  The checker uses this mode.
- **Forward** (`-forward host:port`): one port forwards TCP and UDP to one
  address, through one link at a time. Every link is checked in the
  background; the port stays on a usable link and moves to the first usable
  one when it goes bad. A connection without a VPN is the last resort.

Built for programmatic use: a parent process starts it, reads the JSON
report, uses the ports, and exits — the tunnels go down with it.

Internally, links are parsed by libXray and served by in-process xray-core.

## Usage

```
xray-tun-connect -link <share-link> [-link ...]
xray-tun-connect -links "<link1>,<link2>"        # commas or newlines; # starts a comment
xray-tun-connect -stdin-links < links.txt         # links stay out of `ps`
xray-tun-connect -forward vpn.example.com:51973 -start-port 51974 -stdin-links < links.txt
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
| `-check-interval` | `15s` | forward mode: time between checks of every link |
| `-check-timeout` | `5s` | forward mode: timeout of one check |
| `-check-fails` | `3` | forward mode: failed checks in a row that make a link unusable |
| `-disable-direct` | | forward mode: no connection without a VPN as the last resort |
| `-xray-log-level` | `warning` | xray-core's own log level, written to stderr as is |
| `-ready-timeout` | `10s` | wait for inbounds to start listening |
| `-version` | | print semver and exit |

## Forward mode

With `-forward host:port`, there is one port, `-start-port`, and it is not a
SOCKS proxy: whatever comes to it, TCP or UDP, goes through the current link
to that one address. The far end sees the connection come from the VPN
server. The link's protocol must support UDP for UDP traffic, as VLESS,
VMess, Trojan and Shadowsocks do, and the server must allow it.

- **The check.** Every `-check-interval`, every link at once gets a DNS
  question over UDP, through the link, to 1.1.1.1 and 8.8.8.8; one answer is
  enough. It proves that the link carries UDP. It does not prove that the VPN
  server reaches the forward target, and it does not depend on anything at the
  target: a target that is down does not move the port.
- **The usable list.** A link that answers is usable. `-check-fails` failures
  in a row make it unusable, and it comes back with its next answer. Links
  that do not parse are rejected at the start and never become usable.
- **Which link serves.** The port stays on its link while that link is
  usable, even when an earlier one comes back. When it stops being usable,
  the port moves to the first usable link in the order given: the order is
  the priority.
- **`direct`.** The connection without a VPN is never checked: it is the
  last resort, used only while no link is usable, and the port leaves it as
  soon as one is. `-disable-direct` drops it; then, with no usable link, the
  port is closed and `no_tunnels` is logged every minute until a link comes
  back.
- **The port stays.** On a switch the port is gone only while the next link
  starts, under a second. A program using it sees a short gap and goes on.

## Output contract

**The default mode** prints one JSON report line on stdout, for the program
that started it:

```json
{"version":"0.5.1","listen":"127.0.0.1","tunnels":[
  {"index":0,"port":10000,"name":"MSK","protocol":"vless","ok":true},
  {"index":1,"port":10001,"ok":false,"error":"unsupported share format"},
  {"index":2,"port":10002,"name":"TRJ","protocol":"trojan","ok":true}
]}
```

A link that fails to parse or that xray-core rejects **keeps its index and
port** with `ok:false`: the port map never shifts because of one bad line.

**The log**, on stderr in both modes, is one line per event, in the format
every VpnChecker service shares: `LEVEL event key=value ...`, values with
spaces in quotes. Whatever collects it puts the time and the service in
front. Links appear by name only.

```
INFO start version=0.5.1 listen=127.0.0.1 port=51974 forward=vpn.example.com:51973 links=2 direct=true
WARN link_rejected index=2 name=old reason="unsupported share format"
INFO link_usable index=0 name=MSK
INFO active index=0 name=MSK
WARN link_unusable index=0 name=MSK fails=3 reason="dns timeout"
WARN switch from=MSK to=AMS reason=unusable
INFO switch from=direct to=MSK reason=link_recovered
ERROR no_tunnels usable=0 direct=off
```

| event | level | when |
|---|---|---|
| `start` | INFO | at start: what runs |
| `link_rejected` | WARN | a link does not parse or xray-core rejects it |
| `link_usable` / `link_unusable` | INFO / WARN | forward: a link enters or leaves the usable list |
| `active` | INFO | forward: the first link on the port |
| `switch` | WARN, INFO from direct | forward: the port moves to another link |
| `link_failed` | WARN | forward: the chosen link did not start on the port |
| `no_tunnels` | ERROR | no usable link, every minute. Forward: `direct=off` with `-disable-direct`, `direct=failed` when direct did not start either |
| `usage`, `start_failed`, `forward_failed` | ERROR | the process exits |

xray-core's own lines, at `-xray-log-level`, go to stderr as they are.

Lifecycle: after the report the process serves until SIGINT/SIGTERM. When stdin
is a pipe, EOF on it also shuts the process down, so a parent that dies takes
the tunnels with it. Run it with `stdin=PIPE` and keep the pipe open. From a
shell, give the links as a file on stdin, not a pipe: with
`printf ... | xray-tun-connect -stdin-links` it stops right after its report,
because `printf` closes the pipe.

With `-stdin-links` the same pipe carries the links first: write them one per
line, then an empty line, then keep the pipe open. Command-line arguments are
visible to every user via `ps`; stdin is not.

Where stderr is a journald socket (systemd), xray-core cannot open it for its
log: start fails with `open /dev/stderr: no such device or address`. Use
`-xray-log-level none` there.

Exit codes: `0` clean shutdown, `1` core failed to start (`start_failed`, no
report) or no link was usable (the report is printed first), `2` usage error.

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
