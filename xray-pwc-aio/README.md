# xray-pwc-aio

The probe as one Docker container. Everything a probe runs lives inside it,
so the host needs nothing but Docker: no systemd units, no firewall rules, no
packages, no files of its own. One `compose.yml` starts it, the same on
Linux, macOS and Windows. From then on Ansible works inside the container,
through xray-pwc-net, and nobody needs SSH to the host.

Inside, s6 is root and nothing else is: every service runs as `abc`, the
linuxserver.io base image's user.

## Two parts

| | what | changes | who updates it |
|---|---|---|---|
| **the image** `ghcr.io/nikitades/xray-pwc-aio:<version>` | Ubuntu (linuxserver.io base with s6-overlay), Python, Chromium with its system libraries, WireGuard tools, `wireguard-go`, sshd, cron and logrotate, the Alloy and Caddy of its release, and the s6 service definitions, which only start the release's scripts | rarely: a new base, a new service, a new system library | the host's owner: `docker compose pull && docker compose up -d` |
| **the volume** `app` → `/app` | the releases (`releases/<version>`, `current`), tools of other versions (`opt/`), the state (`state/`: keys, links, settings, Alloy's buffer) and the log (`logs/`) | on every release and settings change | the join token once, then Ansible, `probes.yml`, from inside |

A release is the bundle of one version: the checker's venv with the
`xray-pwc-probe` wheel, `xray-pwc-connect`, the service scripts
([`services/`](services)) and `release.env`. The image carries the bundle of
its own version, so a new probe starts on it; after that, Ansible brings any
other release over xray-pwc-net.

The image is the one thing Ansible cannot change. So a release says in
`release.env` which image it needs at least (`MIN_IMAGE`, from
[`deps.env`](deps.env)), and `probes.yml` refuses a release the probe's image
is too old for: that one needs the host's owner to pull the new image. Raise
`MIN_IMAGE` only for what a release really needs from the image.

The volume is all the probe keeps. Lost (`docker compose down -v`), it comes
back from the token in `compose.yml` and from `probes.yml`, without Alloy's
buffer.

## The services

s6-overlay starts each one, as root, with `aio-run`: it lets `abc` read and
control the service (`s6-svc` from Ansible), then runs the release's script,
`/app/current/services/<service>`, as `abc`. Every line goes through
`aio-log`, which puts the time and the service name in front and appends it
to `/app/logs/aio.log` and to `docker compose logs`.

| service | what it does |
|---|---|
| `entrypoint` | once per start, as root: checks `NET_ADMIN` and `/dev/net/tun`, makes `/app/state` from `PWC_JOIN` on the first start, makes the SSH host key, gives `/app` to `abc` |
| `wg` | the xray-pwc-net interface from `state/wg/xray-pwc-net.conf` and the address, subnet and MTU in `state/aio.env`: the kernel's WireGuard, or `wireguard-go`; logs when the handshake goes stale and comes back |
| `connect-forward` | only with links in `state/service_links.txt` (`outbound_access_policy: vpn`): carries the tunnel through them, `xray-pwc-connect -forward` on `127.0.0.1:51974` |
| `probe` | the checker, `xray-pwc-probe`, with `state/links.txt` |
| `alloy` | Grafana Alloy: the checker's and the host's metrics to Prometheus |
| `relay` | only with `AIO_RELAY=true` (`outbound_access_policy: relay`): Caddy between Alloy and Prometheus, a new connection per request |
| `sshd` | as `abc` on port 2222, with a key, only from the xray-pwc-net server: the bastion |
| `logrotate` | cron, nightly, as root: `aio.log` weekly, four weeks kept, compressed from the second |

A service with nothing to do (no links, relay off) logs why and waits.

**Without root.** The tunnel needs one capability, `NET_ADMIN`, which
`compose.yml` grants to the container. In the image it sits on three
binaries only: `wg`, `wireguard-go` and `aio-net`, a small helper that sets
the interface's address, MTU and route (iproute2's `ip` drops its
capabilities for a user who is not root). They act on the container's own
network, never the host's.

## The log

```
2026-10-12T15:41:23.500Z connect-forward WARN link_unusable index=0 name=MSK fails=3 reason="dns timeout"
<time UTC>              <service>   <LEVEL> <event> <key=value ...>
```

Our services write `LEVEL event key=value`; Alloy, Caddy and sshd keep their
own format after the prefix. `deploy` lines come from Ansible (a release
switched or removed), `s6` lines from a service that stopped on its own.

## Start a probe

`xray-pwc-net-join.yml` makes `files/xray-pwc-net/join/<name>/compose.yml`,
with the join token in it: the probe's address and WireGuard key, the
server's key and address, the service links (with
`outbound_access_policy: vpn`) and your SSH key. The file is private. On the
machine, in a folder of its own:

```bash
docker compose up -d
docker compose logs -f      # "wg INFO handshake_ok": it is in xray-pwc-net
```

On macOS and Windows the host metrics are those of Docker's VM.

The token is read only while the volume has no state. A probe whose tunnel
never comes up cannot be reached: fix the token (the service links, the
policy), and its owner starts it again from scratch:
`docker compose down -v`, the new `compose.yml`, `docker compose up -d`.

## Build

The root [`Makefile`](../Makefile) builds every target of the
[`Dockerfile`](Dockerfile), here and in CI:

```bash
make bundle            # the bundles, dist/<version>/xray-pwc-<version>-linux-<arch>.tar.zst
make image             # the image, into the local Docker, for this machine's CPU
make tool TOOL=alloy TOOL_VERSION=v1.20.0 ARCH=arm64   # a tool, for Ansible
```

Both build from `dist/<version>/`: the `xray-pwc-probe` wheel, its locked
requirements and `xray-pwc-connect`, which `make` builds first. A release
(tag `vX.Y.Z`) is built and published by CI, see the
[root README](../README.md#versions-and-releases).

A fork that changes the image publishes its own and sets `aio_image_name:
<registry>/xray-pwc-aio` in `ansible/inventory.yml` (under `all.vars`). The
image is public: it has no secrets.
