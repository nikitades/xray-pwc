"""Drops one host from a hand-written YAML inventory, keeping the rest as it is.

    inventory-drop-host.py <inventory.yml> <host> --check|--write

The host's block is its `<host>:` line and every line under it that is blank or
indented deeper: its settings and their comments. Comments and order elsewhere
stay, which a YAML load and dump would lose. Prints a diff and "changed", or
"absent" when the host is not there; with --check it writes nothing.
"""

import difflib
import re
import sys

import yaml


def hosts_of(tree):
    """Every host name in a YAML inventory tree."""
    names = set()
    for group in (tree or {}).values():
        if not isinstance(group, dict):
            continue
        names.update((group.get("hosts") or {}).keys())
        names.update(hosts_of(group.get("children")))
    return names


def main():
    path, host = sys.argv[1], sys.argv[2]
    check = sys.argv[3] == "--check"
    with open(path) as f:
        lines = f.readlines()

    start = re.compile(r"^( +)" + re.escape(host) + r":\s*(#.*)?$")
    out, i, dropped = [], 0, False
    while i < len(lines):
        m = start.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent = len(m.group(1))
        i += 1
        while i < len(lines) and (
            not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > indent
        ):
            i += 1
        # Blank lines at the end of the block separate it from what follows: keep one.
        if lines[i - 1].strip() == "" and out and out[-1].strip():
            out.append("\n")
        dropped = True

    if not dropped:
        print("absent")
        return
    text = "".join(out)
    if host in hosts_of(yaml.safe_load(text)):
        sys.exit(f"{host} is still in {path} after the edit: remove it by hand")
    sys.stdout.writelines(difflib.unified_diff(lines, out, path, path))
    if not check:
        with open(path, "w") as f:
            f.write(text)
    print("changed")


main()
