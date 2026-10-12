"""First thing at every start: checks the container and, the first time,
turns the join token (VPNC_JOIN) into /app/state. Later starts keep the
state, which Ansible manages from then on, and ignore the token.

The token is base64url of JSON, made by vpnc-net-join.yml:
    {"v": 1, "node": ..., "aio_env": ..., "wg_conf": ..., "service_links": ...,
     "authorized_keys": ...}
Every file is written as it comes: the same templates make it in Ansible.
"""

import base64
import json
import os
import pwd
import socket
import subprocess
import sys
from pathlib import Path

STATE = Path("/app/state")
LOGS = Path("/app/logs")
CAP_NET_ADMIN = 12


def log(level: str, event: str, **fields: object) -> None:
    def value(raw: object) -> str:
        text = str(raw)
        return json.dumps(text) if not text or any(c in text for c in ' ="') else text

    print(" ".join([level, event, *(f"{k}={value(v)}" for k, v in fields.items())]), flush=True)


def fail(event: str, **fields: object) -> None:
    log("ERROR", event, **fields)
    sys.exit(1)


def write(path: Path, text: str, mode: int, group: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text)
    path.chmod(mode)
    if group:
        os.chown(path, 0, pwd.getpwnam(group).pw_gid)


def check_container() -> None:
    caps = next(
        line.split()[1] for line in Path("/proc/self/status").read_text().splitlines()
        if line.startswith("CapEff:")
    )
    if not int(caps, 16) >> CAP_NET_ADMIN & 1:
        fail("no_net_admin", hint="cap_add: [NET_ADMIN] in compose.yml")
    if not Path("/dev/net/tun").exists():
        fail("no_tun", hint="devices: [/dev/net/tun] in compose.yml")


def decode(token: str) -> dict:
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except ValueError as e:
        fail("bad_token", err=e)
    if not isinstance(data, dict) or data.get("v") != 1:
        fail("bad_token", err="unknown token version")
    for key in ("node", "aio_env", "wg_conf", "authorized_keys"):
        if key not in data:
            fail("bad_token", err=f"no {key}")
    return data


def create_state(data: dict) -> None:
    node = data["node"]
    if socket.gethostname() != node:
        fail("hostname_mismatch", hostname=socket.gethostname(), node=node,
             hint=f"hostname: {node} in compose.yml")
    write(STATE / "aio.env", data["aio_env"], 0o644)
    write(STATE / "wg" / "vpnc-net.conf", data["wg_conf"], 0o600)
    write(STATE / "service_links.txt", data.get("service_links", ""), 0o600)
    write(STATE / "ssh" / "authorized_keys", data["authorized_keys"], 0o600)
    write(STATE / "links.txt", "", 0o640, group="abc")
    env = dict(line.split("=", 1) for line in data["aio_env"].splitlines() if "=" in line)
    log("INFO", "state_created", node=node, ip=env.get("AIO_IP", ""))


def main() -> None:
    check_container()
    # The linuxserver.io init hands /app to abc on every start; sshd's strict
    # modes and the checker's sandbox both want it root's.
    os.chown("/app", 0, 0)
    Path("/app").chmod(0o755)
    LOGS.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o711)
    token = os.environ.get("VPNC_JOIN", "")
    if (STATE / "aio.env").exists():
        log("INFO", "state_found", node=socket.gethostname())
        if token:
            log("INFO", "token_ignored", reason="the state exists, Ansible manages it")
    elif token:
        create_state(decode(token))
    else:
        fail("no_state", hint="VPNC_JOIN from vpnc-net-join.yml in compose.yml")

    key = STATE / "ssh" / "ssh_host_ed25519_key"
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        log("INFO", "host_key_created")
    (STATE / "ssh").chmod(0o700)


if __name__ == "__main__":
    main()
