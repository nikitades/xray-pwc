# VpnChecker: reference for AI agents

Dense, complete map of the repository. Human docs: `README.md`, `xray-tun-pwc/README.md`,
`xray-tun-connect/README.md`, `ansible/README.md`. When code and this file disagree, the
code wins; fix this file.

## 1. Purpose

Monitors Xray VPN share links (vless/vmess/trojan/ss) *from inside target networks*
(e.g. a home ISP in a Russian city). "Probes" (small Linux boxes) open real websites
through each link in headless Chromium every few minutes and report how far they got.
Results flow to one central server (Prometheus + Grafana). Probes need no public IP:
they dial out over a private AmneziaWG network, **vpnc-net**, which also carries SSH
for Ansible.

Version 0.1.0 (checker), 0.4.0 (xray-tun-connect). Semver: new feature -> minor, fix -> patch;
bump `xray-tun-connect/cmd/xray-tun-connect/VERSION` with every change to it. Few probes in production.

## 2. Repository layout

```
xray-tun-connect/   Go. share links -> local ports: all as SOCKS5, or one rotating forward port. Embeds xray-core + libXray.
  cmd/xray-tun-connect/{main.go,VERSION}
  internal/tun/{tun.go,config.go,forward.go,links.go,log.go,*_test.go}
xray-tun-pwc/       Python 3.14, uv. The checker ("Pressurising Web Checker"). Dockerfile builds both.
  src/xray_tun_pwc/{domain,app,ports,entrypoints}/, container.py, fakes.py; tests next to code (*_test.py)
ansible/            Deployment. Run every command from this directory (ansible.cfg sets inventory).
  monitoring.yml probes.yml probes-clean.yml vpnc-net-join.yml vpnc-net-remove.yml
  tasks/{docker,vpnc-net,vpnc-net-build,vpnc-net-build-via,vpnc-net-ssh}.yml
  handlers/vpnc-net.yml
  group_vars/{all,monitoring,probes}.yml
  templates/{monitoring,probe,vpnc-net}/
  files/vpnc_net_check_subnet.py
```

## 3. Runtime topology

```
probe:  xray-tun-pwc -> xray-tun-connect (SOCKS 127.0.0.1:10000+i) -> VPN link -> websites
        xray-tun-pwc :8000/metrics -> alloy (scrape 30s) -> relay :8080 -> vpnc-net -> prometheus
server: prometheus (remote-write receiver) <- grafana <- caddy (public HTTPS)
```

### Central server (`monitoring` group, first host = vpnc-net server)

Compose project `vpn-monitoring`, dir `/opt/vpn-monitoring`:

| service | image | binds | notes |
|---|---|---|---|
| prometheus | prom/prometheus:v3.15.0 | 127.0.0.1:9090, <vpnc ip>:9090 | `--web.enable-remote-write-receiver`, retention 180d / 40GB, out_of_order window 1h, **no auth** |
| grafana | grafana/grafana:13.2.2 | 127.0.0.1:3000 | admin password from vault; data source URL `http://prometheus:9090` (added by hand) |
| coredns | coredns/coredns:1.12.1 | <vpnc ip>:53 udp+tcp | zone `vpnc-net.internal` from `coredns/vpnc-net.hosts` (`<inventory name>.vpnc-net.internal`, `prom.vpnc-net.internal`); `.` forwarded to 1.1.1.1, 8.8.8.8; auto reload |

Host (not Compose): Caddy system package (only public web; Grafana at
`caddy_grafana_domain:caddy_https_port`, ACME HTTP-01 on :80, so 8443 works when 443 is
taken); `vpnc-net.service` (AmneziaWG server, UDP 51973); sysctl `ip_forward`; docker
drop-in `docker.service.d/vpnc-net.conf` (After/Wants vpnc-net, because containers bind
the vpnc address).

### Probe (`probes` group)

Compose project `xray-tun-pwc`, dir `/opt/xray-tun-pwc`:

