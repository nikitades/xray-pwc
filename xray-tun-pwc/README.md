# xray-tun-pwc

**PWC = Pressurising Web Checker.** It checks Xray VPN links by putting real
pressure on them: it opens real websites through each link in Chromium, the
way a person would. Then it exports one result per link as Prometheus metrics.

## What one check does

Every few minutes, one round:

1. [xray-tun-connect](../xray-tun-connect/) starts a local SOCKS5 tunnel for
   every link at once, each on its own port. A link it cannot start is
   `link_broken` and skipped.
2. For each link in turn, with a fresh browser, Chromium opens **heavy
   sites** through its tunnel. It picks a random site from the heavy list,
   `PWC_HEAVY_VISITS` times (3 by default). Heavy pages are big, so they catch
   connections that freeze after the first few kilobytes.
3. Then **every target site**, one after another. These are the sites people
   actually need.
4. After the last link the tunnels are closed. The next round starts with
   fresh tunnels.

Every page gets up to three tries. The wait between tries doubles: 2 s, then
4 s.

### When does a page count as opened?

| kind | opened when | why |
|---|---|---|
| heavy site | the page reached `DOMContentLoaded` | we want its bytes to pass through the tunnel |
| target site | the response is HTTP `200` | a login wall is fine: it proves the site is reachable |

### The result for a link

The result is the furthest stage the check reached:

| stage | `pwc_tunnel_stage` | result | meaning |
|---|---|---|---|
| link broken | `0` | `link_broken` | the tunnel could not start from this link |
| unreachable | `1` | `unreachable` | nothing proves that traffic got through |
| partial | `2` | `partial` | traffic got through, but not every page opened |
| complete | `3` | `ok` | every page opened |

A failure is always a full failure. Only a success can be partial.

What proves that traffic got through the tunnel:

| what happened on a page | proves a connection |
|---|---|
| the page opened | yes |
| the site answered with any HTTP status | yes |
| a TLS handshake with the site failed | yes |
| the SOCKS connection was refused (`ERR_PROXY_CONNECTION_FAILED`) | no |
| timeout, reset, DNS error, anything unknown | no: could be a dead server or a stalled connection |

So a link where every page only timed out is `unreachable`, and a link where
every site answered `429` is `partial`.

### Why heavy pages

A check that fetches a tiny page, such as `generate_204`, sends a few hundred
bytes. Some DPI setups, notably in Russia, let a TCP connection to certain
foreign networks start normally and then stall it after roughly 16 KB. A tiny
request passes; a real page does not. We hit exactly this on the probes' own
metrics traffic, before it went into the probes' WireGuard tunnel. Heavy pages
push enough bytes through the tunnel to cross that threshold.

## What it does not catch

A check answers one question: did the pages load through the tunnel.

- **Speed.** Slow throttling that still fits in the 30 s timeout looks fine.
- **Everything but the main page.** Images, scripts and CDNs are not checked:
  a target site passes on its HTML, a heavy site even with a `403`.
- **Bots versus people.** Headless Chromium is not a real user. Sites may
  answer it with a captcha or `429`, which is rate limiting, not censorship.
- **Short outages.** Problems shorter than the check interval can be missed.
- **Who is to blame.** Comparing probes hints at it (the provider, the VPN
  server or the probe), but it is a guess, and it needs more than one probe.

## What it costs

- **Traffic.** Every check of every link downloads the HTML of
  `PWC_HEAVY_VISITS` heavy pages and all target pages, plus whatever the
  browser fetches before `DOMContentLoaded`. On a metered connection, choose
  the number of links, the visits and the interval with that in mind.
- **Image size.** The image contains Chromium, so it is large. On probes it
  is the `xray-pwc-aio` image, pulled once from a registry; Ansible then sends
  only the code, built on your machine, and only when it changes.
- **CPU and memory.** One Chromium at a time. Links are checked one after
  another, not in parallel.

## Output

It logs to stderr in the format every VpnChecker service shares:
`LEVEL event key=value ...`, values with spaces in quotes. On a probe, the
time and the service name are put in front. One line on start, then one per
link after every check, with `result` and `stage` as in the metrics:

