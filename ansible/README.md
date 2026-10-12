# Ansible: probes fleet monitoring

These playbooks deploy the whole system: a central server with Prometheus and
Grafana, and any number of probes, each one `xray-pwc-aio` container.

| playbook | hosts | what it does |
|---|---|---|
| `monitoring.yml` | `monitoring` | the vpnc-net server (WireGuard) + Prometheus (receives remote write) + Grafana + Caddy + CoreDNS |
| `probes.yml` | the probe containers | the code (a release), Alloy and Caddy, the links, the tunnel settings, inside each container |
| `probes-host.yml` | the probes' hosts | puts `compose.yml` on a host and brings the container up, or to a new image |
| `probes-clean.yml` | the probes' hosts | takes a container down with its state, log and image |
| `vpnc-net-join.yml` | your machine + `monitoring` | adds a vpnc-net member: address, keys, inventory entry, link files, `compose.yml` with the join token; tells the server |
| `vpnc-net-remove.yml` | your machine + `monitoring` | forgets a vpnc-net member and tells the server |

Run every command from this directory: `ansible.cfg` here sets the inventory
and the collections path.

```bash
ansible-playbook monitoring.yml                       # the central server
ansible-playbook vpnc-net-join.yml -e name=<probe>    # a new probe
ansible-playbook probes-host.yml --limit <probe>      # its container, on its host
ansible-playbook probes.yml                           # every probe
ansible-playbook probes.yml --limit <probe>           # one probe
ansible-playbook probes-clean.yml --limit <probe>     # take a probe away
ansible-playbook vpnc-net-remove.yml -e name=<probe>  # and forget it
```

Add `--check --diff` to any of them to see the changes without making them.

## Step by step

### 1. What you need

- **Your machine**, the control plane (where you run Ansible): Ansible,
  Docker with `buildx`, `zstd`, Python 3, and a checkout of this whole
  repository. What goes into the probes is built here.
- **A central server**: Debian or Ubuntu with systemd-resolved, a public IP,
  root SSH access, and a DNS name for Grafana pointing to it. Port 80, your
  HTTPS port and UDP `vpnc_net_port` (51973) must be open, also in the
  hosting's own firewall. It is also the bastion: SSH to the probes goes
  through it.
- **Probes**: any machine with Docker and the compose plugin, Linux or macOS,
  amd64 or arm64, and outgoing internet. No public address and no open port:
  the container connects out. Where the provider blocks UDP to the server,
  vpnc-net goes through your VPN links instead, see service links in step 5.

Your machine is not in vpnc-net: it reaches the probes through the server
(`ssh -J`).

### 2. Install the Ansible collections

```bash
cd ansible
ansible-galaxy collection install -r requirements.yml -p ./collections
```

### 3. Describe your hosts

```bash
cp inventory.example.yml inventory.yml
$EDITOR inventory.yml
```

Set the central server under `monitoring`, the Grafana domain, and
`vpnc_net_ssh_key`, the public SSH key that goes into the probes. If port 443
is busy on the central server, set `caddy_https_port: 8443`. Probes come with
`vpnc-net-join.yml`; in `inventory.yml` a probe only needs per-probe settings
and, for `probes-host.yml` and `probes-clean.yml`, its host's SSH address.

The probe's name is important. It becomes the `instance` label in metrics,
the container's hostname, its DNS name in vpnc-net and the folder name for
its links. Choose names that say where the probe is, for example `spb-home-1`.

### 4. Set the secrets and the server's keys

```bash
cp vault.yml.example vault.yml
$EDITOR vault.yml
```

You need a Grafana admin password (`openssl rand -hex 24`). `vault.yml` is
plain text and git-ignored. Keep a backup of it somewhere safe.

Then give the server its vpnc-net address and keys:

```bash
ansible-playbook vpnc-net-join.yml -e name=mon-1      # named as in inventory.yml
```

The keys land in `host_vars/<name>/vpnc-net.yml`, git-ignored.

### 5. Add the VPN links

Each probe has a folder named exactly like the probe: `files/probes/<probe>/`.
`vpnc-net-join.yml` makes it with an empty `links.txt` and `service_links.txt`.
Put the share links to check into `links.txt`, one per line:

