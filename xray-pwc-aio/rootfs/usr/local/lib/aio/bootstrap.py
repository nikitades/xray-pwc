"""First thing at every start, as root: checks the container, hands /app to
abc, the user every service runs as, and, the first time, turns the join token
(PWC_JOIN) into /app/state. Later starts keep the state, which Ansible manages
from then on, and ignore the token.

The token is base64url of JSON, made by xray-pwc-net-join.yml:
    {"v": 2, "node": ..., "aio_env": ..., "wg_conf": ..., "service_links": ...,
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

APP = Path("/app")
STATE = APP / "state"
LOGS = APP / "logs"
TOKEN_VERSION = 2
USER = "abc"
CAP_NET_ADMIN = 12


def log(level: str, event: str, **fields: object) -> None:
    def value(raw: object) -> str:
        text = str(raw)
        return json.dumps(text) if not text or any(c in text for c in ' ="') else text

    print(" ".join([level, event, *(f"{k}={value(v)}" for k, v in fields.items())]), flush=True)


def fail(event: str, **fields: object) -> None:
    log("ERROR", event, **fields)
    sys.exit(1)


def owner() -> tuple[int, int]:
    user = pwd.getpwnam(USER)
    return user.pw_uid, user.pw_gid


def hand_over(path: Path) -> None:
    """path and everything under it belongs to abc."""
    uid, gid = owner()
    for root, dirs, files in os.walk(path):
        for name in [root, *(os.path.join(root, n) for n in dirs + files)]:
            st = os.lstat(name)
            if (st.st_uid, st.st_gid) != (uid, gid):
                os.lchown(name, uid, gid)


def write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text)
    path.chmod(mode)


def check_container() -> None:
    caps = next(
        line.split()[1]
        for line in Path("/proc/self/status").read_text().splitlines()
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
    if not isinstance(data, dict) or data.get("v") != TOKEN_VERSION:
        fail("bad_token", err=f"not a v{TOKEN_VERSION} token, make it again: xray-pwc-net-join.yml")
    for key in ("node", "aio_env", "wg_conf", "authorized_keys"):
        if key not in data:
            fail("bad_token", err=f"no {key}")
    return data


def create_state(data: dict) -> None:
    node = data["node"]
    if socket.gethostname() != node:
        fail(
            "hostname_mismatch",
            hostname=socket.gethostname(),
            node=node,
            hint=f"hostname: {node} in compose.yml",
        )
    write(STATE / "aio.env", data["aio_env"], 0o644)
    write(STATE / "wg" / "xray-pwc-net.conf", data["wg_conf"], 0o600)
    write(STATE / "service_links.txt", data.get("service_links", ""), 0o600)
    write(STATE / "ssh" / "authorized_keys", data["authorized_keys"], 0o600)
    write(STATE / "links.txt", "", 0o600)
    env = dict(line.split("=", 1) for line in data["aio_env"].splitlines() if "=" in line)
    log("INFO", "state_created", node=node, ip=env.get("AIO_IP", ""))


def main() -> None:
    # The log file first, and abc's: this script's own lines go through
    # aio-log as root, which must not create it as root's.
    uid, gid = owner()
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "aio.log").touch()
    hand_over(LOGS)
    check_container()
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    token = os.environ.get("PWC_JOIN", "")
    if (STATE / "aio.env").exists():
        log("INFO", "state_found", node=socket.gethostname())
        if token:
            log("INFO", "token_ignored", reason="the state exists, Ansible manages it")
    elif token:
        create_state(decode(token))
    else:
        fail("no_state", hint="PWC_JOIN from xray-pwc-net-join.yml in compose.yml")

    key = STATE / "ssh" / "ssh_host_ed25519_key"
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        log("INFO", "host_key_created")
    for path in (APP / "releases", APP / "opt", STATE):
        path.mkdir(parents=True, exist_ok=True)
        hand_over(path)
    if (APP / "current").is_symlink():
        os.lchown(APP / "current", uid, gid)
    os.chown(APP, uid, gid)
    # wireguard-go keeps its control socket here: /run is root's, and new at
    # every start.
    run = Path("/run/wireguard")
    run.mkdir(exist_ok=True)
    os.chown(run, uid, gid)


if __name__ == "__main__":
    main()