| service | image | notes |
|---|---|---|
| xray-tun-pwc | `xray-tun-pwc:<srchash>-<arch>` (built locally) | `links.txt` (uid 1000, 0400); probe's own DNS (`probe_dns` override), shm 512m |
| alloy | grafana/alloy:v1.20.0 | scrapes checker + `prometheus.exporter.unix` (host /proc,/sys,/) every 30s; remote_write to `http://relay:8080/api/v1/write`, max 200 samples/batch; WAL up to 8h |
| relay | caddy:2.11.7-alpine | reverse_proxy to `prom_remote_write_upstream` = `http://prom.vpnc-net.internal:9090`, `keepalive off` (new TCP per request: some DPI stalls a TCP flow to foreign hosting after ~16 KB); `dns: [vpnc server ip]` |

Host (not Compose): `vpnc-net.service` (client); `vpnc-net-via.service` only if
`files/probes/<host>/service_links.txt` exists (see 6.4).

## 4. xray-tun-pwc (checker)

Per link, every `PWC_CHECK_INTERVAL` (PT5M) + random `PWC_CHECK_JITTER` (PT2M), links
sequentially, one Chromium at a time:

1. xray-tun-connect starts SOCKS tunnel (links passed via stdin, not argv).
2. Open `PWC_HEAVY_VISITS` (3) random URLs from `PWC_HEAVY_SITES`; success = `DOMContentLoaded`.
3. Open all `PWC_TARGET_SITES` (Instagram, Facebook); success = HTTP 200.
4. Close tunnel + browser.

Each page: 3 attempts, 30 s navigation timeout, backoff 2 s, 4 s.

Stage (`pwc_tunnel_stage`): 0 link broken, 1 unreachable (no proof traffic passed),
2 partial, 3 all OK. Proof of passage: page opened, any HTTP status, TLS handshake
failure. Not proof: `ERR_PROXY_CONNECTION_FAILED`, timeout, reset, DNS error.

Metrics on :8000: `pwc_tunnel_stage{instance,tunnel,stable_id}`;
`pwc_tunnel_error{...,error_type}` = 1 if last check hit it, 0 if only earlier.
`instance` = `PWC_INSTANCE` = inventory hostname; `tunnel` = text after `#`;
`stable_id` = hash of link without name. Alloy adds `job="xray-tun-pwc"`, `host`.

Env: `PWC_CONFIG_PATH`, `PWC_INSTANCE`, `PWC_HEAVY_SITES`, `PWC_HEAVY_VISITS`,
`PWC_TARGET_SITES`, `PWC_CHECK_INTERVAL`, `PWC_CHECK_JITTER`, `PWC_HTTP_PORT`,
`PWC_XRAY_TUN_CONNECT_BIN` (else `$GOBIN`, `$GOPATH/bin`, `PATH`; minimum version checked).

Architecture: hexagonal. `domain/` entities, sites, schedule; `app/` use cases + Protocol
ports; `ports/` adapters (Playwright browser, Prometheus metrics, xray-tun-connect
process); `entrypoints/cli.py`; `container.py` (`build_container`, `build_test_container`);
`fakes.py`. Dev: `uv run pytest`, `uv run ruff check .`, `uvx ty check src`.

Dockerfile: stage `golang:1.27.2-trixie` builds xray-tun-connect from build context
`xray-tun-connect`; runtime `python:3.14.8-slim-trixie` + uv + Playwright chromium shell;
user `checker` uid 1000. Build: `docker buildx build --build-context xray-tun-connect=../xray-tun-connect .`

## 5. xray-tun-connect

Two modes:

- **All at once** (default): link i = SOCKS5 on `start-port+i`. Used by the checker.
- **Forward** (`-forward host:port`): one port, `start-port`, forwards TCP+UDP to the
  target through one link at a time (inbound protocol `tunnel`). `tun.DirectLink`
  (`direct://#direct`, outbound `freedom`, no VPN) is appended last unless
  `-disable-direct`; it counts as a candidate. With >1 candidate a health
  check runs every `-health-interval` (15s, timeout `-health-timeout` 5s); `-health-fails`
  (3) failures in a row -> close the xray instance, start the next link on the **same
  port** (round robin; links that fail to start are skipped, a full round of failures
  waits one interval). Check: `-health-url` given -> plain GET from this machine (status
  < 400 passes, redirects not followed); not given -> GET `https://www.gstatic.com/generate_204`
  through the link via an extra internal SOCKS inbound (`check` tag) on a free port.
  A single candidate is never checked. stdout: the report line (every link with the shared
  port), then one JSON event per line: `{"event":"active"|"failed","index","name","reason"}`;
  indexes are positions in the full list. Code: `internal/tun/forward.go`.