```
# Home ISP, main channel
vless://11111111-1111-1111-1111-111111111111@198.51.100.10:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=jNXHt1yRo0vDuchQlIP6Z0ZvjT3KtzVI-T4E7RoLJS0&sid=6ba85179e30d4fc2&type=tcp&flow=xtls-rprx-vision#msk-reality

trojan://SuperSecret123@198.51.100.12:443?security=tls&sni=t.example.com&type=tcp#msk-trojan
```

The text after `#` is the link's name in logs and metrics, so make it clear.
Empty lines and lines starting with `#` are skipped. All files in the folder
but `service_links.txt` are joined into one list. `probes.yml` stops if the
list has no links. `files/probes/` is git-ignored.

Different probes can have different links, or the same ones. The same link
on several probes is what lets you compare networks.

**Service links.** `files/probes/<probe>/service_links.txt` is a separate
list in the same format, not part of the checks: links for the probe's own
traffic. Without links in it, the tunnel goes straight to the server over
UDP. With links, it goes through them, for providers that block that UDP. Use
it where the direct tunnel does not hold, and put the links in order of
preference: pick ones whose servers allow UDP, and more than one. Every 15 s
every link is checked with a DNS question over UDP through it; the tunnel
stays on its link while that one passes, and moves to the first passing link
in the file when it fails 3 times in a row. With none passing, it goes
straight, without a VPN, until one passes again. A link can be in both files,
but the files do not know about each other.

### 6. Deploy the central server

```bash
ansible-playbook monitoring.yml
```

Check that `https://<grafana domain>` opens the Grafana login page.

### 7. Add the probes

```bash
ansible-playbook vpnc-net-join.yml -e name=probe-2
```

This writes `inventory.d/probe-2.yml` and `host_vars/probe-2/vpnc-net.yml`,
creates `files/probes/probe-2/` with empty link files (existing ones are
kept), tells the server about the new member, and makes
`files/vpnc-net/join/probe-2/compose.yml`. That file is the probe: the image,
and the join token with the probe's key, the server's address and key, your
SSH key and the service links. It is private.

Put links into `service_links.txt` before the join if the probe's network
blocks UDP to the server: they go into the token. Later changes go through
`probes.yml`.

Start the container on the machine, in a folder of its own:

```bash
docker compose up -d
tail -f logs/aio.log                 # wait for: wg INFO handshake_ok
```

or, with the host's SSH address in the inventory (`probe_host`,
`probe_host_user`, `probe_host_port`), from here:

```bash
ansible-playbook probes-host.yml --limit probe-2
```

The image comes from GHCR (`aio_image` in `group_vars/probes.yml`). Then put
the links to check into `files/probes/probe-2/links.txt` (step 5) and:

```bash
ansible-playbook probes.yml --limit probe-2
```

The first run builds the code and the tools on your machine and copies them
in, so it takes a few minutes. Later runs send only what changed.

Check a probe:

```bash
ssh -J root@<central server> root@probe-2.vpnc-net.internal tail -n 20 /app/logs/aio.log
```

```
2026-10-12T17:43:31.366Z pwc INFO start version=0.2.0 instance=probe-2 links=2 interval=PT5M jitter=PT2M
2026-10-12T17:43:49.536Z pwc INFO check index=0 name=msk-reality result=ok stage=3
2026-10-12T17:43:49.536Z pwc WARN check index=1 name=msk-trojan result=partial stage=2 failed="instagram.com: HTTP 429"
```

`alloy` lines with `level=error` about sending mean the data does not reach
Prometheus: look at the `wg` lines for the handshake.

### 8. Look at the results in Grafana

1. Log in as `admin` with `vault_grafana_admin_password`.
2. Add a data source: Prometheus, URL `http://prometheus:9090`.
3. In Explore, run `pwc_tunnel_stage`.

