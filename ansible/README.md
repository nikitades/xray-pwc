# Ansible: probes fleet monitoring

These playbooks deploy the whole system: a central server with Prometheus and
Grafana, and any number of probes, each one `xray-pwc-aio` container.

| playbook | hosts | what it does |
|---|---|---|
| `monitoring.yml` | `monitoring` | the xray-pwc-net server (WireGuard) + Prometheus (receives remote write) + Grafana + Caddy + CoreDNS |
| `probes.yml` | the probe containers | the release (`xray_pwc_release`), the tools it needs, the links, the tunnel settings, inside each container |
| `xray-pwc-net-join.yml` | your machine + `monitoring` | adds a xray-pwc-net member: address, keys, inventory entry, link files, `compose.yml` with the join token; tells the server. For the server itself only the keys |
| `xray-pwc-net-remove.yml` | your machine + `monitoring` | forgets a xray-pwc-net member and tells the server |

Run every command from this directory: `ansible.cfg` here sets the inventory
and the collections path.

```bash
ansible-playbook monitoring.yml                            # the central server
ansible-playbook xray-pwc-net-join.yml -e name=<probe>     # a new probe
ansible-playbook probes.yml                                # every probe
ansible-playbook probes.yml --limit <probe>                # one probe
ansible-playbook probes.yml --limit <probe> -e xray_pwc_release=local   # try this working tree
ansible-playbook xray-pwc-net-remove.yml -e name=<probe>   # forget a probe
```

Ansible never touches a probe's host. The owner of the machine starts the
container once, and pulls a new image when a release needs one; everything
else goes through xray-pwc-net.

Add `--check --diff` to any of them to see the changes without making them.
What needs a real change first is skipped then: sending a release or a
tool, and everything after the keys of a new member.

## Step by step

### 1. What you need