```
INFO start version=0.2.1 instance=probe-1 links=3 interval=PT5M jitter=PT2M
INFO check index=0 name=msk-reality result=ok stage=3
WARN check index=1 name=spb-ws result=partial stage=2 failed="instagram.com: HTTP 429, thesun.co.uk: ERR_CONNECTION_RESET"
ERROR check index=2 name=backup result=link_broken stage=0 failed="tunnel: unsupported share format"
```

`result` is `ok` (INFO), `partial` (WARN), `unreachable` or `link_broken`
(ERROR). The lines of xray-tun-connect, which the checker starts, come
through with `xtc.` in front of their event, for example
`WARN xtc.link_rejected index=2 name=backup reason="..."`; a line not in this
format becomes `WARN xtc.output msg="..."`.

Two errors are not about a link:

- `ERROR xtc_unavailable` at the start: xray-tun-connect is missing or too
  old. The checker stops.
- `ERROR round_failed err="..."`: xray-tun-connect itself failed in a round,
  for example it printed no report in time or its port was taken. The round
  is skipped, the metrics keep the last values, and the next round tries
  again.

Every error is logged by its own name, never grouped with similar ones:

| error | what happened |
|---|---|
| `ERR_...` | Chromium's network error code as is, for example `ERR_CONNECTION_RESET`, `ERR_CONNECTION_CLOSED`, `ERR_EMPTY_RESPONSE`, `ERR_PROXY_CONNECTION_FAILED`, `ERR_SSL_PROTOCOL_ERROR` |
| `timeout` | the page did not load in time and Chromium reported no error |
| `HTTP 429` | the site answered, but not with `200` |
| anything else | an error the checker does not know, printed in full |

## Metrics

On `http://<host>:8000/metrics`, one series per link. The value is the stage
from the table above:

```
pwc_tunnel_stage{instance="probe-1",tunnel="msk-reality",stable_id="3f9c..."} 3
pwc_tunnel_stage{instance="probe-1",tunnel="spb-ws",stable_id="8a01..."} 2
```

- `instance`: the name of this probe (`PWC_INSTANCE`). Compare probes to see
  whether the problem is the VPN, one ISP, or one probe.
- `tunnel`: the link's name, the text after `#` in the link.
- `stable_id`: a hash of the link without its name. Renaming a link does not
  break its history in Prometheus.

The series stays the same when the stage changes, so graphs never break.

Next to it, `pwc_tunnel_error` has one series per link and error type:

```
pwc_tunnel_error{instance="probe-1",tunnel="spb-ws",stable_id="8a01...",error_type="ERR_CONNECTION_RESET"} 1
pwc_tunnel_error{instance="probe-1",tunnel="spb-ws",stable_id="8a01...",error_type="HTTP 429"} 0
```

- `error_type`: the error as in the log (`ERR_...`, `timeout`, `HTTP 429`),
  `unrecognized` for an unknown error, `link_broken` when the tunnel could not
  start.
- The value is `1` if the last check met this error, `0` if only an earlier
  check did. A link that never failed has no series.

In Grafana, a State timeline panel with value mappings (`0` and `1` red, `2`
yellow, `3` green) shows the whole history at a glance. Useful queries:

```promql
# links that work, fully or partly
pwc_tunnel_stage >= 2

# probes where a link is only partial
count by (tunnel) (pwc_tunnel_stage == 2)

# links that fail on every probe: likely the VPN server
max by (tunnel) (pwc_tunnel_stage) < 2

# probes where every link fails: likely the probe or its provider
max by (instance) (pwc_tunnel_stage) < 2

# what is failing right now, and where
pwc_tunnel_error == 1

# share of checks in the last day where the link worked
avg_over_time((pwc_tunnel_stage >= bool 2)[1d:5m])
```

## Settings

All settings are environment variables, and all are optional.