See the [xray-tun-pwc README](../xray-tun-pwc/README.md#metrics) for what
the metric means and for useful queries. Dashboards and alerts are not part of this repository:
build them in the Grafana UI.

### 9. Day-to-day changes

**Change links or service links:** edit `files/probes/<probe>/`, then

```bash
ansible-playbook probes.yml --limit <probe>
```

**Change the code** (the checker, xray-tun-connect): a new release is built
and sent, the old one stays for a rollback.

```bash
ansible-playbook probes.yml
```

**Change the image** (`xray-pwc-aio/`): raise `xray-pwc-aio/VERSION`,
publish the image (see [its README](../xray-pwc-aio/README.md#build)), then

```bash
ansible-playbook probes-host.yml
```

`probes.yml` warns about a probe that runs another image version.

**Add a probe:** see step 7.

**Remove a probe:** take its container away on its host, then forget it.
Its links in `files/probes/<probe>/` stay: delete them yourself.

```bash
ansible-playbook probes-clean.yml --limit <probe>
ansible-playbook vpnc-net-remove.yml -e name=<probe>
```

Without SSH to the host: `docker compose down -v --rmi all` in the folder of
`compose.yml` there, then delete the folder.

**Lost compose.yml:** run the join again. The address and the keys stay. A
running container does not need it: its state is in `./state` on its host.

## How it works

### The probe container

Everything a probe runs is in one container, `xray-pwc-aio`: WireGuard,
xray-tun-connect for the tunnel, the checker, Alloy, the relay and sshd,
under s6. See [`../xray-pwc-aio/README.md`](../xray-pwc-aio/README.md). On
the host it is a folder:

```
~/xray-pwc-aio/
├── compose.yml    the image and the join token
├── state/         keys, links, settings, Alloy's buffer: managed by Ansible
└── logs/          aio.log, rotated weekly, four weeks kept
```

and the `xray-pwc-aio_app` volume with the code and the tools.

`probes.yml` connects to the container's sshd, as root of the container, at
its vpnc-net address, through the server (`ProxyJump`). The image stays as it
is: Ansible changes only `/app`.

### Releases

`probes.yml` builds, on your machine, from `xray-pwc-aio/Dockerfile`, for
the probe's CPU type:

- **the release**: the checker's venv and xray-tun-connect, named by a hash
  of the `xray-tun-pwc/`, `xray-tun-connect/` and Dockerfile sources;
- **Alloy and Caddy**, named by their versions (`aio_alloy_version`,
  `aio_caddy_version`).

They are kept in `files/aio/` as zstd tar files. A package the container has
is not sent again. A release is unpacked into `/app/releases/<hash>` and
switched in by moving the `/app/current` link, then the services restart. The
release before stays (`aio_keep_releases: 2`): a rollback is `ln -sfn
releases/<old> /app/current` in the container and a restart. Unused tools
go.

The image has Chromium for its own Playwright. A release with another
Playwright gets its Chromium in the volume, `/app/opt/ms-playwright`.

### The relay

From a probe in Russia, a TCP connection to some foreign hosting freezes
after about 16 KB. Small requests pass, bigger ones hang. Grafana Alloy keeps
one long connection and sends big batches, so its data never arrives.

By default every probe runs a small Caddy relay. Alloy sends metrics to the
relay, and the relay opens a **new** connection to the central server for
every request. Alloy also sends small batches (200 samples, about 4 KB). See
`prom_remote_write_upstream` and `prom_remote_write_max_samples` in
`group_vars/probes.yml`.

Inside vpnc-net the connection to Prometheus is hidden in WireGuard, and on a
probe with service links even inside the VPN link, so the stall may not
happen at all. Set `probe_relay: false` for such a probe in the inventory:
the relay stays idle, and Alloy sends straight to the server's vpnc-net
address over one connection, 2000 samples per batch. If the `alloy` lines in
the log then show `Failed to send batch` again and again, turn the relay
back on.

### Caddy on the central server

```
caddy_grafana_domain  -> all of Grafana
```

Grafana listens only on `127.0.0.1`, Caddy faces the internet. Certificates
come from Let's Encrypt with the HTTP-01 check on port 80, so they work even
when HTTPS runs on 8443. The cost of 8443 is a port in every URL. Prometheus
is not public at all, see below.

### vpnc-net

vpnc-net is a small private WireGuard network. The central server is its
server, `wg-quick@vpnc-net` with the kernel's WireGuard; the probes are
clients. Clients connect out to the server and keep the tunnel open, so
probes behind NAT are reachable.

```
your machine ──ssh──► server (public address)
                        │ ssh -J: the server opens the connection itself
                        ▼
        server 10.213.97.1 ◄──── probe-1 10.213.97.10
        Prometheus :9090   ◄──── probe-2 10.213.97.11
        CoreDNS    :53
```

- **Members.** Every member has `host_vars/<name>/vpnc-net.yml`: `vpnc_net_ip`
  and the key pair. `vpnc-net-join.yml` hands out `.1` to the server and the
  first free address from `.10` to the rest, and keeps them on later runs.
  The server config and the CoreDNS names come from these files. Adding a
  member only reloads the server's tunnel (`wg syncconf`): the others stay
  connected.
- **Who reaches whom.** Probes reach only the server: it drops traffic
  between peers (one `iptables` rule in the WireGuard config). You reach the
  probes through the server: an SSH connection the server opens itself is
  not traffic between peers.
- **SSH.** In a probe, sshd lets in root with `vpnc_net_ssh_key`, and only
  from the server's vpnc-net address. Ansible trusts a probe's host key on
  first use: the tunnel already proves who is on the other end.
- **Names.** CoreDNS on the server answers `<member name>.vpnc-net.internal` and
  `prom.vpnc-net.internal`, and forwards every other name. The server's own
  resolver sends that zone to it, so `ssh -J <server> root@probe-2.vpnc-net.internal`
  works. The checker keeps the probe's own DNS, so the tests see the probe's
  real network.
- **Prometheus has no auth.** It listens on `127.0.0.1` and the vpnc-net
  address only.
- **No harm to the host.** On a probe, the tunnel lives in the container's
  own network: the host gets no interface, route or rule. Only
  `vpnc_net_subnet` goes into the tunnel.
- **Service links.** On a probe with links in `service_links.txt`, the
  tunnel's endpoint is `127.0.0.1:51974`, and the `xtc-forward` service,
  `xray-tun-connect -forward`, sends its UDP through one link at a time to the
  server. See step 5 for how the link is chosen, and the
  [xray-tun-connect README](../xray-tun-connect/README.md#forward-mode). The
  `xtc-forward` lines in the log show the links by name only.

### Other details

- **Missing `vault.yml` is not an error for Ansible.** It silently skips a
  missing file in `vars_files`. `monitoring.yml` starts with an `assert` that
  checks the keys it needs.
- **Docker on the central server.** Ubuntu uses `docker-compose-v2`, Debian
  13 uses `docker-compose`. The playbook picks the right one and installs
  Docker only if it is missing. On the probes Docker is yours to install.
- **Old data after a network break.** Alloy keeps unsent data for up to 8
  hours and sends it when the network is back. Prometheus accepts samples up
  to 1 hour old (`out_of_order_time_window`). Data older than that is lost.
- **Links stay out of the terminal.** The tasks that write them use
  `no_log: true`, so `--diff` does not print them.
- **On macOS** Docker Desktop does not keep the group of a file in `./state`,
  so `probes.yml` reports `Write share links` as changed on every run there.
- **A "probe is gone" alert:**
  `absent_over_time(up{job="xray-tun-pwc",host="probe-1"}[10m])`, with
  "No data" state set to OK. Otherwise the alert fires all the time on a
  healthy probe.

## Secrets

Everything secret is git-ignored and stays on your machine:

| file | holds |
|---|---|
| `inventory.yml`, `inventory.d/` | your hosts |
| `vault.yml` | the Grafana password |
| `host_vars/<name>/vpnc-net.yml` | each member's vpnc-net address and keys |
| `files/probes/` | share links, which contain credentials |
| `files/vpnc-net/join/` | the probes' `compose.yml` with their join tokens |

`vault.yml` is plain text, not encrypted with ansible-vault: protect your
machine accordingly, and keep a backup of it and of `host_vars/`. On a
probe, the secrets are in `~/xray-pwc-aio/` (`compose.yml`, `state/`),
readable by root.

## Pinned versions

`prom/prometheus:v3.15.0`, `grafana/grafana:13.2.2`, `coredns/coredns:1.12.1`;
in the probes: `grafana/alloy:v1.20.0`, `caddy:2.11.7`, and the image's own
in [`../xray-pwc-aio/Dockerfile`](../xray-pwc-aio/Dockerfile).
