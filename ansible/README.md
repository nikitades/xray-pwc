# Ansible: probes fleet monitoring

These playbooks deploy the whole system: a central server with Prometheus and
Grafana, and any number of probes running xray-tun-pwc.

| playbook | hosts | what it does |
|---|---|---|
| `monitoring.yml` | `monitoring` | the vpnc-net server + Prometheus (receives remote write) + Grafana + Caddy + CoreDNS |
| `probes.yml` | `probes` | a vpnc-net client + xray-tun-pwc + Grafana Alloy + a small relay |
| `probes-clean.yml` | `probes` | removes the probe stack, its data and its images (`-e vpnc_net_remove=true`: also the vpnc-net client) |
| `vpnc-net-join.yml` | your machine + `monitoring` | adds a vpnc-net member: address, keys, a join file or an app config; tells the server |
| `vpnc-net-remove.yml` | your machine + `monitoring` | forgets a vpnc-net member and tells the server |

Run every command from this directory: `ansible.cfg` here sets the inventory
and the collections path.

```bash
ansible-playbook monitoring.yml                       # the central server
ansible-playbook probes.yml                           # every probe
ansible-playbook probes.yml --limit <probe>           # one probe
ansible-playbook vpnc-net-join.yml -e name=<probe> -e ssh_user=<user>   # a new probe
ansible-playbook vpnc-net-join.yml -e name=<name> -e group=vpnc_net_peers   # a laptop
ansible-playbook vpnc-net-remove.yml -e name=<name>                     # forget a member
```

Add `--check --diff` to any of them to see the changes without making them.

## Step by step

### 1. What you need

- **Your machine** (where you run Ansible): Ansible, Docker with `buildx`,
  Python 3, and a checkout of this whole repository. The probe image is
  built here from the `xray-tun-pwc/` and `xray-tun-connect/` sources.
- **A central server**: Debian or Ubuntu, a public IP, root SSH access, and
  a DNS name for Grafana pointing to it. Port 80, your HTTPS port and UDP
  `vpnc_net_port` (51973) must be open, also in the hosting's own firewall.
- **Probes**: Debian or Ubuntu on amd64 or arm64 with systemd, `python3`,
  `iptables` and `openssl`, a user with passwordless `sudo`, and outgoing
  internet. No public address needed: Ansible reaches them through vpnc-net.
  Where the provider blocks UDP to the server, vpnc-net goes through your VPN
  links instead, see service links in step 5.
- **The AmneziaWG app** on your machine, for the vpnc-net tunnel.

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
`vpnc_net_ssh_key`, the SSH key Ansible uses for the probes. If port 443 is busy
on the central server, set `caddy_https_port: 8443`. Probes that already have
a public address can go under `probes` too; the rest come with
`vpnc-net-join.yml`.

The probe's name is important. It becomes the `instance` label in metrics,
its DNS name in vpnc-net and the folder name for its links. Choose names that say
where the probe is, for example `spb-home-1`.

### 4. Set the secrets and join vpnc-net

```bash
cp vault.yml.example vault.yml
$EDITOR vault.yml
```

You need a Grafana admin password (`openssl rand -hex 24`) and the vpnc-net
obfuscation numbers; the example file says how to pick them. `vault.yml` is
plain text and git-ignored. Keep a backup of it somewhere safe.

Then give the server and your machine their vpnc-net addresses and keys:

```bash
# the server, named as in inventory.yml
ansible-playbook vpnc-net-join.yml -e name=mon-1
# your machine
ansible-playbook vpnc-net-join.yml -e name=laptop -e group=vpnc_net_peers
```

The keys land in `host_vars/<name>/vpnc-net.yml`, git-ignored. Import
`files/vpnc-net/join/laptop.conf` into the AmneziaWG app and run the `/etc/resolver`
line from `files/vpnc-net/join/laptop.txt`, after step 6.

### 5. Add the VPN links

Create one folder per probe, named exactly like the probe in the inventory,
and put the share links there, one per line:

