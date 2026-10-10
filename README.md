# VpnChecker

Monitoring for Xray VPN links, measured from the networks your users are in.

You put small machines (probes) inside the networks you care about, for
example a home ISP in a given city. Every few minutes each probe opens a set
of web pages through every VPN link, in a headless Chromium, and reports
whether they loaded. Grafana Alloy on each probe pushes the results to a
central Prometheus with remote write, and you read them in Grafana.

The project is young (`0.1.0`) and has been run against a small number of
probes. Treat the defaults as a starting point, not as tuned values.

## What it measures

A check of one link, on one probe, is:

1. Start a local SOCKS5 tunnel for the link (this is a simplified version of a
   real Xray tunnel).
2. Open `N` heavy pages, each picked at random from a list (default: 3 pages
   from Wikipedia's random article, The Sun, site123). A heavy page counts as
   opened when the document reaches `DOMContentLoaded`.
3. Open every target page from a second list, in order (default: Instagram,
   Facebook). A target page counts as opened when the main document answers
   HTTP `200`.
4. Close the tunnel and the browser. The next check starts from scratch.

Each page gets up to 3 attempts with a 30 s navigation timeout and a backoff
of 2 s, then 4 s. Both lists are plain URLs in the settings.

The link's result is `OK` (every page opened), `partial` (some did) or
`failed` (none did, or the link could not be started). One metric carries it:
`xrc_tunnel_up` is 1 when at least one page opened, and its `perfect` label
says whether all of them did. It is also labelled with the probe name, the
link name and a hash of the link that does not change when the link is
renamed.

### Why heavy pages

A check that fetches a tiny page, such as `generate_204`, sends a few hundred
bytes. Some DPI setups, notably in Russia, let a TCP connection to certain
foreign networks start normally and then stall it after roughly 16 KB. A tiny
request passes; a real page does not. We hit exactly this on the probes' own
metrics traffic. Heavy pages push enough bytes through the tunnel to cross
that threshold.

## What it does not measure

The check answers one question: did the pages load through the tunnel. It
does not measure speed, so slow throttling that still fits in 30 s looks
fine. It does not check images, scripts or CDNs: a target page passes on its
main HTML, and a heavy page passes even with a 403. Headless Chromium is not
a real user, and sites may answer it with a captcha or `429`, which is rate
limiting, not censorship. Problems shorter than the check interval can be
missed. Comparing probes hints at who is broken (the ISP, the VPN server or
the probe), but it is a guess, and it needs more than one probe.

## What it costs

- **Traffic.** Every check of every link downloads the HTML of `N` heavy pages
  and all target pages, plus whatever the browser fetches before
  `DOMContentLoaded`. On a metered or home connection, choose the number of
  links, `N` and the interval with that in mind.
- **Image size.** The probe image contains Chromium, so it is large. It is
  built once on your machine and sent to probes only when the code changes.
- **CPU and memory.** One Chromium at a time per probe. Links are checked one
  after another, not in parallel.

## Components

| folder | language | role |
|---|---|---|
| [`xray-tun-connect/`](xray-tun-connect/) | Go | turns share links into local SOCKS5 tunnels, one port per link; a bad link keeps its port and is reported, the others still start |
| [`xray-tun-pwc/`](xray-tun-pwc/) | Python | **PWC = Pressurising Web Checker.** Runs the checks through the tunnels and exports the metrics |
| [`ansible/`](ansible/) | Ansible | deploys probes and a central Prometheus + Grafana behind Caddy |

```
probe:  xray-tun-pwc -> Chromium -> SOCKS5 -> xray-tun-connect -> VPN server -> websites
          |
          +- /metrics -> Grafana Alloy -> relay -> remote_write -> central Prometheus -> Grafana
```

Probes push metrics; the central server never connects to them, so probes can
sit behind NAT. Deploying with Ansible still needs SSH access to each probe.
The relay on each probe opens a new connection for every
write, to get around the same ~16 KB stall described above.

## Quickstart

Check one link from your machine. You need [uv](https://docs.astral.sh/uv/),
Python 3.14 and the `xray-tun-connect` binary. Get the binary in one of two
ways.

**A. Build it.** Needs Go 1.27+:

```bash
make -C xray-tun-connect install          # installs xray-tun-connect into ~/go/bin
```

**B. Download it.** Take `xray-tun-connect-<os>-<arch>` from the Releases page
(for example `xray-tun-connect-linux-amd64`), then either put it on `PATH`
under the name `xray-tun-connect`:

```bash
install -m 755 xray-tun-connect-linux-amd64 ~/.local/bin/xray-tun-connect
```

or point the checker at it directly:

```bash
chmod +x xray-tun-connect-linux-amd64
export XRC_XRAY_TUN_CONNECT_BIN="$PWD/xray-tun-connect-linux-amd64"
```

On macOS, a file downloaded with a browser is quarantined. Clear it with
`xattr -d com.apple.quarantine <file>`.

Then run the checker:

```bash
cd xray-tun-pwc
uv sync
uv run playwright install chromium

echo 'vless://...your link...#my-server' > links.txt
XRC_CONFIG_PATH=links.txt uv run xray-tun-pwc
```

```
xray-tun-pwc 0.1.0 | instance my-laptop | 1 tunnels | every PT5M + up to PT2M
my-server - OK
```

A check from your own machine only tells you that the link works from your
network. The point of the project is to run it from the networks you care
about: **to monitor a fleet of probes, follow the step-by-step guide in
[`ansible/README.md`](ansible/README.md).**

Settings and the Docker image are described in
[`xray-tun-pwc/README.md`](xray-tun-pwc/README.md).

## Metrics

Each probe exposes one series per link on `http://<probe>:8000/metrics`:

```
xrc_tunnel_up{instance="spb-home-1",tunnel="msk-reality",stable_id="3f9c0d2a71e4b6c8",perfect="true"} 1
xrc_tunnel_up{instance="spb-home-1",tunnel="ams-ws",stable_id="8a01c7d3e9f25b14",perfect="false"} 1
xrc_tunnel_up{instance="spb-home-1",tunnel="old-trojan",stable_id="c42e6f0b18a9d357",perfect="false"} 0
```

Here `msk-reality` opened every page, `ams-ws` opened some (`partial`), and
`old-trojan` opened nothing (`failed`).

| label | meaning |
|---|---|
| `instance` | the probe (`XRC_INSTANCE`) |
| `tunnel` | the link name, the text after `#` |
| `stable_id` | hash of the link without its name; survives renames |
| `perfect` | `true` when every page opened |

When `perfect` changes, Prometheus starts a new series. Drop the label to get
one continuous line per link. Useful queries:

```promql
# is the link up on this probe at all
max without (perfect) (xrc_tunnel_up)

# links that are up but lose some sites
xrc_tunnel_up{perfect="false"} == 1

# links that are down on every probe: likely the VPN server
max by (tunnel) (xrc_tunnel_up) == 0

# probes where every link is down: likely the probe or its ISP
max by (instance) (xrc_tunnel_up) == 0

# share of checks in the last day where the link was up
avg_over_time((max without (perfect) (xrc_tunnel_up))[1d:5m])
```

## Secrets

Share links contain credentials. On the probe they are a plain-text file
readable by root and by the checker's user. They are passed to
`xray-tun-connect` over stdin, so they do not show up in the process list.

Inventory, passwords and links (`ansible/inventory.yml`, `ansible/vault.yml`,
`ansible/files/probes/`) are git-ignored. `vault.yml` is plain text, not
encrypted with ansible-vault: protect your machine accordingly.