- **Your machine**, the control plane (where you run Ansible): Ansible,
  `zstd`, Python 3, `make`, and a checkout of this whole repository. Ansible
  downloads the release from GitHub to here and sends it on. To try your own
  changes (`xray_pwc_release: local`) or to bring a tool a probe's image
  lacks, also Docker with `buildx`, Go and [uv](https://docs.astral.sh/uv/).
- **A central server**: Debian or Ubuntu with systemd-resolved, a public IP,
  root SSH access, and a DNS name for Grafana pointing to it. Port 80, your
  HTTPS port and UDP `pwc_net_port` (51973) must be open, also in the
  hosting's own firewall. It is also the bastion: SSH to the probes goes
  through it.
- **Probes**: any machine with Docker and the compose plugin, Linux, macOS or
  Windows, amd64 or arm64, and outgoing internet. No public address, no open
  port, no SSH for you: the container connects out. Where the provider blocks UDP to the server,
  xray-pwc-net goes through your VPN links instead, see the outbound access
  policy in step 5.

Your machine is not in xray-pwc-net: it reaches the probes through the server
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
`pwc_net_ssh_key`, the public SSH key that goes into the probes. If port 443
is busy on the central server, set `caddy_https_port: 8443`. Probes come with
`xray-pwc-net-join.yml`; in `inventory.yml` a probe only needs its own
settings, if any (see `inventory.example.yml`).

The probe's name is important. It becomes the `instance` label in metrics,
the container's hostname, its DNS name in xray-pwc-net and the folder name for
its links. Choose names that say where the probe is, for example `spb-home-1`.

### 4. Set the secrets and the server's keys

```bash
cp vault.yml.example vault.yml
$EDITOR vault.yml
```

You need a Grafana admin password (`openssl rand -hex 24`). `vault.yml` is
plain text and git-ignored. Keep a backup of it somewhere safe.

Then give the server its xray-pwc-net address and keys:

```bash
ansible-playbook xray-pwc-net-join.yml -e name=mon-1      # named as in inventory.yml
```

The keys land in `host_vars/<name>/xray-pwc-net.yml`, git-ignored. That is all
it does for the server: `monitoring.yml` sets it up in step 6.

### 5. Add the VPN links

Each probe has a folder named exactly like the probe: `files/probes/<probe>/`.
`xray-pwc-net-join.yml` makes it in step 7, with an empty `links.txt` and
`service_links.txt`, and keeps files that are there: make it yourself before
the join if you want the links in place first. Put the share links to check
into `links.txt`, one per line:

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

**How the probe reaches the server.** One setting per probe in the
inventory, `outbound_access_policy`:

| policy | xray-pwc-net | Alloy sends | for |
|---|---|---|---|
| `plain` (default) | straight to the server over UDP | over one connection, 2000 samples per batch | most networks |
| `relay` | straight to the server over UDP | through the relay, a new connection per batch of 200 | a probe where the `alloy` lines show `Failed to send batch` again and again |
| `vpn` | through the links in `service_links.txt` | as with `plain` | providers that block UDP to the server |

**Service links.** `files/probes/<probe>/service_links.txt` is a separate
list in the same format, not part of the checks: links for the probe's own
traffic, used only with `outbound_access_policy: vpn`, and then it must have
links. Put them in order of preference: pick ones whose servers allow UDP,
and more than one. Every 15 s every link is checked with a DNS question over
UDP through it; the tunnel stays on its link while that one passes, and moves
to the first passing link in the file when it fails 3 times in a row. With
none passing, it goes straight, without a VPN, until one passes again. A link can be in both files,
but the files do not know about each other.

### 6. Deploy the central server

```bash
ansible-playbook monitoring.yml
```

Check that `https://<grafana domain>` opens the Grafana login page.

### 7. Add the probes

```bash
ansible-playbook xray-pwc-net-join.yml -e name=probe-2
```

This writes `inventory.d/probe-2.yml` and `host_vars/probe-2/xray-pwc-net.yml`,
creates `files/probes/probe-2/` with empty link files (existing ones are
kept), tells the server about the new member, and makes
`files/xray-pwc-net/join/probe-2/compose.yml`. That file is the probe: the image,
and the join token with the probe's key, the server's address and key, your
SSH key and, with `outbound_access_policy: vpn`, the service links. It is
private.

If the probe's network blocks UDP to the server, set
`outbound_access_policy: vpn` for it in `inventory.yml` and put links into
`service_links.txt` before the join: they go into the token. If the join has
run already, run it again: the address and keys stay, the token is new. The
container reads the token only into an empty volume, and a probe whose
tunnel never comes up cannot be reached to fix it: get the token right
before the first start. Once the tunnel is up, `probes.yml` changes these
settings like any other.

Give `compose.yml` to the machine's owner, or start it yourself, in a folder
of its own:

```bash
docker compose up -d
docker compose logs -f               # wait for: wg INFO handshake_ok
```

The image is the release's, from GHCR (`aio_image` in
`group_vars/probes.yml`). Then put the links to check into
`files/probes/probe-2/links.txt` (step 5) and:

```bash
ansible-playbook probes.yml --limit probe-2
```

Check a probe:

```bash
ssh -J root@<central server> -p 2222 abc@probe-2.xray-pwc-net.internal tail -n 20 /app/logs/aio.log
```

```
2026-10-12T17:43:31.366Z probe INFO start version=1.1.0 instance=probe-2 links=2 interval=PT5M jitter=PT2M
2026-10-12T17:43:49.536Z probe INFO check index=0 name=msk-reality result=ok stage=3
2026-10-12T17:43:49.536Z probe WARN check index=1 name=msk-trojan result=partial stage=2 failed="instagram.com: HTTP 429"
```

`alloy` lines with `level=error` about sending mean the data does not reach
Prometheus: look at the `wg` lines for the handshake.

### 8. Look at the results in Grafana

1. Log in as `admin` with `vault_grafana_admin_password`.
2. Add a data source: Prometheus, URL `http://prometheus:9090`.
3. In Explore, run `xray_pwc_tunnel_stage`.