Flags: `-link` (repeat), `-links`, `-stdin-links` (until first empty line), `-start-port`
10000, `-listen` 127.0.0.1, `-forward`, `-health-url`, `-health-interval`,
`-health-timeout`, `-health-fails`, `-disable-direct`, `-xray-log-level` warning,
`-ready-timeout` 10s, `-version`.

Tests (`go test -race ./...`): link splitting/parsing, config build and error mapping,
ports reserved for bad links, real traffic through a SOCKS port and through a forward
(TCP and UDP echo, via `DirectLink`), rotation on the same port, round robin, healthy
link kept, single candidate unchecked, probe through the link, skip of links that do not
start, fallback to direct, CLI flags and usage errors (`cmd/xray-tun-connect/main_test.go`).

- Prints exactly one JSON line `{version,listen,tunnels:[{index,port,name,protocol,ok,error}]}`
  on stdout; logs on stderr. Bad links keep index/port with `ok:false` (stable port map);
  config is rebuilt dropping the outbound xray rejects, one per round.
- Lives until SIGINT/SIGTERM, or EOF on stdin **when stdin is a pipe** (parent death kills
  tunnels). To feed links from a file without that, redirect from a regular file.
- Under systemd stderr is a journald socket: xray-core fails `open /dev/stderr` -> use
  `-xray-log-level none` (sets error path "none").
- Exit: 0 clean, 1 core failed / no usable link, 2 usage.
- Build: Go >= 1.27.1. `make build|install|dist|test`. VERSION is embedded; bump it when
  code changes (Ansible caches the probe-side binary by VERSION).

## 6. vpnc-net (AmneziaWG private network)

### 6.1 Parameters (`group_vars/all.yml`)

| var | value |
|---|---|
| `vpnc_net_interface` | `vpnc-net` |
| `vpnc_net_subnet` | `10.213.97.0/24` (server .1, others: first free from .10) |
| `vpnc_net_port` | UDP 51973 |
| `vpnc_net_mtu` / `keepalive` | 1280 / 25 |
| `vpnc_net_domain` | `vpnc-net.internal` |
| `vpnc_net_server` | `groups.monitoring[0]` |
| `vpnc_net_endpoint` | `caddy_grafana_domain:vpnc_net_port` |
| `vpnc_net_members` | hosts with `vpnc_net_ip` defined |
| `vpnc_net_admin_ips` | members with `vpnc_net_admin: true` |
| binaries | amneziawg-go v3.1.20260828, amneziawg-tools v3.1.20260812, built in `golang:1.25.1-bookworm` |
| paths | bin `/opt/vpnc-net/bin`, conf `/etc/vpnc-net`, unit `/etc/systemd/system/vpnc-net.service` |
| local cache | `ansible/files/vpnc-net/bin/<go>-<tools>-<arch>/`, `xray-tun-connect-<VERSION>-<arch>` |
| join output | `ansible/files/vpnc-net/join/` |

Obfuscation (AWG 1.x Jc/Jmin/Jmax/S1/S2/H1-H4) from `vault_vpnc_net_obfuscation`; S/H
must match on all peers. Userspace only (`WG_QUICK_USERSPACE_IMPLEMENTATION`), no kernel
module, independent of any packaged amneziawg.

### 6.2 Design rules

- Hub and spoke. Clients `AllowedIPs = subnet` only: no default route, no `DNS=`, so other
  VPNs on the host keep working. Subnet overlap check (`vpnc_net_check_subnet.py <subnet>
  <our ifaces...>`) aborts if a foreign route/address overlaps.