| variable | default | meaning |
|---|---|---|
| `PWC_CONFIG_PATH` | `config.txt` | file with share links, one per line, `#` for comments |
| `PWC_INSTANCE` | hostname | this probe's name in metrics and logs |
| `PWC_HEAVY_SITES` | Wikipedia random page, The Sun, site123 examples | JSON list of URLs |
| `PWC_HEAVY_VISITS` | `3` | how many random heavy sites to open per check; `0` skips them |
| `PWC_TARGET_SITES` | Instagram, Facebook | JSON list of URLs, all opened every check |
| `PWC_CHECK_INTERVAL` | `PT5M` | time between checks, ISO 8601 |
| `PWC_CHECK_JITTER` | `PT2M` | random extra wait, so probes don't all check at once |
| `PWC_HTTP_PORT` | `8000` | port for `/metrics` |
| `PWC_XRAY_TUN_CONNECT_BIN` | auto | path to `xray-tun-connect` |

Share links contain credentials: keep the links file readable only by the
checker. It passes them to `xray-tun-connect` over stdin, so they do not
show up in the process list.

Sites are full URLs, not just domains, so you can check one exact page:

```bash
export PWC_TARGET_SITES='["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://t.me/durov"]'
export PWC_HEAVY_VISITS=5
```

Without `PWC_XRAY_TUN_CONNECT_BIN`, the binary is looked up in `$GOBIN` if
it is set, otherwise in `$GOPATH/bin` (`~/go/bin` by default), then in `PATH`. If it is missing or too old, the program stops with an error.
When it is missing, the error also says how to install it.

## Run it

### Locally

You need [uv](https://docs.astral.sh/uv/), Python 3.14 and the
`xray-tun-connect` binary. Build it (Go 1.27+):

```bash
make -C ../xray-tun-connect install       # into ~/go/bin
```

or `make -C ../xray-tun-connect build` and point the checker at
`../xray-tun-connect/xray-tun-connect` with `PWC_XRAY_TUN_CONNECT_BIN=<path>`.
There are no prebuilt binaries. Then:

```bash
uv sync
uv run playwright install chromium
echo 'vless://...your link...#my-server' > links.txt
PWC_CONFIG_PATH=links.txt uv run xray-tun-pwc
```

A check from your machine only tells you that the link works from your
network. Stop it with Ctrl+C or `SIGTERM`. It finishes cleanly and closes the tunnels.

### In Docker

The image has Chromium and `xray-tun-connect` inside. Give the build the Go
sources as a second build context:

```bash
docker buildx build --build-context xray-tun-connect=../xray-tun-connect -t xray-tun-pwc --load .
docker run --rm --shm-size 512m \
  -v "$PWD/links.txt:/config/links.txt:ro" \
  -e PWC_CONFIG_PATH=/config/links.txt -e PWC_INSTANCE=probe-1 \
  -p 8000:8000 xray-tun-pwc
```

`--shm-size` gives Chromium more shared memory. Docker's default is 64 MB,
and Chromium can run out of it on big pages.

On real probes the checker runs inside the `xray-pwc-aio` container
([`../xray-pwc-aio/README.md`](../xray-pwc-aio/README.md)), deployed by
the Ansible playbook `probes.yml` ([`../ansible/README.md`](../ansible/README.md)).
This Dockerfile is for trying it on your own machine.

## Development

```bash
uv run pytest
uv run ruff check .
uvx ty check src
```

Code layout:

```
src/xray_tun_pwc/
  domain/        links, tunnels, sites, visit errors, schedule
  app/           use cases and the ports (Protocols) they need
  ports/         real adapters: xray-tun-connect, Playwright, Prometheus
  entrypoints/   settings and the CLI loop
  container.py   wiring: build_container() and build_test_container()
  fakes.py       fake adapters for tests
```

Tests live next to the code they test: `browser.py` and `browser_test.py`.
A use case test takes a container where every adapter is already a fake,
changes one fake if it needs to, and runs the use case from the container:

```python
async def test_rejects_older(container: Container):
    container[XrayTunConnect] = FakeXrayTunConnect(reported_version="0.0.0")
    with pytest.raises(XrayTunConnectTooOld):
        await container[CheckXrayTunConnectVersionUsecase].handle(MINIMUM)
```
