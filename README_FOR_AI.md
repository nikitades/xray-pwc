# xray-pwc: reference for AI agents

Dense, complete map of the repository. Human docs: `README.md`, `xray-pwc-probe/README.md`,
`xray-pwc-connect/README.md`, `xray-pwc-aio/README.md`, `ansible/README.md`. When code and
this file disagree, the code wins; fix this file.

## 1. Purpose

Monitors Xray VPN share links (vless/vmess/trojan/ss) *from inside target networks*
(e.g. a home ISP in a Russian city). "Probes" open real websites through each link in
headless Chromium every few minutes and report how far they got. Results flow to one
central server (Prometheus + Grafana). A probe is one Docker container, `xray-pwc-aio`,
on any Docker host (Linux, macOS, Windows). Probes need no public IP: they dial out over a
private WireGuard network, **xray-pwc-net** (optionally carried through Xray links); SSH
to them goes through the central server (bastion). Nothing touches a probe's host after
its owner's `docker compose up -d`. MIT license.

## 2. Versions

One version, root `VERSION` (1.1.0), for xray-pwc-probe, xray-pwc-connect, the bundle and
the image. `xray-pwc-probe/pyproject.toml` repeats it (`make check-version`). Semver: new
feature -> minor, fix -> patch; bump with every release.

- Compatibility: probe and connect must share X.Y.Z (`domain/release.py` `Release.of`, any
  suffix ignored); the image must be >= the bundle's `MIN_IMAGE` (`xray-pwc-aio/deps.env`).
- Release build (CI on tag `vX.Y.Z`, `RELEASE=1`): VERSION as is. Any other build: dev,
  `VERSION-dev.<hash>` (Python: `VERSION.dev0+<hash>`), hash = sha256[:12] of names and
  contents of every non-ignored file under VERSION, xray-pwc-probe, xray-pwc-connect,
  xray-pwc-aio. Same sources -> same version -> not rebuilt or resent.
- Go version: `-ldflags -X main.version=...`; else `debug.ReadBuildInfo().Main.Version`
  (set by `go install ...@vX.Y.Z`); else `0.0.0-dev`.

## 3. Repository layout

```
VERSION LICENSE Makefile   one version; MIT; every build (local and CI)
.github/workflows/         ci.yml (tests + dev builds per arch), release.yml (tag -> everything)
xray-pwc-connect/   Go module github.com/nikitades/xray-pwc/xray-pwc-connect (tags
                    xray-pwc-connect/vX.Y.Z). share links -> local ports: SOCKS5 each, or one forward port.
  cmd/xray-pwc-connect/main.go   internal/tun/  internal/logfmt/
xray-pwc-probe/     Python 3.14, uv, wheel xray_pwc_probe. The checker. Its Dockerfile is for local tries.
  src/xray_pwc_probe/{domain,app,ports,entrypoints}/, log.py, container.py, fakes.py; tests next to code
xray-pwc-aio/       The probe image and the bundle. Dockerfile (targets image, bundle, alloy, caddy, chromium), deps.env.
  aio-log/          Go: prefixes time + service, appends to /app/logs/aio.log and stdout
  aio-net/          Go (vishvananda/netlink): interface add/del, address, MTU, up, route; cap_net_admin
  rootfs/           image side: s6 definitions, aio-run, finish, bootstrap.py, logrotate, crontab
  services/         release side: the service scripts (common.sh, wg, connect-forward, probe, alloy, relay, sshd, sshd_config)
ansible/            Deployment. Run every command from this directory (ansible.cfg sets inventory).
  monitoring.yml probes.yml xray-pwc-net-join.yml xray-pwc-net-remove.yml migrate-1.1.yml (one-time)
  tasks/{docker,xray-pwc-net,release-fetch,aio-package,outbound-policy}.yml   handlers/xray-pwc-net.yml
  group_vars/{all,monitoring,probes}.yml   templates/{monitoring,probe,xray-pwc-net}/
dist/               (git-ignored) build output: dist/<version>/, dist/tools/
```