```bash
mkdir -p files/probes/probe-1
$EDITOR files/probes/probe-1/links.txt
```

```
# Home ISP, main channel
vless://11111111-1111-1111-1111-111111111111@198.51.100.10:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=jNXHt1yRo0vDuchQlIP6Z0ZvjT3KtzVI-T4E7RoLJS0&sid=6ba85179e30d4fc2&type=tcp&flow=xtls-rprx-vision#msk-reality

trojan://SuperSecret123@198.51.100.12:443?security=tls&sni=t.example.com&type=tcp#msk-trojan
```

The text after `#` is the link's name in logs and metrics, so make it clear.
Empty lines and lines starting with `#` are skipped. All files in the folder
are joined into one list. `files/probes/` is git-ignored.

Different probes can have different links, or the same ones. The same link
on several probes is what lets you compare networks.

**Service links.** `files/probes/<probe>/service_links.txt` is a separate
list in the same format, not part of the checks: links for the probe's own
traffic. With it, the probe's vpnc-net tunnel does not go straight to the
server over UDP but through one of these links (`vpnc-net-via.service`), for
providers that block the UDP. Use it only where the direct tunnel does not
hold. Pick links whose servers allow UDP, and more than one: if Prometheus
stops answering through the tunnel 3 times in a row, 15 s apart, the next
link takes over, round and round. After the last link the tunnel tries going
straight to the server, without a VPN, then starts over. A link can be in both files, but the files
do not know about each other.

### 6. Deploy the central server

```bash
ansible-playbook monitoring.yml
```

Check it:

- `https://<grafana domain>` opens the Grafana login page;
- the AmneziaWG app shows a handshake, and `ping 10.213.97.1` answers.

### 7. Add the probes

**A probe without a public address:**

```bash
ansible-playbook vpnc-net-join.yml -e name=probe-2 -e ssh_user=ars   # -e arch=arm64 for ARM
```

This writes `inventory.d/probe-2.yml` and `host_vars/probe-2/vpnc-net.yml`,
tells the server about the new member, and makes
`files/vpnc-net/join/probe-2-amd64.sh` with a passphrase in
`files/vpnc-net/join/probe-2.txt`. Send the file and the passphrase to the
machine by different channels. There, as `ssh_user`:

```bash
bash probe-2-amd64.sh
```

It checks the machine first and changes nothing if it does not fit. Then it
installs the tunnel, adds your SSH key and waits for the server:
`vpnc-net: OK, probe-2 is 10.213.97.12`. Then, here:

```bash
ansible-playbook probes.yml --limit probe-2
```

**A probe with a public address** (in `inventory.yml`):
`ansible-playbook vpnc-net-join.yml -e name=probe-1`, then
`ansible-playbook probes.yml --limit probe-1`. The first run sets the tunnel
up over the public address, puts `vpnc_net_ssh_key` on the probe, and if SSH
through vpnc-net works, writes `host_vars/probe-1/ssh.yml`. From then on Ansible
connects through vpnc-net. `-e vpnc_net_ssh=false` goes back to the public address.

The first run builds the probe image on your machine and copies it to the
probe, so it takes a few minutes. Later runs skip this step until the code
changes.

Check a probe:

```bash
ssh ars@probe-1.vpnc-net.internal sudo docker logs xray-tun-pwc
```

```
xray-tun-pwc 0.1.0 | instance probe-1 | 2 tunnels | every PT5M + up to PT2M
msk-reality - OK
msk-trojan - partial[instagram.com: HTTP 429]
```

`sudo docker logs xray-tun-pwc-alloy` must not show send errors. If it does,
check the tunnel: `sudo /opt/vpnc-net/bin/awg show` must show a recent handshake.

### 8. Look at the results in Grafana

1. Log in as `admin` with `vault_grafana_admin_password`.
2. Add a data source: Prometheus, URL `http://prometheus:9090`.
3. In Explore, run `pwc_tunnel_stage`.

