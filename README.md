# VpnChecker

VpnChecker shows whether your VPN configs (aka links) really work for people in a given
network, for example a home internet provider in one city. You put a small
machine, a *probe*, into that network. Every few minutes the probe opens real
websites through each of your VPN configs, the way a person would, and records how
far it got: everything opened, some sites opened, or nothing got through.

All probes send their results to one central server, which stores them and
draws them in Grafana. Probes need no public address and no open ports: each
one connects to the server by itself through a small private WireGuard
network, vpnc-net.

Remarkably, the Wireguard network is wrapped into the XRay tunnel! This means,
in environments where providers impose heavy DPI, technologies such as Wireguard and
at times even AmneziaWG don't work. But that's no issue here! XRay takes care of that.

A simple schema:

`probe <-> XRay tunnel <-> Wireguard <-> metrics collector`

The same network lets you reach the probes over SSH, from
the control plane machine (the one you run Ansible on) through the central
server. Where the provider blocks that connection, a probe carries it
through one of your VPN links instead.

To make it clear, there are two lists of VPN configs:

- the list of configs to check
- the list of configs to use as a secure XRay tunnel

and these lists don't have to be the same. But they might.

A probe is one Docker container, so any machine with Docker can be one,
Linux or macOS. Everything is installed with Ansible from your own machine,
and what goes into the probes is built there too. The project is young: treat
the defaults as a starting point, not as tuned values. No root needed!

## Which part you need

The parts work on their own too. Take only what solves your problem:

| your problem | take |
|---|---|
| You want to know if an Xray link really works: real pages in a real browser, not a ping | [`xray-tun-pwc`](xray-tun-pwc/README.md), the checker. Python; run it on your machine with a links file, or in its Docker image. It gives a log line and Prometheus metrics per link |
| You need many Xray links as local ports at once, or one stable port that carries TCP and UDP through whichever of your links works now | [`xray-tun-connect`](xray-tun-connect/README.md). One Go binary: share links in, SOCKS ports or a self-healing forward out |
| You want a fleet of probes in different networks, watched from one Grafana | everything: [`xray-pwc-aio`](xray-pwc-aio/README.md), the probe as one container, and the [Ansible playbooks](ansible/README.md) that set up the server, join the probes and deliver the code |

## What runs on the central server

A Docker Compose project, `vpn-monitoring` in `/opt/vpn-monitoring`:

| service | what it does | listens on |
|---|---|---|
| `prometheus` | stores the results: probes push them (remote write), kept 180 days or 40 GB. It has no password, so it is not public | `127.0.0.1:9090` and `10.213.97.1:9090` (vpnc-net) |
| `grafana` | graphs, dashboards, alerts | `127.0.0.1:3000`, public through Caddy |
| `coredns` | names inside vpnc-net: `<host>.vpnc-net.internal` and `prom.vpnc-net.internal`; any other name goes to 1.1.1.1 and 8.8.8.8 | `10.213.97.1:53` |

On the host itself, outside Compose:

- **Caddy** (a system package): HTTPS for Grafana with a Let's Encrypt
  certificate. It is the only public web service.
- **`wg-quick@vpnc-net`**: the vpnc-net server (WireGuard, UDP `51973`).
  Docker starts after it, because Prometheus and CoreDNS listen on its
  address. Probes reach only this server, not each other.
- **sshd**, as the bastion: SSH to the probes goes through it (`ssh -J`).

## What runs on a probe

One container, `xray-pwc-aio`, started from one `compose.yml`. Inside, under
s6:

| service | what it does |
|---|---|
| `pwc` | the checker. It opens the sites through each link with Chromium and xray-tun-connect, and uses the probe's own network and DNS, so it sees what a local user sees |
| `alloy` | Grafana Alloy: every 30 s it collects the checker's results and the host's CPU, memory, disk and network, and sends them on. If the server is out of reach, it keeps up to 8 hours of data and sends it later |
| `relay` | optional (`probe_relay`, on by default): a small Caddy that takes Alloy's data and sends it to Prometheus, a new connection for every batch, because some providers freeze a long connection to foreign hosting after about 16 KB |
| `wg` | the vpnc-net client, in the container's own network: the host gets no interface or route, and other VPNs on the machine keep working |
| `xtc-forward` | only with service links: carries vpnc-net through one of those VPN links, for providers that block UDP to the server. It checks every link all the time and moves to the next good one when the current one fails; with none good, it goes without a VPN |
| `sshd` | for Ansible and you, through the central server |

Everything logs into one file on the host, `logs/aio.log`, rotated weekly.

```
probe:  pwc -> VPN link -> websites
        alloy -> relay -> vpnc-net (-> service link) -> server: prometheus -> grafana
```

## Where to read more

- [`xray-tun-pwc/`](xray-tun-pwc/README.md), the checker: what one check
  does, how the result is counted, what it does not catch, the metrics and
  useful queries, the settings. Start here to try one link on your own
  machine.
- [`xray-tun-connect/`](xray-tun-connect/README.md), a small Go tool the
  checker uses: it turns share links into local ports, and carries the
  vpnc-net tunnel on probes with service links.
- [`xray-pwc-aio/`](xray-pwc-aio/README.md), the probe container: its
  services, its log, what is in the image and what Ansible brings.
- [`ansible/`](ansible/README.md), the deployment: the central server and
  the probes step by step, adding and removing probes, how vpnc-net works,
  where the secrets live.
