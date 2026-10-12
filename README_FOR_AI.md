# VpnChecker: reference for AI agents

Dense, complete map of the repository. Human docs: `README.md`, `xray-tun-pwc/README.md`,
`xray-tun-connect/README.md`, `xray-pwc-aio/README.md`, `ansible/README.md`. When code and
this file disagree, the code wins; fix this file.

## 1. Purpose

Monitors Xray VPN share links (vless/vmess/trojan/ss) *from inside target networks*
(e.g. a home ISP in a Russian city). "Probes" open real websites through each link in
headless Chromium every few minutes and report how far they got. Results flow to one
central server (Prometheus + Grafana). A probe is one Docker container, `xray-pwc-aio`,
on any Docker host (Linux or macOS). Probes need no public IP: they dial out over a
private WireGuard network, **vpnc-net**; SSH to them goes through the central server
(bastion).

Versions: checker 0.2.0 (`xray-tun-pwc/pyproject.toml`), xray-tun-connect 0.5.0
(`xray-tun-connect/cmd/xray-tun-connect/VERSION`), image 1.0.0 (`xray-pwc-aio/VERSION`).
Semver: new feature -> minor, fix -> patch; bump with every change. Few probes in production.

## 2. Repository layout

```
xray-tun-connect/   Go. share links -> local ports: all as SOCKS5, or one forward port. Embeds xray-core + libXray.
  cmd/xray-tun-connect/{main.go,VERSION}
  internal/tun/{tun.go,config.go,forward.go,udpcheck.go,links.go,log.go,*_test.go}
  internal/logfmt/   the shared log line format
xray-tun-pwc/       Python 3.14, uv. The checker. Its Dockerfile is for local tries only.
  src/xray_tun_pwc/{domain,app,ports,entrypoints}/, log.py, container.py, fakes.py; tests next to code
xray-pwc-aio/       The probe container. Dockerfile (targets: base image, release, alloy, caddy), VERSION.
  aio-log/          Go: prefixes time + service, appends to /app/logs/aio.log
  rootfs/           s6 services (etc/s6-overlay/s6-rc.d), scripts (usr/local/lib/aio), crontab
ansible/            Deployment. Run every command from this directory (ansible.cfg sets inventory).
  monitoring.yml probes.yml probes-host.yml probes-clean.yml vpnc-net-join.yml vpnc-net-remove.yml
  tasks/{docker,vpnc-net,aio-build,aio-package}.yml   handlers/vpnc-net.yml
  group_vars/{all,monitoring,probes}.yml   templates/{monitoring,probe,vpnc-net}/
```

## 3. Runtime topology

