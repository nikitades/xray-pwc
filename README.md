# VpnChecker

VpnChecker shows whether your VPN links really work for people in a given
network, for example a home internet provider in one city. You put a small
Linux machine, a *probe*, into that network. Every few minutes the probe
opens real websites through each of your links, the way a person would, and
records how far it got: everything opened, some sites opened, or nothing got
through.

All probes send their results to one central server, which stores them and
draws them in Grafana. Probes need no public address and no open ports: each
one connects to the server by itself through a small private network,
vpnc-net. The same network lets you reach the probes over SSH from your
laptop. Where the provider blocks that connection, a probe can carry it
through one of your VPN links instead.

Everything is installed with Ansible from your own machine. The probe
software is built there too and copied to the probes, so no registry or
public repository is needed. The project is young (`0.1.0`) and runs on a
handful of probes: treat the defaults as a starting point, not as tuned
values.

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
- **`vpnc-net.service`**: the vpnc-net server (AmneziaWG, UDP `51973`). Docker
  starts after it, because Prometheus and CoreDNS listen on its address.

## What runs on a probe

A Docker Compose project, `xray-tun-pwc` in `/opt/xray-tun-pwc`:

| service | what it does |
|---|---|
| `xray-tun-pwc` | the checker. It opens the sites through each link with Chromium and xray-tun-connect, and uses the probe's own network and DNS, so it sees what a local user sees |
| `alloy` | Grafana Alloy: every 30 s it collects the checker's results and the host's CPU, memory, disk and network, and sends them on. If the server is out of reach, it keeps up to 8 hours of data and sends it later |
| `relay` | optional (`probe_relay`, on by default): a small Caddy that takes Alloy's data and sends it to Prometheus through vpnc-net. It opens a new connection for every batch, because some providers freeze a long connection to foreign hosting after about 16 KB. Without it, Alloy sends straight to Prometheus |

On the host itself, outside Compose:

- **`vpnc-net.service`**: the vpnc-net client. Only `10.213.97.0/24` goes
  into it: no default route, no DNS changes, so other VPNs on the machine
  keep working.
- **`vpnc-net-via.service`**, only on probes with a `service_links.txt`:
  carries vpnc-net through one of those VPN links, for providers that block
  UDP to the server. If the server stops answering through it, the next link
  takes over, and after the last one a direct try without a VPN.

```
probe:  xray-tun-pwc -> VPN link -> websites
        alloy -> relay -> vpnc-net (-> service link) -> server: prometheus -> grafana
```

## Where to read more

- [`xray-tun-pwc/`](xray-tun-pwc/README.md), the checker: what one check
  does, how the result is counted, what it does not catch, the metrics and
  useful queries, the settings. Start here to try one link on your own
  machine.
- [`xray-tun-connect/`](xray-tun-connect/README.md), a small Go tool the
  checker uses: it turns share links into local ports, and forwards the
  vpnc-net tunnel on probes with service links.
- [`ansible/`](ansible/README.md), the deployment: the central server and
  the probes step by step, adding and removing probes, how vpnc-net works,
  where the secrets live.