- Firewall (`/etc/vpnc-net/firewall.sh up|down`, run by PostUp/PostDown/ExecReload):
  server chain `VPNC-NET-FWD` (admins -> anywhere, ESTABLISHED back, rest dropped:
  probes cannot reach each other); client chain `VPNC-NET-IN` (accept admin IPs).
- `ExecReload` = `awg syncconf` + firewall up: adding peers never drops others.
- Restart handlers use `systemctl restart --no-block` + `wait_for_connection` because
  Ansible may be connected through the tunnel. Removal uses transient
  units (`systemd-run`) for the same reason.
- Only the probe relay uses CoreDNS; the checker keeps the probe's real DNS.

### 6.3 Members and files (all git-ignored)

- `host_vars/<name>/vpnc-net.yml`: `vpnc_net_ip`, `vpnc_net_private_key`,
  `vpnc_net_public_key`, optional `vpnc_net_admin`. Written by `vpnc-net-join.yml`;
  rerun keeps address and keys. Keys: openssl X25519, raw 32 bytes base64.
- `inventory.d/<name>.yml`: new hosts (inventory sources: `inventory.yml,inventory.d`).
  Laptops: group `vpnc_net_peers`, `ansible_connection: local`, admin.
- `host_vars/<probe>/ssh.yml`: written by `tasks/vpnc-net-ssh.yml` once SSH through the
  tunnel works: `ansible_host` = vpnc ip (`-e vpnc_net_ssh=false` -> old address in
  `vpnc_net_ssh_old_host`), user/port from `ssh -G`, key `vpnc_net_ssh_key` (inventory
  var, default `~/.ssh/id_ed25519.pub`), `-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new`.
  The monitoring host stays on its public address.
- Join file `<name>-<arch>.sh`: bash header + `__PAYLOAD_BELOW__` + tar.gz encrypted
  `openssl enc -aes-256-cbc -pbkdf2 -iter 200000`; passphrase (`openssl rand -hex 16`) in
  `<name>.txt`, or env `VPNC_NET_JOIN_PASS`. Payload: awg binaries, the same templates
  as the role (so Ansible later sees no diff), subnet check, SSH pubkey, and with service
  links also xray-tun-connect + `service_links.txt` + via files. `install.sh` validates
  first (Linux, systemd, arch, ip/iptables/python3, subnet), installs, adds the key for
  `$SUDO_USER`, waits for a handshake (30 s, 60 s with via).
- Laptop: `<name>.conf` for the AmneziaWG app; macOS DNS via
  `/etc/resolver/vpnc-net.internal` containing `nameserver 10.213.97.1`.

### 6.4 Service links / via (DPI workaround)

Some ISPs block UDP to the server. If `files/probes/<host>/service_links.txt` exists
(`vpnc_net_via` = not server and file exists):

- client `Endpoint = 127.0.0.1:51974` (`vpnc_net_via_port`); `vpnc-net.service` Wants/After
  `vpnc-net-via.service`.
- `vpnc-net-via.service` = `xray-tun-connect -stdin-links -forward <endpoint>
  -start-port 51974 -xray-log-level none -health-url <vpnc_net_via_health_url>`,
  `StandardInput=file:/etc/vpnc-net/service_links.txt` (Ansible writes it normalized:
  one link per line, no blanks, because `-stdin-links` stops at the first empty line).
  Health URL = `http://<server vpnc ip>:9090/-/healthy` (hosted by Prometheus itself),
  fetched directly, i.e. through vpnc-net and therefore through the current link (UDP
  path included). 15 s interval, 5 s timeout, 3 fails in a row -> next link round robin,
  then `direct` (plain UDP to the server), same port. If Prometheus is down, the probe
  keeps rotating (harmless for metrics, SSH over the tunnel blinks). Journal shows JSON
  events with link names only. No shell script (an older `via.sh` is removed).
- The server sees the VPN server's address as the endpoint; WG roaming handles switches.
- `service_links.txt` is independent of the checked links: `probe_link_files` (all files
  in `files/probes/<host>/`, joined into `links.txt`) rejects `service_links.txt`.
- Removing the file removes the via unit/files and reverts to direct UDP.
- Links must allow UDP on the VPN server (x-ui sometimes disables it); the health URL
  goes through the tunnel, so it catches links that are up but carry no UDP.