```
probe container:  pwc -> xray-tun-connect (SOCKS 127.0.0.1:10000+i) -> VPN link -> websites
                  pwc :8000 -> alloy (scrape 30s) -> relay 127.0.0.1:8080 -> wg vpnc-net
                  [wg endpoint 127.0.0.1:51974 -> xtc-forward -> service link] -> server:51973/udp
server:           prometheus (remote-write receiver) <- grafana <- caddy (public HTTPS)
control plane:    ssh -J root@<server> root@<probe>.vpnc-net.internal   (not a vpnc-net member)
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
taken by 3x-ui); `wg-quick@vpnc-net` (`/etc/wireguard/vpnc-net.conf`, kernel WireGuard,
UDP 51973; PostUp: `iptables -I FORWARD -i %i -o %i -j DROP` and `resolvectl` routing
`~vpnc-net.internal` to CoreDNS for the host itself); docker drop-in
`docker.service.d/vpnc-net.conf` (Wants/After `wg-quick@vpnc-net`, containers bind the
vpnc address). `ip_forward` stays as Docker sets it; peer-to-peer is dropped by the rule.

### Probe: the `xray-pwc-aio` container

On the host: `~/xray-pwc-aio/{compose.yml,state/,logs/}` + named volume `xray-pwc-aio_app`.
`compose.yml` (template `templates/probe/compose.yml.j2`): image
`ghcr.io/nikitades/xray-pwc-aio:<VERSION>`, `hostname: <probe>`, `cap_add: NET_ADMIN`,
`/dev/net/tun`, `shm_size: 512m`, `dns:` from `probe_dns`, env `VPNC_JOIN` (token),
mounts `app:/app`, `./state:/app/state`, `./logs:/app/logs`, host `/proc`,`/sys`,`/` ro.

Image (`xray-pwc-aio/Dockerfile`, default target `base`): `ghcr.io/linuxserver/baseimage-ubuntu:noble-e8a527a2-ls87`
(s6-overlay; its init chowns `/app` to `abc` on every start, bootstrap chowns it back),
uv + Python 3.14.8 at `/opt/python` (`/usr/local/bin/python3`), wireguard-tools, iproute2,
iptables, openssh-server, cron, logrotate, zstd; Playwright's Chromium headless shell for
the lock file's Playwright in `/ms-playwright` (version in `/etc/aio-playwright-version`);
`wireguard-go` 0.0.20250522, `aio-log`, fallback `xray-tun-connect` in `/usr/local/bin`;
`/etc/aio-version`. `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`: a failed bootstrap stops the container.

`/app` layout: `releases/<hash>/{venv,bin/{xray-tun-connect,alloy->,caddy->},browsers-path}`,
`current -> releases/<hash>`, `opt/{alloy-<v>,caddy-<v>,ms-playwright}`, `state/`, `logs/`.

`/app/state` (host bind mount; written by the token once, then by Ansible):
`aio.env` (AIO_NODE, AIO_IP, AIO_SERVER_IP, AIO_FORWARD, AIO_VIA_PORT, AIO_RELAY),
`wg/vpnc-net.conf`, `service_links.txt`, `links.txt` (0640 root:abc), `pwc.env`,
`ssh/{authorized_keys,ssh_host_ed25519_key}`, `alloy/{config.alloy,data/}`,
`relay/Caddyfile`, `logrotate.status`.

s6 services (`rootfs/etc/s6-overlay/s6-rc.d`, scripts in `rootfs/usr/local/lib/aio/`), each
run as `aio-run <svc> <script>` (output -> `aio-log <svc>`), `finish` logs unexpected exits as `s6`:

| service | does |
|---|---|
| `init-aio` (oneshot, `with-contenv`) | `bootstrap.py`: checks CapEff NET_ADMIN and `/dev/net/tun`; chowns `/app` root; empty state + `VPNC_JOIN` -> writes state (fails on hostname != node); state present -> ignores token; creates SSH host key |
| `svc-wg` | `wg-quick up state/wg/vpnc-net.conf` (`WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go` fallback); logs `up` (impl kernel/wireguard-go), `handshake_ok`/`handshake_lost` (stale > 180 s), every 30 s |
| `svc-xtc-forward` | links in `service_links.txt` -> `xray-tun-connect -stdin-links -forward $AIO_FORWARD -listen 127.0.0.1 -start-port $AIO_VIA_PORT -xray-log-level none < file`; else idle |
| `svc-pwc` | `s6-setuidgid abc current/venv/bin/xray-tun-pwc`, `PWC_CONFIG_PATH=state/links.txt`, `PWC_INSTANCE=$AIO_NODE`, `PLAYWRIGHT_BROWSERS_PATH` from `current/browsers-path` |
| `svc-alloy` | `current/bin/alloy run state/alloy/config.alloy --storage.path=state/alloy/data --server.http.listen-addr=127.0.0.1:12345` |
| `svc-relay` | `AIO_RELAY=true` -> `caddy run --config state/relay/Caddyfile` (`http://127.0.0.1:8080`, h1, keepalive off); else idle |
| `svc-sshd` | `sshd -D -e -f usr/local/lib/aio/sshd_config -o AllowUsers=root@$AIO_SERVER_IP` (key only) |
| cron (linuxserver `svc-cron`) | `/etc/crontabs/root`: nightly logrotate, `aio.log` weekly, rotate 4, compress, delaycompress, copytruncate |