## 4. Builds (root Makefile; macOS make 3.81 compatible)

| target | output |
|---|---|
| `make version` | the version these sources build as |
| `make connect` | `dist/<v>/xray-pwc-connect-{linux,darwin}-{amd64,arm64}` (CGO off, -trimpath) |
| `make wheel` | `dist/<v>/xray_pwc_probe-<pyv>-py3-none-any.whl` + `requirements.txt` (`uv export --frozen --no-dev --no-emit-project`, hashed). Built from a copy with `uv version --frozen <pyv>` |
| `make bundle [ARCHES="amd64 arm64"]` | `dist/<v>/xray-pwc-<v>-linux-<arch>.tar.zst`; prints `bundle: <path>` |
| `make image [ARCH=] [PLATFORMS=] [IMAGE_OUTPUT=] [IMAGE_TAGS=] [IMAGE_FLAGS=]` | the image; default `type=docker`, tag `<IMAGE>:<v>` |
| `make tool TOOL=alloy\|caddy\|chromium TOOL_VERSION= ARCH=` | `dist/tools/<tool>-<version>-linux-<arch>.tar.zst`; prints `tool: <path>` |
| `make sums` | `dist/<v>/SHA256SUMS` |
| `make test` | check-version, go vet/test (connect -race, aio-log, aio-net GOOS=linux vet), pytest, ruff, ruff format, ty |

Bundle (Dockerfile `bundle-build`, named context `dist=dist/<v>`): at
`/app/releases/<v>`: `venv/` (`uv venv`, `uv pip install --require-hashes -r requirements.txt`,
`uv pip install --no-deps <wheel>`, bytecode compiled), `bin/xray-pwc-connect`, `services/`,
`release.env` (VERSION, MIN_IMAGE, ALLOY_VERSION, CADDY_VERSION, PLAYWRIGHT_VERSION from the venv).

CI release (`release.yml`): `files` (check tag == VERSION, make test, make connect wheel
RELEASE=1, artifact) -> `build` matrix amd64 `ubuntu-24.04` / arm64 `ubuntu-24.04-arm`
(make bundle; make image push-by-digest) -> `publish` (imagetools create X.Y.Z, X.Y, latest;
make sums; `gh release create vX.Y.Z` with bundles, connect binaries, wheel, SHA256SUMS; push
tag `xray-pwc-connect/vX.Y.Z`). `ci.yml`: make test; per arch make bundle and image (cacheonly).

## 5. Runtime topology

```
probe container:  probe -> xray-pwc-connect (SOCKS 127.0.0.1:10000+i) -> VPN link -> websites
                  probe :8000 -> alloy (scrape 30s) [-> relay 127.0.0.1:8080] -> wg xray-pwc-net
                  [wg endpoint 127.0.0.1:51974 -> connect-forward -> service link] -> server:51973/udp
                  [] by outbound_access_policy: relay -> the relay; vpn -> connect-forward
server:           prometheus (remote-write receiver) <- grafana <- caddy (public HTTPS)
control plane:    ssh -J root@<server> -p 2222 abc@<probe>.xray-pwc-net.internal   (not a member)
```

### Central server (`monitoring` group, first host = xray-pwc-net server)

Compose project `vpn-monitoring` (name kept: its volumes hold the data), dir `/opt/vpn-monitoring`:

| service | image | binds | notes |
|---|---|---|---|
| prometheus | prom/prometheus:v3.15.0 | 127.0.0.1:9090, <net ip>:9090 | `--web.enable-remote-write-receiver`, retention 180d / 40GB, out_of_order window `metrics_backlog` (8h, = Alloy WAL keepalive), **no auth** |
| grafana | grafana/grafana:13.2.2 | 127.0.0.1:3000 | admin password from vault; data source URL `http://prometheus:9090` (added by hand) |
| coredns | coredns/coredns:1.12.1 | <net ip>:53 udp+tcp | zone `xray-pwc-net.internal` from `coredns/xray-pwc-net.hosts` (`<inventory name>.xray-pwc-net.internal`, `prom.xray-pwc-net.internal`); `.` forwarded to 1.1.1.1, 8.8.8.8; auto reload |