## 7. Ansible

Requirements on the controller: Ansible, Docker with buildx, Python 3, full repo checkout,
`ansible-galaxy collection install -r requirements.yml -p ./collections` (community.docker).

| command | does |
|---|---|
| `ansible-playbook monitoring.yml` | vpnc-net server, Prometheus, Grafana, CoreDNS, Caddy |
| `ansible-playbook probes.yml [--limit p]` | vpnc-net client (+via), SSH migration, Docker, image, probe stack |
| `ansible-playbook vpnc-net-join.yml -e name=<n> -e ssh_user=<u> [-e arch=arm64]` | new probe: address, keys, inventory.d, server update, join file |
| `ansible-playbook vpnc-net-join.yml -e name=<n> -e group=vpnc_net_peers` | laptop config |
| `ansible-playbook vpnc-net-join.yml -e name=<monitoring host>` | server keys only |
| `ansible-playbook probes-clean.yml --limit p [-e vpnc_net_remove=true]` | remove stack/images (and tunnel) |
| `ansible-playbook vpnc-net-remove.yml -e name=<n>` | forget member, update server |

`--check --diff` works everywhere (tasks that cannot run in check mode are guarded).
No Makefile in `ansible/` (removed on purpose; use plain ansible-playbook).

Order inside `probes.yml`: assert links exist -> `tasks/vpnc-net.yml` -> `tasks/vpnc-net-ssh.yml`
-> `tasks/docker.yml` -> hash sources (`git ls-files -co` of xray-tun-pwc + xray-tun-connect,
sha256[:12]) -> build/export/copy/load image if the tag is missing -> links.txt, alloy
config, relay Caddyfile, compose -> `docker compose up` -> remove other `xray-tun-pwc:*` tags.

`tasks/vpnc-net.yml`: assert keys/vault -> subnet check
-> build awg locally (docker, once per version/arch, throttle 1) -> copy binaries -> via
block or via cleanup -> config/firewall/unit templates -> server block (sysctl, docker
drop-in, CoreDNS files) -> ensure running -> flush handlers.

Variable precedence trap: `group_vars/all.yml` beats inventory `all:vars`, so values meant
to be set in inventory are referenced with `| default()` (e.g. `vpnc_net_ssh_key`).

Ansible silently skips a missing `vault.yml` in `vars_files`; every playbook asserts the
keys it needs. `vault.yml` is plain text (not ansible-vault).

## 8. Secrets and git hygiene

Git-ignored: `inventory.yml`, `inventory.d/*` (except `.gitkeep`), `vault.yml`,
`host_vars/`, `files/probes/`, `files/vpnc-net/`, `collections/`, `.vault_pass`, `.caddypass`.
Never print: share links, private keys, vault values, join passphrases. Templates holding
them use `no_log: true`. The repo is public: no real hostnames, IPs or links in tracked files
(examples use 198.51.100.0/24 and example.com).

## 9. Conventions

- English in code and docs; plain, simple wording; comments explain why.
- Python: hexagonal layers, Protocol ports, fakes in tests, tests beside code.
- Go: stdlib flags, table tests, `gofmt`, `go vet`.
- Ansible: FQCN modules, task names in sentence case, handlers in `handlers/`, idempotent
  (a second run reports `changed=0`).
- Pinned image versions everywhere (no `latest`).

## 10. Known pitfalls

- RU DPI: UDP to some foreign hosting is cut; TCP flows stall after ~16 KB. Hence heavy
  pages, the per-request relay, and service links.
- Some home routers put the EDNS OPT record before the answer; Go resolvers fail ->
  `probe_dns: [1.1.1.1, 8.8.8.8]` for that probe.
- Jinja `include` drops the trailing newline: `obfuscation.j2` ends with an empty line on
  purpose.
- The monitoring stack must restart after the tunnel interface is recreated (bound IP).
- Docker Compose package: `docker-compose-v2` on Ubuntu, `docker-compose` on Debian 13.
- Probe-down alert: `absent_over_time(up{job="xray-tun-pwc",host="<p>"}[10m])`, "No data" = OK.
