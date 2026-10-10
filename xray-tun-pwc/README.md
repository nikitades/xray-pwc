# xray-tun-pwc

**PWC = Pressurising Web Checker.** It checks Xray VPN links by putting real
pressure on them: it opens real websites through each link in Chromium, the
way a person would. Then it exports one result per link as Prometheus metrics.

## What one check does

Every few minutes, for every link:

1. [xray-tun-connect](../xray-tun-connect/) starts a local SOCKS5 tunnel for
   the link.
2. Chromium opens **heavy sites** through the tunnel. It picks a random site
   from the heavy list, `XRC_HEAVY_VISITS` times (3 by default). Heavy pages
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

| result | meaning | `xrc_tunnel_up` | `perfect` label |
|---|---|---|---|
| `OK` | every page opened | 1 | `true` |
| `partial` | some pages opened, some did not | 1 | `false` |
| `failed` | nothing opened, or the link itself is broken | 0 | `false` |

## Output

On start it prints one line about itself, then one line per link after every
check:

```
xray-tun-pwc 0.1.0 | instance probe-1 | 3 tunnels | every PT5M + up to PT2M
msk-reality - OK
spb-ws - partial[instagram.com: HTTP 429]
backup - failed[tunnel: unsupported share format]
```

Errors are short words, so the log is easy to read:

| word | what happened |
|---|---|
| `tunnel` | the local tunnel or the VPN server refused the connection |
| `timeout` | the page did not load in time |
| `reset` | the connection was closed in the middle |
| `tls` | the TLS handshake failed or the certificate is wrong |
| `dns` | the site name could not be resolved |
| `HTTP 429` | the site answered, but not with `200` |

## Metrics

On `http://<host>:8000/metrics`:

```
xrc_tunnel_up{instance="probe-1",tunnel="msk-reality",stable_id="3f9c...",perfect="true"} 1.0
xrc_tunnel_up{instance="probe-1",tunnel="spb-ws",stable_id="8a01...",perfect="false"} 1.0
```

- `instance`: the name of this probe (`XRC_INSTANCE`). Compare probes to see
  whether the problem is the VPN, one ISP, or one probe.
- `tunnel`: the link's name, the text after `#` in the link.
- `stable_id`: a hash of the link without its name. Renaming a link does not
  break its history in Prometheus.
- `perfect`: `true` when every page opened, `false` otherwise.

Each link has one series at a time. When `perfect` changes, Prometheus sees a
new series and the old one ends. To draw one continuous line per link, drop
the label:

```promql
max without (perfect) (xrc_tunnel_up)          # is the link up at all
count by (tunnel) (xrc_tunnel_up{perfect="false"} == 1)   # probes where a link is only partial
```

## Settings

All settings are environment variables, and all are optional.

| variable | default | meaning |
|---|---|---|
| `XRC_CONFIG_PATH` | `config.txt` | file with share links, one per line, `#` for comments |
| `XRC_INSTANCE` | hostname | this probe's name in metrics and logs |
| `XRC_HEAVY_SITES` | Wikipedia random page, The Sun, site123 examples | JSON list of URLs |
| `XRC_HEAVY_VISITS` | `3` | how many random heavy sites to open per check; `0` skips them |
| `XRC_TARGET_SITES` | Instagram, Facebook | JSON list of URLs, all opened every check |
| `XRC_CHECK_INTERVAL` | `PT5M` | time between checks, ISO 8601 |
| `XRC_CHECK_JITTER` | `PT2M` | random extra wait, so probes don't all check at once |
| `XRC_HTTP_PORT` | `8000` | port for `/metrics` |
| `XRC_XRAY_TUN_CONNECT_BIN` | auto | path to `xray-tun-connect` |

Sites are full URLs, not just domains, so you can check one exact page:

```bash
export XRC_TARGET_SITES='["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://t.me/durov"]'
export XRC_HEAVY_VISITS=5
```

Without `XRC_XRAY_TUN_CONNECT_BIN`, the binary is looked up in `$GOBIN`,
then `$GOPATH/bin` (`~/go/bin` by default), then `PATH`. If it is missing or too old, the program stops with an error.
When it is missing, the error also says how to install it.

## Run it

### Locally

```bash
make -C ../xray-tun-connect install
uv sync
uv run playwright install chromium
XRC_CONFIG_PATH=links.txt uv run xray-tun-pwc
```

Stop it with Ctrl+C or `SIGTERM`. It finishes cleanly and closes the tunnels.

### In Docker

The image has Chromium and `xray-tun-connect` inside. Give the build the Go
sources as a second build context:

```bash
docker buildx build --build-context xray-tun-connect=../xray-tun-connect -t xray-tun-pwc --load .
docker run --rm --shm-size 512m \
  -v "$PWD/links.txt:/config/links.txt:ro" \
  -e XRC_CONFIG_PATH=/config/links.txt -e XRC_INSTANCE=probe-1 \
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