Host (not Compose): Caddy system package (only public web; Grafana at
`caddy_grafana_domain:caddy_https_port`, ACME HTTP-01 on :80, so 8443 works when 443 is
taken by 3x-ui); `wg-quick@xray-pwc-net` (`/etc/wireguard/xray-pwc-net.conf`, kernel
WireGuard, UDP 51973; PostUp: `iptables -I FORWARD -i %i -o %i -j DROP` and `resolvectl`
routing `~xray-pwc-net.internal` to CoreDNS for the host itself); docker drop-in
`docker.service.d/xray-pwc-net.conf` (Wants/After the tunnel; containers bind its address).

### Probe: the `xray-pwc-aio` container

On the host: `compose.yml` + named volume `xray-pwc-aio_app` (= `/app`), nothing else.
`compose.yml` (`templates/probe/compose.yml.j2`): image `aio_image` =
`<aio_image_name | default ghcr.io/nikitades/xray-pwc-aio>:<aio_image_version>`
(`xray_pwc_release`, or VERSION when it is `local`), `hostname: <probe>`, `cap_add:
NET_ADMIN`, `/dev/net/tun`, `shm_size: 512m`, `dns:` from `probe_dns`, env `PWC_JOIN`
(token), mounts `app:/app`, host `/proc`,`/sys`,`/` ro (for Alloy). Same file on every OS.

Image (target `image`): `ghcr.io/linuxserver/baseimage-ubuntu:noble-e8a527a2-ls87` (s6-overlay;
user `abc` uid 911, gid set to 911 at build, shell bash, home /config), uv + Python 3.14.8 at
`/opt/python` (`/usr/local/bin/python3`), wireguard-tools, iproute2, libcap2-bin,
openssh-server, cron, logrotate, zstd; Chromium headless shell + system libs for the bundle's
Playwright in `/ms-playwright` (`/etc/aio-playwright-version`); `/opt/alloy-<v>/alloy`,
`/opt/caddy-<v>/caddy`; `wireguard-go` 0.0.20250522, `aio-log`, `aio-net` in `/usr/local/bin`;
`setcap cap_net_admin+ep` on `wg`, `wireguard-go`, `aio-net` (not `ip`: iproute2 drops caps
for non-root); the bundle of its version at `/app/releases/<v>`, `/app/current` -> it, all
owned 911; `/etc/aio-version`. `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`.

`/app`: `releases/<v>/{venv,bin/xray-pwc-connect,services,release.env}`, `current ->
releases/<v>`, `opt/{alloy-<v>,caddy-<v>,ms-playwright-<pw>}` (tools a release needs and
the image lacks), `state/`, `logs/aio.log*`. All owned by abc.

