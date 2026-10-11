# xray-tun-pwc

**PWC = Pressurising Web Checker.** It checks Xray VPN links by putting real
pressure on them: it opens real websites through each link in Chromium, the
way a person would. Then it exports one result per link as Prometheus metrics.

## What one check does

Every few minutes, for every link:

1. [xray-tun-connect](../xray-tun-connect/) starts a local SOCKS5 tunnel for
   the link.
2. Chromium opens **heavy sites** through the tunnel. It picks a random site
   from the heavy list, `PWC_HEAVY_VISITS` times (3 by default). Heavy pages
   are big, so they catch connections that freeze after the first few
   kilobytes.
3. Chromium opens **every target site** through the tunnel, one after
   another. These are the sites people actually need.
4. The tunnel is closed. The next check starts with a fresh tunnel and a fresh
   browser.

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
| link broken | `0` | `failed` | the tunnel could not start from this link |
| unreachable | `1` | `failed` | nothing proves that traffic got through |
| partial | `2` | `partial` | traffic got through, but not every page opened |
| complete | `3` | `OK` | every page opened |

A failure is always a full failure. Only a success can be partial.

What proves that traffic got through the tunnel:

| what happened on a page | proves a connection |
|---|---|
| the page opened | yes |
| the site answered with any HTTP status | yes |
| a TLS handshake with the site failed | yes |
| the SOCKS connection was refused (`ERR_PROXY_CONNECTION_FAILED`) | no |
| timeout, reset, DNS error, anything unknown | no: could be a dead server or a stalled connection |

So a link where every page only timed out is `failed`, and a link where
every site answered `429` is `partial`.

## Output

On start it prints one line about itself, then one line per link after every
check:

```
xray-tun-pwc 0.1.0 | instance probe-1 | 3 tunnels | every PT5M + up to PT2M
msk-reality - OK
spb-ws - partial[instagram.com: HTTP 429, thesun.co.uk: ERR_CONNECTION_RESET]
backup - failed[tunnel: unsupported share format]
```

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

```promql
pwc_tunnel_stage >= 2                          # the link works, fully or partly
count by (tunnel) (pwc_tunnel_stage == 2)      # probes where a link is only partial
pwc_tunnel_error == 1                          # what is failing right now, and where
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

Sites are full URLs, not just domains, so you can check one exact page:

```bash
export PWC_TARGET_SITES='["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://t.me/durov"]'
export PWC_HEAVY_VISITS=5
```

Without `PWC_XRAY_TUN_CONNECT_BIN`, the binary is looked up in `$GOBIN`,
then `$GOPATH/bin` (`~/go/bin` by default), then `PATH`. If it is missing or too old, the program stops with an error.
When it is missing, the error also says how to install it.

## Run it

### Locally

```bash
make -C ../xray-tun-connect install
uv sync
uv run playwright install chromium
PWC_CONFIG_PATH=links.txt uv run xray-tun-pwc
```

Stop it with Ctrl+C or `SIGTERM`. It finishes cleanly and closes the tunnels.

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

On real probes, use the Ansible playbook `probes-pwc.yml`. See
[`../ansible/README.md`](../ansible/README.md).

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
