// Command aio-net sets up the xray-pwc-net interface for the wg service,
// which runs as abc. It carries cap_net_admin in the image; ip from iproute2
// cannot, since it drops its capabilities when the user is not root.
//
//	aio-net add <iface>                          a kernel WireGuard interface
//	aio-net up <iface> <address/bits> <mtu> <subnet>   address, MTU, up, route
//	aio-net del <iface>
package main

import (
	"fmt"
	"net"
	"os"
	"strconv"

	"github.com/vishvananda/netlink"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "aio-net:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) < 2 {
		return usage()
	}
	cmd, iface := args[0], args[1]
	switch {
	case cmd == "add" && len(args) == 2:
		return netlink.LinkAdd(&netlink.Wireguard{LinkAttrs: netlink.LinkAttrs{Name: iface}})
	case cmd == "del" && len(args) == 2:
		link, err := netlink.LinkByName(iface)
		if err != nil {
			return nil
		}
		return netlink.LinkDel(link)
	case cmd == "up" && len(args) == 5:
		return up(iface, args[2], args[3], args[4])
	}
	return usage()
}

func up(iface, address, mtu, subnet string) error {
	link, err := netlink.LinkByName(iface)
	if err != nil {
		return err
	}
	addr, err := netlink.ParseAddr(address)
	if err != nil {
		return err
	}
	if err := netlink.AddrReplace(link, addr); err != nil {
		return fmt.Errorf("address %s: %w", address, err)
	}
	n, err := strconv.Atoi(mtu)
	if err != nil {
		return fmt.Errorf("mtu %q: %w", mtu, err)
	}
	if err := netlink.LinkSetMTU(link, n); err != nil {
		return fmt.Errorf("mtu %d: %w", n, err)
	}
	if err := netlink.LinkSetUp(link); err != nil {
		return fmt.Errorf("up: %w", err)
	}
	_, dst, err := net.ParseCIDR(subnet)
	if err != nil {
		return err
	}
	if err := netlink.RouteReplace(&netlink.Route{LinkIndex: link.Attrs().Index, Dst: dst}); err != nil {
		return fmt.Errorf("route %s: %w", subnet, err)
	}
	return nil
}

func usage() error {
	return fmt.Errorf("usage: aio-net add|del <iface>, aio-net up <iface> <address/bits> <mtu> <subnet>")
}