`/app/state` (token once, then Ansible): `aio.env` (AIO_NODE, AIO_IP, AIO_SERVER_IP,
AIO_SUBNET, AIO_MTU, AIO_FORWARD, AIO_VIA_PORT, AIO_RELAY), `wg/xray-pwc-net.conf`
(`wg setconf` format: keys and peer only), `service_links.txt`, `links.txt`, `probe.env`,
`ssh/{authorized_keys,ssh_host_ed25519_key}`, `alloy/{config.alloy,data/}`,
`relay/Caddyfile`, `logrotate.status` (root's).

s6: every `svc-<x>/run` is `aio-run <x>` (as root: `s6-svperms -g abc -O abc` so abc can
read status and `s6-svc` the service; then `s6-setuidgid abc`, output to `aio-log <x>`,
exec `/app/current/services/<x>`; no release -> idle). `finish` (root) logs unexpected exits
as `s6`; for wg it runs `aio-net del xray-pwc-net`.

| service | does (all as abc) |
|---|---|
| `init-aio` (oneshot, `with-contenv`, root) | `bootstrap.py`: touch `logs/aio.log` as abc first; check CapEff NET_ADMIN, `/dev/net/tun`; no `state/aio.env` + `PWC_JOIN` v2 -> write state (fails on hostname != node); state present -> ignore token; SSH host key; chown releases, opt, state, /app to abc; `/run/wireguard` for abc |
| `wg` | `aio-net del`; `aio-net add` (kernel) else `wireguard-go` (impl logged); `wg setconf` (retry 10 s until the endpoint resolves); `aio-net up <iface> AIO_IP/32 AIO_MTU AIO_SUBNET`; logs `up`, `handshake_ok`/`handshake_lost` (stale > 180 s), every 30 s |
| `connect-forward` | links in `service_links.txt` -> `xray-pwc-connect -stdin-links -forward $AIO_FORWARD -listen 127.0.0.1 -start-port $AIO_VIA_PORT -xray-log-level none < file`; else idle a day |
| `probe` | `current/venv/bin/xray-pwc-probe`, `PWC_CONFIG_PATH=state/links.txt`, `PWC_INSTANCE=$AIO_NODE`, `PWC_CONNECT_BIN=current/bin/xray-pwc-connect`, `PLAYWRIGHT_BROWSERS_PATH` = `/ms-playwright` if the image's Playwright = PLAYWRIGHT_VERSION else `/app/opt/ms-playwright-<pw>`, `probe.env` |
| `alloy` | `alloy run state/alloy/config.alloy --storage.path=state/alloy/data --server.http.listen-addr=127.0.0.1:12345`; binary `/app/opt/alloy-<v>` else `/opt/alloy-<v>` |
| `relay` | `AIO_RELAY=true` -> `caddy run --config state/relay/Caddyfile` (`http://127.0.0.1:8080`, h1, keepalive off); else idle |
| `sshd` | `sshd -D -e -f current/services/sshd_config -o AllowUsers=abc@$AIO_SERVER_IP`: port 2222, key only, PidFile none, no root login |
| cron (linuxserver `svc-cron`, root) | nightly logrotate: `aio.log` weekly, rotate 4, compress, delaycompress, copytruncate |

A service with nothing to do logs `INFO idle reason=...` and sleeps (300 s or a day).

## 6. Log format (all services)

`<ISO8601 UTC ms> <service> <LEVEL> <event> <key=value ...>`; time + service added by
`aio-log`; programs write `LEVEL event k=v` (logfmt; values with space/=/" quoted). Levels
DEBUG INFO WARN ERROR. Third-party lines (alloy logfmt `level=warn`, caddy JSON, sshd) keep
their format after the prefix. The probe relays its child xray-pwc-connect's lines as
`LEVEL connect.<event> ...`, unknown lines as `WARN connect.output msg=...`.
Services: entrypoint, wg, connect-forward, probe, alloy, relay, sshd, s6, deploy (Ansible),
logrotate. Links only by name. Implementations: `xray-pwc-connect/internal/logfmt`,
`xray-pwc-probe/src/xray_pwc_probe/log.py`.

## 7. xray-pwc-probe (checker)

One round every `PWC_CHECK_INTERVAL` (PT5M) + random `PWC_CHECK_JITTER` (PT2M):

1. One xray-pwc-connect process for the round: SOCKS tunnels for all links at once
   (links via stdin, not argv; `-xray-log-level error`).
2. Per link, sequentially, a fresh browser (one Chromium at a time): open `PWC_HEAVY_VISITS`
   (3) random URLs from `PWC_HEAVY_SITES`; success = `DOMContentLoaded`.
3. Open all `PWC_TARGET_SITES` (Instagram, Facebook); success = HTTP 200.
4. After the last link: close the tunnels.

Each page: 3 attempts, 30 s navigation timeout, backoff 2 s, 4 s.

Stage (`xray_pwc_tunnel_stage`): 0 link broken, 1 unreachable (no proof traffic passed),
2 partial, 3 all OK. Proof of passage: page opened, any HTTP status, TLS handshake
failure. Not proof: `ERR_PROXY_CONNECTION_FAILED`, timeout, reset, DNS error.
Log per link: `check index name result(ok|partial|unreachable|link_broken) stage [failed="..."]`,
INFO / WARN / ERROR; `start version instance links interval jitter`; `connect_unavailable`
ERROR exits (missing, or another X.Y.Z); `round_failed err` ERROR = `XrayPwcConnectFailed`
in a round (no report, port taken): round skipped, metrics unchanged, loop goes on.

Metrics on :8000: `xray_pwc_tunnel_stage{instance,tunnel,stable_id}`;
`xray_pwc_tunnel_error{...,error_type}` = 1 if last check hit it, 0 if only earlier.
(Before 1.1.0: `pwc_tunnel_*`; old series stay in Prometheus.) `instance` = `PWC_INSTANCE`
= inventory hostname; `tunnel` = text after `#`; `stable_id` = hash of link without name.
Alloy adds `job="xray-pwc-probe"`, `host`; node metrics get `job="node"`, `instance=<probe>`.

Env: `PWC_CONFIG_PATH`, `PWC_INSTANCE`, `PWC_HEAVY_SITES`, `PWC_HEAVY_VISITS`,
`PWC_TARGET_SITES`, `PWC_CHECK_INTERVAL`, `PWC_CHECK_JITTER`, `PWC_HTTP_PORT`,
`PWC_CONNECT_BIN` (else `$GOBIN` if set, otherwise `$GOPATH/bin` (~/go/bin); then `PATH`).
Install: wheel from the GitHub release (`uv tool install <url>` / pip); connect via
`go install ...@vX.Y.Z` or the release's binaries.

Architecture: hexagonal. `domain/` entities, release, sites, schedule; `app/` use cases +
Protocol ports; `ports/` adapters (Playwright browser, Prometheus metrics, xray-pwc-connect
process, stderr relayed through `log.relayed`); `entrypoints/cli.py`; `container.py`;
`fakes.py`. Dev: `uv run pytest`, `uv run ruff check .`, `uvx ty check src`.

## 8. xray-pwc-connect

- **All at once** (default): link i = SOCKS5 (UDP on) on `start-port+i` (or `Options.Ports`).
  stdout: one JSON report `{version,listen,tunnels:[{index,port,name,protocol,ok,error}]}`
  (the protocol the probe reads); bad links keep index/port with `ok:false`; config rebuilt
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
  unless `-disable-direct`; else port closed and `no_tunnels usable=0 direct=off|failed` ERROR
  every minute (`failed`: direct did not start on the port). A link that fails to start on
  the port: `link_failed`, marked unusable. First serve: `active`; later: `switch from to
  reason=unusable`. Code: `internal/tun/forward.go`.
- Flags: `-link`, `-links`, `-stdin-links` (until first empty line), `-start-port` 10000,
  `-listen` 127.0.0.1, `-forward`, `-check-interval`, `-check-timeout`, `-check-fails`,
  `-disable-direct`, `-xray-log-level` warning (xray's own lines, raw, stderr), `-ready-timeout` 10s, `-version`.
- Lives until SIGINT/SIGTERM, or EOF on stdin **when stdin is a pipe**; for a long forward
  give the links as a regular file on stdin. Under journald use `-xray-log-level none`.
- Exit: 0 clean, 1 core failed (`start_failed`, no report) / no usable link (default mode,
  report printed first), 2 usage. `printf ... | xray-pwc-connect -stdin-links` exits right
  after the report (EOF on the pipe).
- Tests (`go test -race ./...`): parsing, config build, stable ports, SOCKS traffic,
  forward TCP+UDP echo via DirectLink, first usable, no failback, move to first usable,
  leave direct, no_tunnels repeat + closed port + direct field, rejected links, real DNS
  check through a SOCKS UDP association against a fake resolver, logfmt, CLI.
- Build: Go >= 1.27.1. `make build|install|test` in the module; cross builds `make connect` at the root.

## 9. xray-pwc-net (WireGuard)

### 9.1 Parameters (`group_vars/all.yml`)

| var | value |
|---|---|
| `pwc_net_interface` | `xray-pwc-net` |
| `pwc_net_subnet` | `10.213.97.0/24` (server .1, probes: first free from .10) |
| `pwc_net_port` | UDP 51973 |
| `pwc_net_mtu` / `keepalive` | 1280 / 25 |
| `pwc_net_domain` | `xray-pwc-net.internal` |
| `pwc_net_server` | `groups.monitoring[0]` |
| `pwc_net_endpoint` | `caddy_grafana_domain:pwc_net_port` |
| `pwc_net_members` | hosts with `pwc_net_ip` defined |
| `pwc_net_bastion` | `<server ansible_user>@<server ansible_host>` |
| `pwc_net_ssh_args` | `-o ProxyJump=<bastion> -o StrictHostKeyChecking=accept-new` |
| `pwc_net_ssh_public_key` | `pwc_net_ssh_key` from inventory, default `~/.ssh/id_ed25519.pub` |
| `pwc_net_via_lines` | normalized `files/probes/<host>/service_links.txt` |
| `pwc_net_via` | not the server and `outbound_access_policy == 'vpn'` |
| `pwc_net_via_links` | the lines + `\n` when via, else `''` (token, `state/service_links.txt`) |
| `pwc_net_via_port` | 51974 |
| `pwc_net_join_dir` | `ansible/files/xray-pwc-net/join/` |
| `metrics_backlog` | `8h`: Alloy WAL `max_keepalive_time` and Prometheus `out_of_order_time_window` |

### 9.2 Design rules

- Hub and spoke. Probes route only the subnet, inside the container's netns: the host
  gets nothing. Peers reach only the server (FORWARD DROP on the server). The control plane
  is not a member: `ssh -J` through the server (sshd on the server opens the TCP itself).
- Probe sshd: port 2222, only `abc@<server net ip>`, key `pwc_net_ssh_key`.
- Server reload = `systemctl reload wg-quick@xray-pwc-net` (`wg syncconf`): adding peers
  never drops others. Probe: `wg syncconf` on config change; new address/MTU/subnet needs
  `s6-svc -r /run/service/svc-wg` (Ansible notifies it on `aio.env` change).
- `outbound_access_policy` (per probe, `group_vars/probes.yml`, default `plain`; checked by
  `tasks/outbound-policy.yml` in join and probes.yml; old `probe_relay` fails):
  `plain` = direct UDP, Alloy straight to Prometheus, 2000 samples/batch; `relay` = direct
  UDP, Alloy -> local Caddy (`aio_relay`, `AIO_RELAY=true`), 200 samples/batch, new
  connection per request; `vpn` = via service links (must be non-empty), Alloy as plain.
  The relay hides nothing from DPI (all inside WG UDP); only for a stalling long connection.
- Service links (via): wg `Endpoint = 127.0.0.1:51974`, `connect-forward` carries it.
  The server sees the VPN server's address; WG roaming handles switches. Other policies
  write an empty `service_links.txt` (= direct UDP). Links must allow UDP on the VPN server
  (x-ui may disable it).
- No host access: a probe whose tunnel never comes up is unrecoverable from here; its owner
  restarts it with a new token (`docker compose down -v`, new compose.yml, up).
- The probe keeps the probe's real DNS; Alloy and the relay use the server IP directly.

### 9.3 Members and files (all git-ignored)

- `host_vars/<name>/xray-pwc-net.yml`: `pwc_net_ip`, `pwc_net_private_key`, `pwc_net_public_key`.
  Written by `xray-pwc-net-join.yml`; rerun keeps address and keys. Keys: openssl X25519, raw
  32 bytes base64. Free addresses: `grep '^pwc_net_ip:' host_vars/*/xray-pwc-net.yml`.
- `inventory.d/<name>.yml`: new probes (group `probes`, no connection vars: those are in
  `group_vars/probes.yml`). Inventory sources: `inventory.yml,inventory.d`.
- `files/probes/<name>/{links.txt,service_links.txt}`: created empty by join (`force: false`).
- `files/xray-pwc-net/join/<name>/{compose.yml,next-steps.txt}`: compose with the token.
- Token (`pwc_net_join_token` in `group_vars/probes.yml`): base64url (no padding) of JSON
  `{v:2,node,aio_env,wg_conf,service_links,authorized_keys}`, rendered from the same
  templates as `probes.yml` (`templates/probe/{aio.env,wg.conf}.j2`), so the first deploy
  changes nothing there.

## 10. Ansible

Requirements on the controller: Ansible, zstd, Python 3, make, full repo checkout,
`ansible-galaxy collection install -r requirements.yml -p ./collections` (community.docker);
for `xray_pwc_release: local` and `make tool`: Docker with buildx, Go, uv.

| command | does |
|---|---|
| `ansible-playbook monitoring.yml` | xray-pwc-net server, Prometheus, Grafana, CoreDNS, Caddy |
| `ansible-playbook xray-pwc-net-join.yml -e name=<n>` | probe: address, keys, inventory.d, empty link files, server update, compose.yml; server name: keys only (the server play's `hosts` is `none` then) |
| `ansible-playbook probes.yml [--limit p] [-e xray_pwc_release=local]` | in the container, as abc: the release, tools, state files, switch, prune |
| `ansible-playbook xray-pwc-net-remove.yml -e name=<n>` | forget member (host_vars, inventory.d, join dir), update server; links stay; the container stays until its owner removes it |
| `ansible-playbook migrate-1.1.yml` | ONE-TIME, pre-1.1.0 -> 1.1.0: server vpnc-net -> xray-pwc-net, probes replaced via host SSH (`probe_host*`), then probes.yml. Delete after |

`--check --diff` works everywhere (read-only commands and release-fetch have `check_mode:
false`); deliveries and a new member's later plays are skipped in check mode.

`probes.yml` (connection: `group_vars/probes.yml`: `ansible_host=pwc_net_ip`, port 2222,
user `abc`, key = `pwc_net_ssh_key` without `.pub`, ProxyJump, python
`/usr/local/bin/python3`): assert links + policy -> wait_for_connection -> setup min -> arch
-> slurp `/etc/aio-version`, `/etc/aio-playwright-version` -> `tasks/release-fetch.yml`
(localhost, run_once, per arch: `local` -> `make bundle ARCHES=...`; X.Y.Z -> get_url
`releases/download/vX.Y.Z/xray-pwc-X.Y.Z-linux-<arch>.tar.zst` into `dist/X.Y.Z/` with
`checksum: sha256:<SHA256SUMS url>`; then read `release.env` from each ->
`xray_pwc_bundles[arch] = {file, env}`) -> assert image (suffix stripped) >= MIN_IMAGE ->
deliver bundle to `/app/releases/<VERSION>` if missing (`tasks/aio-package.yml`: copy,
untar to `.new`, mv) -> tools missing from the image (alloy/caddy not in `/opt/<t>-<v>`,
chromium when the image's Playwright differs) -> `make tool` on localhost, deliver to
`/app/opt/{alloy-<v>,caddy-<v>,ms-playwright-<pw>}` -> state files -> switch `current` (log
`deploy release_switched`, restart every service) -> prune releases beyond
`aio_keep_releases` (2) and `/app/opt` tools no kept release needs. Handlers: `s6-svc -r
/run/service/svc-<x>` (as abc); Sync = `wg syncconf xray-pwc-net <conf>`; tunnel handlers
then `wait_for_connection`.

`tasks/xray-pwc-net.yml` (server only): assert keys -> apt wireguard-tools, iptables ->
template `/etc/wireguard/xray-pwc-net.conf` (notify reload) -> ensure `wg-quick@xray-pwc-net`
-> docker drop-in -> CoreDNS files -> flush handlers.

Variable precedence trap: `group_vars/all.yml` beats inventory `all:vars`, so values meant
to be set in inventory are referenced with `| default()` (e.g. `pwc_net_ssh_key`,
`aio_image_name`). Host vars in the inventory beat `group_vars/probes.yml`: do not set
`ansible_host` for probes there.

Ansible silently skips a missing `vault.yml` in `vars_files`; `monitoring.yml` asserts the
keys it needs. `vault.yml` is plain text (not ansible-vault), holds only the Grafana password.

## 11. Secrets and git hygiene

Git-ignored: `inventory.yml`, `inventory.d/*` (except `.gitkeep`), `vault.yml`,
`host_vars/`, `files/probes/`, `files/xray-pwc-net/`, `collections/`, `.vault_pass`,
`.caddypass`, `/dist/`. Never print: share links, private keys, vault values, join tokens
(compose.yml). Tasks holding them use `no_log: true`. The repo is public: no real
hostnames, IPs or links in tracked files (examples use 198.51.100.0/24 and example.com).
The image and the release files are public and have no secrets.

## 12. Conventions

- English in code and docs; plain, simple wording; comments explain why.
- Python: hexagonal layers, Protocol ports, fakes in tests, tests beside code.
- Go: stdlib flags, table tests, `gofmt`, `go vet`.
- Ansible: FQCN modules, task names in sentence case, handlers in `handlers/` or the play,
  idempotent (a second run reports `changed=0`).
- Pinned versions everywhere, at least two weeks old (no `latest` in what we pull).

## 13. Known pitfalls

- RU DPI: UDP to some foreign hosting is cut; TCP flows stall after ~16 KB. Hence heavy
  pages and service links (policy vpn). The relay dates from when metrics went over plain
  TCP; inside WG it is optional (policy relay).
- Some home routers put the EDNS OPT record before the answer; Go resolvers fail ->
  `probe_dns: [1.1.1.1, 8.8.8.8]` for that probe (in compose.yml: needs the owner to recreate).
- Caddy treats an IP site address as HTTPS even with `auto_https off`: the relay site is
  written `http://127.0.0.1:8080`.
- iproute2 `ip` drops its file capabilities when the user is not root: hence `aio-net`.
  A binary with `cap_net_admin+ep` cannot even start in a container without NET_ADMIN
  (`exec: operation not permitted`), e.g. `docker run --entrypoint wg` without `--cap-add`.
- wg-quick re-executes itself with sudo when not root: the wg service does not use it.
- linuxserver.io init sets abc's uid/gid to PUID/PGID (911) and chowns `/app` at every
  start; the image sets gid 911 at build so build-time ownership matches.
- s6 oneshots do not see the container env: `init-aio` runs under `with-contenv`.
- A new container on an old xray-pwc-net address has a new SSH host key: remove the old
  `known_hosts` entry (`ssh-keygen -R '[<ip>]:2222'`).
- The monitoring stack must restart after the server's tunnel interface is recreated (bound IP).
- Docker Compose package: `docker-compose-v2` on Ubuntu, `docker-compose` on Debian 13.
- Probe-down alert: `absent_over_time(up{job="xray-pwc-probe",host="<p>"}[10m])`, "No data" = OK.
- A probe's SIGTERM finishes the current round first: after a release switch the old
  checker can run for minutes.