See the [xray-pwc-probe README](../xray-pwc-probe/README.md#metrics) for what
the metric means and for useful queries. Dashboards and alerts are not part of this repository:
build them in the Grafana UI.

### 9. Day-to-day changes

**Change links:** edit `files/probes/<probe>/`, then

```bash
ansible-playbook probes.yml --limit <probe>
```

**Change service links or `outbound_access_policy`:** the same. Mind that
the tunnel moves to the new settings: if they do not work, the probe is out
of reach until its owner starts it again with a new token. Keep a working
service link when you change them.

**Try your changes** (the checker, xray-pwc-connect, the service scripts) on
one probe, before a release: the bundle is built here from the working tree,
`X.Y.Z-dev.<hash>`, and sent.

```bash
ansible-playbook probes.yml --limit <probe> -e xray_pwc_release=local
```

**Release** (see the [root README](../README.md#versions-and-releases)):
after the tag, every probe gets the release's bundle, downloaded here from
GitHub and checked against its `SHA256SUMS`. The release before stays on
the probe, for a rollback.

```bash
ansible-playbook probes.yml                      # xray_pwc_release: VERSION
```

**A release that needs a newer image** (`MIN_IMAGE` in
`xray-pwc-aio/deps.env`): `probes.yml` stops for each probe whose image is
older and says so. Its owner sets the new `image:` in `compose.yml` and runs
`docker compose up -d`; the state stays in the volume.

**Add a probe:** see step 7.

**Remove a probe:** forget it here; its links in `files/probes/<probe>/`
stay, delete them yourself. The container keeps running until its owner
runs `docker compose down -v --rmi all` in the folder of `compose.yml`.

```bash
ansible-playbook xray-pwc-net-remove.yml -e name=<probe>
```

**Lost compose.yml:** run the join again. The address and the keys stay. A
running container does not need it: its state is in its volume.

## How it works

### The probe container

Everything a probe runs is in one container, `xray-pwc-aio`: WireGuard,
xray-pwc-connect for the tunnel, the checker, Alloy, the relay and sshd,
under s6, all as the user `abc`. See
[`../xray-pwc-aio/README.md`](../xray-pwc-aio/README.md). On the host it is
a `compose.yml` and the `xray-pwc-aio_app` volume, which holds everything
else: the releases, the tools, the state and the log.

`probes.yml` connects to the container's sshd, as `abc`, on port 2222 of its
xray-pwc-net address, through the server (`ProxyJump`). The image stays as
it is: Ansible changes only `/app`.

### Releases

A release is a bundle, `xray-pwc-<version>-linux-<arch>.tar.zst`: the
checker's venv with the `xray-pwc-probe` wheel, xray-pwc-connect, the service
scripts and `release.env` (the version, `MIN_IMAGE`, the versions of Alloy,
Caddy and Playwright it needs). `xray_pwc_release` says where it comes from,
and that is the only difference: `tasks/release-fetch.yml` either downloads
the release's bundles from GitHub (checked against `SHA256SUMS`) or runs
`make bundle` on this working tree. They are kept in `../dist/`.

From there on it is the same for both. `probes.yml` checks the probe's image
against `MIN_IMAGE`, unpacks the bundle into `/app/releases/<version>`
unless it is there, and switches it in by moving the `/app/current` link;
then the services restart. The release before stays (`aio_keep_releases:
2`): a rollback is `ln -sfn releases/<old> /app/current` in the container and
`s6-svc -r` of the services.

The image has the Alloy, Caddy and Chromium of its own release. A release
that needs other versions gets them in `/app/opt`: `make tool` builds each
one here from its upstream image, once. Tools that no kept release needs
go.

### The relay

`outbound_access_policy: relay` runs a small Caddy in the probe. Alloy sends
metrics to it in small batches (200 samples, about 4 KB), and it opens a
**new** connection to Prometheus for every request. See
`prom_remote_write_upstream` and `prom_remote_write_max_samples` in
`group_vars/probes.yml`.

It hides nothing: Prometheus is reached only inside xray-pwc-net, so the
network sees the WireGuard UDP flow and nothing of the connections in it,
with the relay or without. The relay helps only where Alloy's one long
connection keeps stalling on that tunnel, which the `alloy` lines show as
`Failed to send batch` again and again. Where the network blocks or freezes
the tunnel itself, the relay does not help: use `vpn`.

### Caddy on the central server

```
caddy_grafana_domain  -> all of Grafana
```

Grafana listens only on `127.0.0.1`, Caddy faces the internet. Certificates
come from Let's Encrypt with the HTTP-01 check on port 80, so they work even
when HTTPS runs on 8443. The cost of 8443 is a port in every URL. Prometheus
is not public at all, see below.

### xray-pwc-net

xray-pwc-net is a small private WireGuard network. The central server is its
server, `wg-quick@xray-pwc-net` with the kernel's WireGuard; the probes are
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

- **Members.** Every member has `host_vars/<name>/xray-pwc-net.yml`: `pwc_net_ip`
  and the key pair. `xray-pwc-net-join.yml` hands out `.1` to the server and the
  first free address from `.10` to the rest, and keeps them on later runs.
  The server config and the CoreDNS names come from these files. Adding a
  member only reloads the server's tunnel (`wg syncconf`): the others stay
  connected.
- **Who reaches whom.** Probes reach only the server: it drops traffic
  between peers (one `iptables` rule in the WireGuard config). You reach the
  probes through the server: an SSH connection the server opens itself is
  not traffic between peers.
- **SSH.** In a probe, sshd (port 2222) lets in `abc` with `pwc_net_ssh_key`,
  and only from the server's xray-pwc-net address. Ansible trusts a probe's host key on
  first use: the tunnel already proves who is on the other end.
- **Names.** CoreDNS on the server answers `<member name>.xray-pwc-net.internal` and
  `prom.xray-pwc-net.internal`, and forwards every other name. The server's own
  resolver sends that zone to it, so `ssh -J <server> -p 2222 abc@probe-2.xray-pwc-net.internal`
  works. The checker keeps the probe's own DNS, so the tests see the probe's
  real network.
- **Prometheus has no auth.** It listens on `127.0.0.1` and the xray-pwc-net
  address only.
- **No harm to the host.** On a probe, the tunnel lives in the container's
  own network: the host gets no interface, route or rule. Only
  `pwc_net_subnet` goes into the tunnel.
- **Service links.** On a probe with `outbound_access_policy: vpn`, the
  tunnel's endpoint is `127.0.0.1:51974`, and the `connect-forward` service,
  `xray-pwc-connect -forward`, sends its UDP through one link at a time to the
  server. See step 5 for how the link is chosen, and the
  [xray-pwc-connect README](../xray-pwc-connect/README.md#forward-mode). The
  `connect-forward` lines in the log show the links by name only.

### Other details

- **Missing `vault.yml` is not an error for Ansible.** It silently skips a
  missing file in `vars_files`. `monitoring.yml` starts with an `assert` that
  checks the keys it needs.
- **Docker on the central server.** Ubuntu uses `docker-compose-v2`, Debian
  13 uses `docker-compose`. The playbook picks the right one and installs
  Docker only if it is missing. On the probes Docker is yours to install.
- **Old data after a network break.** Alloy keeps unsent data for up to 8
  hours and sends it when the network is back, and Prometheus accepts samples
  that late (`out_of_order_time_window`): both are `metrics_backlog` in
  `group_vars/all.yml`. Data older than that is lost.
- **Links stay out of the terminal.** The tasks that write them use
  `no_log: true`, so `--diff` does not print them.
- **A "probe is gone" alert:**
  `absent_over_time(up{job="xray-pwc-probe",host="probe-1"}[10m])`, with
  "No data" state set to OK. Otherwise the alert fires all the time on a
  healthy probe.

## Secrets

Everything secret is git-ignored and stays on your machine:

| file | holds |
|---|---|
| `inventory.yml`, `inventory.d/` | your hosts |
| `vault.yml` | the Grafana password |
| `host_vars/<name>/xray-pwc-net.yml` | each member's xray-pwc-net address and keys |
| `files/probes/` | share links, which contain credentials |
| `files/xray-pwc-net/join/` | the probes' `compose.yml` with their join tokens |

`vault.yml` is plain text, not encrypted with ansible-vault: protect your
machine accordingly, and keep a backup of it and of `host_vars/`. On a
probe, the secrets are in `compose.yml` (the token) and in the volume,
readable by root on the host.

## Pinned versions

`prom/prometheus:v3.15.0`, `grafana/grafana:13.2.2`, `coredns/coredns:1.12.1`;
in the probes: Alloy and Caddy in
[`../xray-pwc-aio/deps.env`](../xray-pwc-aio/deps.env), and the image's own
in [`../xray-pwc-aio/Dockerfile`](../xray-pwc-aio/Dockerfile).
