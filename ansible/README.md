# Ansible: probes fleet monitoring

These playbooks deploy the whole system: a central server with Prometheus and
Grafana, and any number of probes running xray-tun-pwc.

| playbook | hosts | what it does |
|---|---|---|
| `monitoring.yml` | `monitoring` | Prometheus (receives remote write) + Grafana + Caddy |
| `probes.yml` | `probes` | xray-tun-pwc + Grafana Alloy + a small relay |
| `probes-clean.yml` | `probes` | removes the probe stack, its data and its images |

## Step by step

### 1. What you need

- **Your machine** (where you run Ansible): Ansible, Docker with `buildx`,
  Python 3, and a checkout of this whole repository. The probe image is
  built here from the `xray-tun-pwc/` and `xray-tun-connect/` sources.
- **A central server**: Debian or Ubuntu, a public IP, root SSH access, and
  two DNS names pointing to it, one for Grafana and one for Prometheus. Port
  80 and your HTTPS port must be open.
- **Probes**: Debian or Ubuntu on amd64 or arm64, SSH access with
  passwordless `sudo`, and outgoing internet. They can be behind NAT.

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

Set the central server under `monitoring`, every probe under `probes`, and
the two domain names. If port 443 is busy on the central server, set
`caddy_https_port: 8443`.

The probe's name in the inventory (`probe-1` in the example) is important.
It becomes the `instance` label in metrics, the user name for remote write,
and the folder name for its links. Choose names that say where the probe is,
for example `spb-home-1`.

### 4. Set the passwords

```bash
cp vault.yml.example vault.yml
$EDITOR vault.yml
```

You need a Grafana admin password and, for every probe, a remote write
password with its bcrypt hash. To make them:

```bash
openssl rand -hex 24                                    # a password
docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext '<the password>'
```

Put the password into `password` and the hash into `bcrypt`. `vault.yml` is
plain text and git-ignored. Keep a backup of it somewhere safe.

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

### 6. Deploy the central server

```bash
ansible-playbook monitoring.yml
```

Check it:

- `https://<grafana domain>` opens the Grafana login page;
- `curl -X POST https://<prometheus domain>/api/v1/write` answers `401`;
- any other path on the Prometheus domain answers `404`.

### 7. Deploy the probes

```bash
ansible-playbook probes.yml
```

The first run builds the image on your machine and copies it to every probe,
so it takes a few minutes. Later runs skip this step until the code changes.

Check a probe:

```bash
ssh probe-1 sudo docker logs xray-tun-pwc
```

```
xray-tun-pwc 0.1.0 | instance probe-1 | 2 tunnels | every PT5M + up to PT2M
msk-reality - OK
msk-trojan - partial[instagram.com: HTTP 429]
```

`ssh probe-1 sudo docker logs xray-tun-pwc-alloy` must not show `401`. A
`401` means the central server does not know this probe yet: run
`monitoring.yml` again.

### 8. Look at the results in Grafana

1. Log in as `admin` with `vault_grafana_admin_password`.
2. Add a data source: Prometheus, URL `http://prometheus:9090`.
3. In Explore, run `xrc_tunnel_up`.

See the [main README](../README.md#metrics) for what the metric means and
for useful queries. Dashboards and alerts are not part of this repository:
build them in the Grafana UI.

### 9. Day-to-day changes

| task | what to do |
|---|---|
| change links | edit `files/probes/<probe>/`, run `ansible-playbook probes.yml --limit <probe>` |
| change the code | run `ansible-playbook probes.yml`; a new image is built and sent |
| add a probe | add it to `inventory.yml`, `vault.yml` and `files/probes/`, run `monitoring.yml`, then `probes.yml --limit <probe>` |
| remove a probe | `ansible-playbook probes-clean.yml --limit <probe>`, remove it from the files above, run `monitoring.yml` |

Always run `monitoring.yml` before `probes.yml` when you add a probe. The
central server must know the new user first, or the probe gets `401`.

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

Every probe runs a small Caddy relay. Alloy sends metrics to the relay, and
the relay opens a **new** connection to the central server for every request.
Alloy also sends small batches (200 samples, about 4 KB). See
`prom_remote_write_upstream` and `prom_remote_write_max_samples` in
`group_vars/probes.yml`.

### Caddy on the central server

```
caddy_grafana_domain     -> all of Grafana
caddy_prometheus_domain  -> ONLY /api/v1/write, with basic auth
                            every other path returns 404
```

Prometheus and Grafana listen only on `127.0.0.1`. Certificates come from
Let's Encrypt with the HTTP-01 check on port 80, so they work even when HTTPS
runs on 8443. The cost of 8443 is a port in every URL.

Basic auth users come from `vault_probe_remote_write`: the user name is the
probe's inventory name, the password hash is its `bcrypt` field. Caddy 2.6.2
from the Ubuntu repository understands only `basicauth`, not `basic_auth`. An
empty basicauth block returns 401 to everyone, so the playbook requires at
least one probe in the vault.

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

## Pinned versions

`prom/prometheus:v3.15.0`, `grafana/grafana:13.2.2`, `grafana/alloy:v1.20.0`,
`caddy:2.11.7-alpine`.