A service with nothing to do logs `INFO idle reason=...` and sleeps (300 s or a day).

## 4. Log format (all services)

`<ISO8601 UTC ms> <service> <LEVEL> <event> <key=value ...>`; time + service added by
`aio-log`; programs write `LEVEL event k=v` (logfmt; values with space/=/" quoted). Levels
DEBUG INFO WARN ERROR. Third-party lines (alloy logfmt `level=warn`, caddy JSON, sshd) keep
their format after the prefix; alloy at `warn`, caddy at `WARN`. pwc relays its child
xtc's lines as `LEVEL xtc.<event> ...`, unknown lines as `WARN xtc.output msg=...`.
Services: entrypoint, wg, xtc-forward, pwc, alloy, relay, sshd, s6, deploy (Ansible), logrotate.
Links only by name. Implementations: `xray-tun-connect/internal/logfmt`, `xray-tun-pwc/src/xray_tun_pwc/log.py`.

## 5. xray-tun-pwc (checker)

Per link, every `PWC_CHECK_INTERVAL` (PT5M) + random `PWC_CHECK_JITTER` (PT2M), links
sequentially, one Chromium at a time:

1. xray-tun-connect starts SOCKS tunnels (links via stdin, not argv; `-xray-log-level error`).
2. Open `PWC_HEAVY_VISITS` (3) random URLs from `PWC_HEAVY_SITES`; success = `DOMContentLoaded`.
3. Open all `PWC_TARGET_SITES` (Instagram, Facebook); success = HTTP 200.
4. Close tunnel + browser.

Each page: 3 attempts, 30 s navigation timeout, backoff 2 s, 4 s.

Stage (`pwc_tunnel_stage`): 0 link broken, 1 unreachable (no proof traffic passed),
2 partial, 3 all OK. Proof of passage: page opened, any HTTP status, TLS handshake
failure. Not proof: `ERR_PROXY_CONNECTION_FAILED`, timeout, reset, DNS error.
Log per link: `check index name result(ok|partial|unreachable|link_broken) stage [failed="..."]`,
INFO / WARN / ERROR; `start version instance links interval jitter`; `xtc_unavailable` ERROR exits.

Metrics on :8000: `pwc_tunnel_stage{instance,tunnel,stable_id}`;
`pwc_tunnel_error{...,error_type}` = 1 if last check hit it, 0 if only earlier.
`instance` = `PWC_INSTANCE` = inventory hostname; `tunnel` = text after `#`;
`stable_id` = hash of link without name. Alloy adds `job="xray-tun-pwc"`, `host`; node
metrics get `job="node"`, `instance=<probe>`.

Env: `PWC_CONFIG_PATH`, `PWC_INSTANCE`, `PWC_HEAVY_SITES`, `PWC_HEAVY_VISITS`,
`PWC_TARGET_SITES`, `PWC_CHECK_INTERVAL`, `PWC_CHECK_JITTER`, `PWC_HTTP_PORT`,
`PWC_XRAY_TUN_CONNECT_BIN` (else `$GOBIN`, `$GOPATH/bin`, `PATH`; minimum 0.5.0 checked).

Architecture: hexagonal. `domain/` entities, sites, schedule; `app/` use cases + Protocol
ports; `ports/` adapters (Playwright browser, Prometheus metrics, xray-tun-connect
process, stderr relayed through `log.relayed`); `entrypoints/cli.py`; `container.py`;
`fakes.py`. Dev: `uv run pytest`, `uv run ruff check .`, `uvx ty check src`.

## 6. xray-tun-connect

- **All at once** (default): link i = SOCKS5 (UDP on) on `start-port+i` (or `Options.Ports`).
  stdout: one JSON report `{version,listen,tunnels:[{index,port,name,protocol,ok,error}]}`
  (the protocol pwc reads); bad links keep index/port with `ok:false`; config rebuilt
  dropping the outbound xray rejects, one per round. Logs `start`, `link_rejected`.
- **Forward** (`-forward host:port`): one port `start-port`, inbound `tunnel` (TCP+UDP) to
  the target through one link at a time. No stdout. A checker xray instance runs every link
  as SOCKS on free ports; every `-check-interval` (15s) all links at once get a DNS
  question (root NS) over SOCKS5 UDP ASSOCIATE to 1.1.1.1:53 and 8.8.8.8:53, one valid
  answer = pass, timeout `-check-timeout` 5s (`internal/tun/udpcheck.go`). States
  unknown/usable/unusable: pass -> usable (`link_usable`); `-check-fails` (3) in a row ->
  unusable (`link_unusable`). The port keeps its link while usable (no failback); else the
  first usable in given order (priority); else `DirectLink` (`direct://#direct`, freedom,
  never checked, last resort, left as soon as a link is usable: `switch reason=link_recovered`)
  unless `-disable-direct`; else port closed and `no_tunnels` ERROR every minute. A link
  that fails to start on the port: `link_failed`, marked unusable. First serve: `active`;
  later: `switch from to reason=unusable`. Code: `internal/tun/forward.go`.
- Flags: `-link`, `-links`, `-stdin-links` (until first empty line), `-start-port` 10000,
  `-listen` 127.0.0.1, `-forward`, `-check-interval`, `-check-timeout`, `-check-fails`,
  `-disable-direct`, `-xray-log-level` warning (xray's own lines, raw, stderr), `-ready-timeout` 10s, `-version`.
- Lives until SIGINT/SIGTERM, or EOF on stdin **when stdin is a pipe**; for a long forward
  give the links as a regular file on stdin. Under journald use `-xray-log-level none`.
- Exit: 0 clean, 1 core failed / no usable link (default mode), 2 usage.
- Tests (`go test -race ./...`): parsing, config build, stable ports, SOCKS traffic,
  forward TCP+UDP echo via DirectLink, first usable, no failback, move to first usable,
  leave direct, no_tunnels repeat + closed port, rejected links, real DNS check through a
  SOCKS UDP association against a fake resolver, logfmt, CLI.
- Build: Go >= 1.27.1. `make build|install|dist|test`. VERSION embedded.

## 7. vpnc-net (WireGuard)

### 7.1 Parameters (`group_vars/all.yml`)

| var | value |
|---|---|
| `vpnc_net_interface` | `vpnc-net` |
| `vpnc_net_subnet` | `10.213.97.0/24` (server .1, probes: first free from .10) |
| `vpnc_net_port` | UDP 51973 |
| `vpnc_net_mtu` / `keepalive` | 1280 / 25 |
| `vpnc_net_domain` | `vpnc-net.internal` |
| `vpnc_net_server` | `groups.monitoring[0]` |
| `vpnc_net_endpoint` | `caddy_grafana_domain:vpnc_net_port` |
| `vpnc_net_members` | hosts with `vpnc_net_ip` defined |
| `vpnc_net_bastion` | `<server ansible_user>@<server ansible_host>` |
| `vpnc_net_ssh_args` | `-o ProxyJump=<bastion> -o StrictHostKeyChecking=accept-new` |
| `vpnc_net_ssh_public_key` | `vpnc_net_ssh_key` from inventory, default `~/.ssh/id_ed25519.pub` |
| `vpnc_net_via_lines/links/via` | normalized `files/probes/<host>/service_links.txt`; via = has links |
| `vpnc_net_via_port` | 51974 |
| `vpnc_net_join_dir` | `ansible/files/vpnc-net/join/` |

### 7.2 Design rules

- Hub and spoke. Probes `AllowedIPs = subnet` only, inside the container's netns: the host
  gets nothing. Peers reach only the server (FORWARD DROP on the server). The control plane
  is not a member: `ssh -J` through the server (sshd on the server opens the TCP itself).
- Probe sshd accepts only `root@<server vpnc ip>`, key `vpnc_net_ssh_key`.
- Server reload = `systemctl reload wg-quick@vpnc-net` (`wg syncconf`): adding peers never
  drops others. Probe: `wg syncconf` on config change; new Address/MTU needs `s6-svc -r svc-wg`.
- Service links (via): wg `Endpoint = 127.0.0.1:51974`, `svc-xtc-forward` carries it (section 6).
  The server sees the VPN server's address; WG roaming handles switches. Empty or no file
  = direct UDP. Links must allow UDP on the VPN server (x-ui may disable it).
- The checker keeps the probe's real DNS; Alloy and the relay use the server IP directly.

### 7.3 Members and files (all git-ignored)

- `host_vars/<name>/vpnc-net.yml`: `vpnc_net_ip`, `vpnc_net_private_key`, `vpnc_net_public_key`.
  Written by `vpnc-net-join.yml`; rerun keeps address and keys. Keys: openssl X25519, raw 32 bytes base64.
- `inventory.d/<name>.yml`: new probes (group `probes`, no connection vars: those are in
  `group_vars/probes.yml`). Inventory sources: `inventory.yml,inventory.d`.
- `files/probes/<name>/{links.txt,service_links.txt}`: created empty by join (`force: false`).
- `files/vpnc-net/join/<name>/{compose.yml,next-steps.txt}`: compose with the token.
- Token (`vpnc_net_join_token` in `group_vars/probes.yml`): base64url (no padding) of JSON
  `{v:1,node,aio_env,wg_conf,service_links,authorized_keys}`, rendered from the same
  templates as `probes.yml` (`templates/probe/{aio.env,wg.conf}.j2`), so the first deploy
  changes nothing there.
- `files/aio/`: package cache `release-<hash>-<arch>.tar.zst`, `alloy-<v>-<arch>`, `caddy-<v>-<arch>`.

## 8. Ansible

Requirements on the controller: Ansible, Docker with buildx, zstd, Python 3, full repo
checkout, `ansible-galaxy collection install -r requirements.yml -p ./collections` (community.docker).

| command | does |
|---|---|
| `ansible-playbook monitoring.yml` | vpnc-net server, Prometheus, Grafana, CoreDNS, Caddy |
| `ansible-playbook vpnc-net-join.yml -e name=<n>` | probe: address, keys, inventory.d, empty link files, server update, compose.yml; server name: keys only |
| `ansible-playbook probes-host.yml [--limit p]` | on the host (`probe_host[_user,_port]`): template compose.yml into `~/xray-pwc-aio`, `docker compose up -d --pull missing`, remove other image tags |
| `ansible-playbook probes.yml [--limit p]` | in the container: build + deliver release/alloy/caddy, state files, switch release, prune |
| `ansible-playbook probes-clean.yml --limit p` | on the host: `docker compose down -v --rmi all`, remove the folder |
| `ansible-playbook vpnc-net-remove.yml -e name=<n>` | forget member (host_vars, inventory.d, join dir), update server; links stay |

`--check --diff` works everywhere. No Makefile in `ansible/` (use plain ansible-playbook).

`probes.yml` (connection: `group_vars/probes.yml`: `ansible_host=vpnc_net_ip`, `root`,
key = `vpnc_net_ssh_key` without `.pub`, ProxyJump, python `/usr/local/bin/python3`; no
vault): assert links -> wait_for_connection -> setup min -> arch -> warn if `/etc/aio-version`
!= `xray-pwc-aio/VERSION` -> release hash (`git ls-files -co` of xray-tun-pwc, xray-tun-connect,
xray-pwc-aio/Dockerfile, sha256[:12]) -> build packages locally once per arch
(`tasks/aio-build.yml`: `docker buildx --target release|alloy|caddy --output type=tar | zstd`,
`creates:`) -> deliver missing ones (`tasks/aio-package.yml`: copy, untar to `.new`, mv) ->
link alloy/caddy into the release -> release Playwright != image's -> install Chromium to
`/app/opt/ms-playwright`; write `browsers-path` -> state files -> switch `current` (log
`deploy release_switched`) -> prune releases beyond `aio_keep_releases` (2) and unused tools.
Handlers: `s6-svc -r /run/service/svc-<x>`; Sync vpnc-net = `wg syncconf`; both tunnel
handlers then `wait_for_connection`.

`tasks/vpnc-net.yml` (server only): assert keys -> apt wireguard-tools -> template
`/etc/wireguard/vpnc-net.conf` (notify reload) -> ensure `wg-quick@vpnc-net` -> docker
drop-in -> CoreDNS files -> flush handlers.

Variable precedence trap: `group_vars/all.yml` beats inventory `all:vars`, so values meant
to be set in inventory are referenced with `| default()` (e.g. `vpnc_net_ssh_key`). Host
vars in the inventory beat `group_vars/probes.yml`: do not set `ansible_host` for probes
there (use `probe_host` for the host).

Ansible silently skips a missing `vault.yml` in `vars_files`; `monitoring.yml` asserts the
keys it needs. `vault.yml` is plain text (not ansible-vault), holds only the Grafana password.

## 9. Secrets and git hygiene

Git-ignored: `inventory.yml`, `inventory.d/*` (except `.gitkeep`), `vault.yml`,
`host_vars/`, `files/probes/`, `files/vpnc-net/`, `files/aio/`, `collections/`, `.vault_pass`, `.caddypass`.
Never print: share links, private keys, vault values, join tokens (compose.yml). Tasks holding
them use `no_log: true`. The repo is public: no real hostnames, IPs or links in tracked files
(examples use 198.51.100.0/24 and example.com). The image is public and has no secrets.

## 10. Conventions

- English in code and docs; plain, simple wording; comments explain why.
- Python: hexagonal layers, Protocol ports, fakes in tests, tests beside code.
- Go: stdlib flags, table tests, `gofmt`, `go vet`.
- Ansible: FQCN modules, task names in sentence case, handlers in `handlers/` or the play,
  idempotent (a second run reports `changed=0`).
- Pinned image versions everywhere, at least two weeks old (no `latest`).

## 11. Known pitfalls

- RU DPI: UDP to some foreign hosting is cut; TCP flows stall after ~16 KB. Hence heavy
  pages, the per-request relay, and service links.
- Some home routers put the EDNS OPT record before the answer; Go resolvers fail ->
  `probe_dns: [1.1.1.1, 8.8.8.8]` for that probe (in compose.yml: needs `probes-host.yml`).
- Caddy treats an IP site address as HTTPS even with `auto_https off`: the relay site is
  written `http://127.0.0.1:8080`.
- linuxserver.io init chowns `/app` to abc at every start; sshd StrictModes then refuses
  keys: bootstrap chowns `/app` back to root.
- s6 oneshots do not see the container env: `init-aio` runs under `with-contenv`.
- A new container on an old vpnc-net address has a new SSH host key: remove the old
  `known_hosts` entry (`ssh-keygen -R <ip>`).
- Docker Desktop (macOS) bind mounts do not keep a chown: `links.txt` shows changed each run.
- The monitoring stack must restart after the server's tunnel interface is recreated (bound IP).
- Docker Compose package: `docker-compose-v2` on Ubuntu, `docker-compose` on Debian 13.
- Probe-down alert: `absent_over_time(up{job="xray-tun-pwc",host="<p>"}[10m])`, "No data" = OK.
