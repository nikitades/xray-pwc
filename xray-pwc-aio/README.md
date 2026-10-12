# xray-pwc-aio

The probe as one Docker container. Everything a probe runs lives inside it,
so the host needs nothing but Docker: no systemd units, no firewall rules, no
packages. One `compose.yml` starts it. From then on Ansible works inside the
container, through vpnc-net, and the image itself changes rarely.

## Two layers

| | what | changes | who updates it |
|---|---|---|---|
| **the image** `ghcr.io/nikitades/xray-pwc-aio:<VERSION>` (`aio_image_name` in Ansible) | Ubuntu (linuxserver.io base with s6-overlay), Python, Chromium with its libraries, WireGuard tools and `wireguard-go`, sshd, logrotate, cron, a fallback `xray-tun-connect`, the service scripts | rarely: a new base, Python, a new service | you, on the host: `probes-host.yml` or `docker compose up -d` |
| **the volume** `/app` | releases of the code (the checker's venv, `xray-tun-connect`), Alloy, Caddy, a Chromium for another Playwright | on every code change | Ansible, `probes.yml`, from inside |
| **the host folder** `./state` → `/app/state` | keys, links, settings, Alloy's buffer | on every settings change | the join token once, then Ansible |
| **the host folder** `./logs` → `/app/logs` | `aio.log` and four weeks back | always | the services |

The volume can be lost: Ansible brings everything in it again. `./state` is
what is worth keeping; it survives even `docker compose down -v`.

## The services

s6-overlay starts them; every one logs through `aio-log`, which puts the
time and the service name in front of each line and appends it to
`/app/logs/aio.log` (and to `docker logs`).

| service | what it does |
|---|---|
| `entrypoint` | once per start: checks `NET_ADMIN` and `/dev/net/tun`, makes `/app/state` from `VPNC_JOIN` on the first start, makes the SSH host key |
| `wg` | the vpnc-net interface from `state/wg/vpnc-net.conf`, the kernel's WireGuard or `wireguard-go`; logs when the handshake goes stale and comes back |
| `xtc-forward` | only with links in `state/service_links.txt` (Ansible writes them with `outbound_access_policy: vpn`): carries the tunnel through them, `xray-tun-connect -forward` on `127.0.0.1:51974` |
| `pwc` | the checker, as user `abc`, with `state/links.txt` |
| `alloy` | Grafana Alloy: the checker's and the host's metrics to Prometheus |
| `relay` | only with `AIO_RELAY=true` (`outbound_access_policy: relay`): Caddy between Alloy and Prometheus, a new connection per request |
| `sshd` | root with a key, only from the vpnc-net server: the bastion |
| `logrotate` | cron, nightly: `aio.log` weekly, four weeks kept, compressed from the second |

A service with nothing to do (no release yet, no links, relay off) logs why
and waits. `docker logs` also shows the s6 and linuxserver.io lines.

## The log

```
2026-10-12T15:41:23.500Z xtc-forward WARN link_unusable index=0 name=MSK fails=3 reason="dns timeout"
<time UTC>              <service>   <LEVEL> <event> <key=value ...>
```

Our services write `LEVEL event key=value`; Alloy, Caddy and sshd keep their
own format after the prefix. `deploy` lines come from Ansible (a release
switched or removed), `s6` lines from a service that stopped on its own.

## Start a probe

`vpnc-net-join.yml` makes `files/vpnc-net/join/<name>/compose.yml`, with the
join token in it: the probe's address and WireGuard key, the server's key and
address, the service links (with `outbound_access_policy: vpn`) and your SSH
key. The file is private. On the machine, in a folder of its own:

```bash
docker compose up -d
tail -f logs/aio.log      # "wg INFO handshake_ok": it is in vpnc-net
```

Or `ansible-playbook probes-host.yml --limit <name>` with SSH to the host.
Linux and macOS (Docker Desktop) work alike; on macOS the host metrics are
those of Docker's VM.

The token is read only while `./state` is empty. To join again from scratch,
take the container down and delete `./state`. To change the tunnel of a
running probe whose tunnel is down (another `outbound_access_policy`, other
service links), keep `./state`: `probes-host.yml` writes the tunnel's files
into the container with `docker exec` and restarts its services.

`probes-host.yml` and `probes-clean.yml` find the container's folder by its
Compose label, wherever it was started; without a container,
`probes-host.yml` makes `~/xray-pwc-aio`.

## Build

The Dockerfile has several targets; Ansible builds the packages itself.

```bash
# the image, for both CPU types, and publish it to your registry
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-context pwc=../xray-tun-pwc --build-context xtc=../xray-tun-connect \
  -t <registry>/xray-pwc-aio:$(cat VERSION) --push .
```

Ansible pulls `ghcr.io/nikitades/xray-pwc-aio` by default. If you change the
image, publish it under your own name and set `aio_image_name:
<registry>/xray-pwc-aio` in `ansible/inventory.yml` (under `all.vars`). The
image is public: it has no secrets. After a change to it, raise `VERSION`,
publish, and run `probes-host.yml`. Ansible warns when a probe runs another
version.

The other targets are files, not images: `release` (the venv at
`/app/releases/<hash>` and `xray-tun-connect`), `alloy` and `caddy`
(`--output type=tar`). `probes.yml` builds them into `ansible/files/aio/`.
