# xray-pwc: just the commands

Ansible commands run from `ansible/`. Details: [README.md](README.md), [ansible/README.md](ansible/README.md).

## First setup

```bash
cd ansible
ansible-galaxy collection install -r requirements.yml -p ./collections
cp inventory.example.yml inventory.yml && $EDITOR inventory.yml   # server, domain, pwc_net_ssh_key
cp vault.yml.example vault.yml && $EDITOR vault.yml               # Grafana password
ansible-playbook xray-pwc-net-join.yml -e name=mon-1              # the server's keys
ansible-playbook monitoring.yml                                   # the server
```

## Add a probe

```bash
ansible-playbook xray-pwc-net-join.yml -e name=spb-home-1 -e policy=plain              # UDP to the server works
ansible-playbook xray-pwc-net-join.yml -e name=spb-home-1 -e policy=relay              # long TCP stalls
ansible-playbook xray-pwc-net-join.yml -e name=spb-home-1 -e policy=vpn \
  -e service_links="$(base64 < service_links.txt)"                                      # UDP blocked
ansible-playbook xray-pwc-net-join.yml -e name=spb-home-1 -e policy=plain -e target=ru  # image from the ru registry
```

Then send `files/xray-pwc-net/join/spb-home-1/compose.yml` to the owner, and:

```bash
$EDITOR files/probes/spb-home-1/links.txt
ansible-playbook probes.yml --limit spb-home-1
```

## On the probe machine (its owner)

```bash
docker compose up -d                          # start
docker compose logs -f                        # wait for: wg INFO handshake_ok
docker compose pull && docker compose up -d   # new image: after editing image: in compose.yml
docker compose down -v                        # wipe, then up -d with a new compose.yml = new token
docker compose down -v --rmi all              # remove for good
```

## Every day

```bash
ansible-playbook probes.yml                                          # everything to every probe
ansible-playbook probes.yml --limit spb-home-1                       # one probe (links, policy)
ansible-playbook probes.yml --limit spb-home-1 -e xray_pwc_release=local   # try the working tree
ansible-playbook probes.yml --check --diff                           # what would change
ansible-playbook xray-pwc-net-join.yml -e name=spb-home-1 -e policy=plain  # lost compose.yml: same keys, new token
```

## Look at a probe

```bash
ssh -J root@<server> -p 2222 abc@spb-home-1.xray-pwc-net.internal                          # shell
ssh -J root@<server> -p 2222 abc@spb-home-1.xray-pwc-net.internal tail -f /app/logs/aio.log  # log
ssh -J root@<server> -p 2222 abc@spb-home-1.xray-pwc-net.internal s6-svc -r /run/service/svc-probe  # restart a service
ssh-keygen -R '[10.213.97.11]:2222'                                     # new container on an old address
```

## Remove a probe

```bash
ansible-playbook xray-pwc-net-remove.yml -e name=spb-home-1                  # out of xray-pwc-net
ansible-playbook xray-pwc-net-remove.yml -e name=spb-home-1 -e purge=true    # and its links, inventory, known_hosts
```

## Build and test

```bash
make test        # everything CI runs
make version     # what these sources build as
make bundle      # dist/<version>/xray-pwc-<version>-linux-<arch>.tar.zst
make image       # the image, into local Docker
make connect     # xray-pwc-connect for linux/darwin, amd64/arm64
make tool TOOL=alloy TOOL_VERSION=v1.20.0 ARCH=arm64
```

## Release

```bash
echo 2.1.0 > VERSION && sed -i '' 's/^version = .*/version = "2.1.0"/' xray-pwc-probe/pyproject.toml
(cd xray-pwc-probe && uv lock) && make check-version
git commit -am "release 2.1.0" && git tag v2.1.0 && git push && git push origin v2.1.0
ansible-playbook probes.yml       # after CI: every probe to the release
```

CI pushes the image to GHCR.

## xray-pwc-connect alone

```bash
go install github.com/nikitades/xray-pwc/xray-pwc-connect/v2/cmd/xray-pwc-connect@latest
xray-pwc-connect -stdin-links < links.txt                                         # SOCKS5 on 10000, 10001, ...
xray-pwc-connect -forward vpn.example.com:51973 -start-port 51974 -stdin-links < links.txt   # one port, first usable link
```

## xray-pwc-probe alone

```bash
uv tool install https://github.com/nikitades/xray-pwc/releases/download/vX.Y.Z/xray_pwc_probe-X.Y.Z-py3-none-any.whl
uvx --from playwright playwright install chromium
PWC_CONFIG_PATH=links.txt xray-pwc-probe   # metrics on :8000
```