See the [xray-tun-pwc README](../xray-tun-pwc/README.md#metrics) for what
the metric means and for useful queries. Dashboards and alerts are not part of this repository:
build them in the Grafana UI.

### 9. Day-to-day changes

**Change links:** edit `files/probes/<probe>/`, then

```bash
ansible-playbook probes.yml --limit <probe>
```

**Change the code:** a new image is built and sent.

```bash
ansible-playbook probes.yml
```

**Add a probe:** see step 7.

**Add a laptop or a phone:** import the `.conf` it writes into the AmneziaWG app.

```bash
ansible-playbook vpnc-net-join.yml -e name=<name> -e group=vpnc_net_peers
```

**Remove a machine:** clean the probe first, while Ansible still reaches it,
then forget it and tell the server. Remove its `files/probes/<probe>/` too.

```bash
ansible-playbook probes-clean.yml --limit <name> -e vpnc_net_remove=true
ansible-playbook vpnc-net-remove.yml -e name=<name>
```

**Lost the join file:** run the join again. The address and the keys stay,
the file and the passphrase are new.

```bash
ansible-playbook vpnc-net-join.yml -e name=<probe>
```

## How it works

### Image build and delivery

`probes.yml` builds the image with `docker buildx` on your machine, for the
probe's CPU type. The image tag is a hash of the `xray-tun-pwc/` and
`xray-tun-connect/` sources, so the same code gives the same tag. If the
probe already has that tag, nothing is built or sent. Otherwise the image goes
to the probe as an archive over SSH and is loaded with `docker load`. The
probe never needs a registry for the checker.

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
there is no relay container, and Alloy sends straight to
`prom.vpnc-net.internal` over one connection, 2000 samples per batch,
resolving the name with the vpnc-net DNS server. If `docker logs
xray-tun-pwc-alloy` then shows `Failed to send batch` again and again, turn
the relay back on.

### Caddy on the central server

```
caddy_grafana_domain  -> all of Grafana
```

Grafana listens only on `127.0.0.1`, Caddy faces the internet. Certificates
come from Let's Encrypt with the HTTP-01 check on port 80, so they work even
when HTTPS runs on 8443. The cost of 8443 is a port in every URL. Prometheus
is not public at all, see below.

### vpnc-net

vpnc-net is a small private network on AmneziaWG (WireGuard with obfuscation, so
it passes Russian DPI). The central server is the vpnc-net server; the probes and
the `vpnc_net_peers` group (your laptop) are clients. Clients connect out to the
server and keep the tunnel open, so probes behind NAT are reachable.

```
laptop (admin) --+                      +-- probe-1  10.213.97.10
                 +-- server 10.213.97.1 +
                 |   Prometheus :9090   +-- probe-2  10.213.97.11
                 |   CoreDNS    :53
```

- **Members.** Every member has `host_vars/<name>/vpnc-net.yml`: `vpnc_net_ip`, the
  key pair, and `vpnc_net_admin: true` for `vpnc_net_peers`. `vpnc-net-join.yml` hands out
  `.1` to the server and the first free address from `.10` to the rest, and
  keeps them on later runs. The server config, the CoreDNS names and the
  firewall rules come from these files. Adding a member only reloads the
  server's tunnel: the other members stay connected.
- **The join file** is a bash header and an encrypted archive
  (`openssl enc -aes-256-cbc -pbkdf2`) in one file: the AmneziaWG binaries,
  the same tunnel files `probes.yml` makes, the subnet check and your SSH
  public key; with service links already in place, also xray-tun-connect and
  those links. The repository has only the generator; the file and its
  passphrase live in git-ignored `files/vpnc-net/join/`.
- **Prometheus has no auth.** It listens on `127.0.0.1` and the vpnc-net
  address only. The probe relay sends to `http://prom.vpnc-net.internal:9090`.
- **Names.** CoreDNS on the server answers `<member name>.vpnc-net.internal` and
  `prom.vpnc-net.internal`, and forwards every other name. Only the probe relay
  uses it; the checker keeps the probe's own DNS, so the tests see the
  probe's real network.
- **Who reaches whom.** Admin members reach everything, for example SSH to a
  probe on any port. Probes reach only the server; the server drops traffic
  between probes. The rules live in `/etc/vpnc-net/firewall.sh` and disappear
  with the tunnel.
- **SSH.** Ansible logs in with `vpnc_net_ssh_key` only (`IdentitiesOnly`) and
  trusts a probe's host key on first use: the tunnel already proves who is on
  the other end. For your own SSH, add to `~/.ssh/config`:
  `Host *.vpnc-net.internal` with `IdentityFile` and `IdentitiesOnly yes`.
- **No harm to other VPNs.** Only `vpnc_net_subnet` goes into the tunnel: no
  default route, no DNS changes. The playbook and the join file stop if the
  subnet is already used by a route or an address on the host. AmneziaWG
  runs in userspace (no kernel module), built locally from source and
  installed into `/opt/vpnc-net`, with its config in `/etc/vpnc-net` and one systemd
  unit, `vpnc-net.service`. It does not touch a packaged amneziawg.
- **Service links.** On a probe with `files/probes/<probe>/service_links.txt`,
  the tunnel's endpoint is `127.0.0.1:51974`. `vpnc-net-via.service` runs
  `xray-tun-connect -forward` with all the links: port 51974 sends the
  tunnel's UDP through one link at a time to the server. Every 15 s it fetches
  `vpnc_net_via_health_url`, Prometheus' `/-/healthy` on the server's vpnc-net
  address, so the check goes through the tunnel and through the link, UDP
  included. 3 failures in a row, and the port moves to the next link, and after
  the last one to `direct` (no VPN, plain UDP to the server); the port itself
  stays, so the tunnel only sees a short gap. WireGuard does not mind
  that the server now sees it coming from the VPN server's address. Without
  the file, the unit is removed and the tunnel goes straight again.
  `journalctl -u vpnc-net-via` shows which link is in use, by name only.
- **Removing the tunnel** cuts Ansible off a probe that has no other
  address. So `probes-clean.yml` keeps it unless you pass
  `-e vpnc_net_remove=true`.

### Other details

- **Missing `vault.yml` is not an error for Ansible.** It silently skips a
  missing file in `vars_files`. Every playbook starts with an `assert` that
  checks the keys it needs.
- **Docker packages.** Ubuntu uses `docker-compose-v2`, Debian 13 uses
  `docker-compose`. The playbooks pick the right one. Docker is installed only
  if it is missing.
- **Old data after a network break.** Alloy keeps unsent data for up to 8
  hours and sends it when the network is back. Prometheus accepts samples up
  to 1 hour old (`out_of_order_time_window`). Data older than that is lost.
- **One extra restart on the first run.** Config templates notify the restart
  handler before the first `docker compose up`, so the stack restarts once.
- **Links stay out of the terminal.** The task that writes them uses
  `no_log: true`, so `--diff` does not print them.
- **A "probe is gone" alert:**
  `absent_over_time(up{job="xray-tun-pwc",host="probe-1"}[10m])`, with
  "No data" state set to OK. Otherwise the alert fires all the time on a
  healthy probe.

## Secrets

Everything secret is git-ignored and stays on your machine:

| file | holds |
|---|---|
| `inventory.yml`, `inventory.d/` | your hosts |
| `vault.yml` | the Grafana password and the vpnc-net obfuscation numbers |
| `host_vars/<name>/vpnc-net.yml` | each member's vpnc-net address and keys |
| `files/probes/` | share links, which contain credentials |
| `files/vpnc-net/join/` | join files, their passphrases, laptop configs |

`vault.yml` is plain text, not encrypted with ansible-vault: protect your
machine accordingly, and keep a backup of it and of `host_vars/`. On a
probe, the links are a file readable by root and the checker's user.

## Pinned versions

`prom/prometheus:v3.15.0`, `grafana/grafana:13.2.2`, `grafana/alloy:v1.20.0`,
`caddy:2.11.7-alpine`.
