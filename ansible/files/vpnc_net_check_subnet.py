"""Fails if the vpnc-net subnet overlaps a route or an address that is not ours.

Usage: vpnc_net_check_subnet.py <subnet> <our interface>...
"""

import ipaddress
import json
import subprocess
import sys

subnet = ipaddress.ip_network(sys.argv[1])
ours = set(sys.argv[2:])


def ip_json(*args):
    return json.loads(subprocess.check_output(["ip", "-j", "-4", *args]))


busy = []
for route in ip_json("route", "show", "table", "all"):
    if route.get("dev") in ours or route.get("dst") == "default":
        continue
    try:
        network = ipaddress.ip_network(route["dst"], strict=False)
    except (KeyError, ValueError):
        continue
    if network.overlaps(subnet):
        busy.append(f"route {route['dst']} dev {route.get('dev')}")

for link in ip_json("addr", "show"):
    if link.get("ifname") in ours:
        continue
    for addr in link.get("addr_info", []):
        network = ipaddress.ip_network(f"{addr['local']}/{addr['prefixlen']}", strict=False)
        if network.overlaps(subnet):
            busy.append(f"address {addr['local']}/{addr['prefixlen']} on {link['ifname']}")

if busy:
    print(f"{subnet} is already used here: " + "; ".join(busy))
    sys.exit(1)
