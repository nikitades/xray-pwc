import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from attrs import frozen
from packaging.version import Version

from xray_tun_pwc.app import (
    TunnelOptions,
    XrayLogLevel,
    XrayTunConnectFailed,
    XrayTunConnectUnavailable,
)
from xray_tun_pwc.domain.entities import VpnConfigLine
from xray_tun_pwc.fakes import LocalPort
from xray_tun_pwc.ports.xray_tun_connect import XrayTunConnectBinary, XrayTunConnectCli


async def test_parses_version(version_script):
    assert await XrayTunConnectCli(version_script("0.3.7")).version() == Version("0.3.7")


async def test_missing_binary_explains_how_to_install(tmp_path: Path):
    with pytest.raises(XrayTunConnectUnavailable, match="not found") as exc:
        await XrayTunConnectCli(tmp_path / "nope").version()
    assert "make install" in str(exc.value)
    assert "XRC_XRAY_TUN_CONNECT_BIN" in str(exc.value)


async def test_nothing_installed_anywhere(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GOBIN", str(tmp_path / "gobin"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    with pytest.raises(XrayTunConnectUnavailable, match="not found"):
        await XrayTunConnectCli().version()


async def test_non_zero_exit(version_script):
    with pytest.raises(XrayTunConnectUnavailable, match="exited 3"):
        await XrayTunConnectCli(version_script("boom", exit_code=3)).version()


async def test_garbage_output(version_script):
    with pytest.raises(XrayTunConnectUnavailable, match="not a version"):
        await XrayTunConnectCli(version_script("hello")).version()


def test_go_install_wins_over_path(monkeypatch, version_script, tmp_path: Path):
    on_path = version_script("1.0.0")
    gobin = tmp_path / "gobin"
    gobin.mkdir()
    installed_by_go = gobin / "xray-tun-connect"
    installed_by_go.write_bytes(on_path.read_bytes())
    installed_by_go.chmod(0o755)

    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setenv("GOBIN", str(gobin))
    assert XrayTunConnectBinary.installed() == XrayTunConnectBinary(installed_by_go)

    monkeypatch.setenv("GOBIN", str(tmp_path / "empty"))
    assert XrayTunConnectBinary.installed() == XrayTunConnectBinary(on_path)


def test_gopath_defaults_to_home_go(monkeypatch, version_script, tmp_path: Path):
    home_go_bin = tmp_path / "go" / "bin"
    home_go_bin.mkdir(parents=True)
    installed_by_go = home_go_bin / "xray-tun-connect"
    installed_by_go.write_bytes(version_script("1.0.0").read_bytes())
    installed_by_go.chmod(0o755)

    monkeypatch.delenv("GOBIN", raising=False)
    monkeypatch.delenv("GOPATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert XrayTunConnectBinary.installed() == XrayTunConnectBinary(installed_by_go)


async def test_against_real_binary(real_xray_tun_connect: Path):
    assert await XrayTunConnectCli(real_xray_tun_connect).version() >= Version("0.1.0")


MSK = VpnConfigLine.from_str("vless://u@msk.example:443?security=tls#msk")
BROKEN = VpnConfigLine.from_str("garbage://nope#broken")


@frozen
class ReportLine:
    tunnels: tuple[dict[str, Any], ...]

    @classmethod
    def of(cls, *tunnels: dict[str, Any]) -> ReportLine:
        return cls(tunnels)

    def __str__(self) -> str:
        return json.dumps({"version": "0.1.0", "listen": "127.0.0.1", "tunnels": self.tunnels})


async def test_start_passes_every_link_and_option(tunnels_script, tmp_path: Path):
    binary = tunnels_script(str(ReportLine.of({"index": 0, "port": 20000, "ok": True})))
    options = TunnelOptions(
        start_port=20000,
        listen="0.0.0.0",
        xray_log_level=XrayLogLevel.INFO,
        ready_timeout=timedelta(seconds=2.5),
    )
    async with XrayTunConnectCli(binary).start([MSK, BROKEN], options):
        pass
    assert (tmp_path / "args").read_text().splitlines() == [
        "-stdin-links",
        "-start-port", "20000",
        "-listen", "0.0.0.0",
        "-xray-log-level", "info",
        "-ready-timeout", "2.5s",
    ]  # fmt: skip


async def test_links_go_through_stdin_not_the_command_line(tunnels_script, tmp_path: Path):
    binary = tunnels_script(str(ReportLine.of({"index": 0, "port": 10000, "ok": True})))
    async with XrayTunConnectCli(binary).start([MSK, BROKEN]):
        pass
    assert (tmp_path / "links").read_text().splitlines() == [MSK.value, BROKEN.value]
    assert MSK.value not in (tmp_path / "args").read_text()


async def test_start_pairs_tunnels_with_their_configs(tunnels_script):
    binary = tunnels_script(
        str(
            ReportLine.of(
                {"index": 0, "port": 10000, "name": "msk", "protocol": "vless", "ok": True},
                {"index": 1, "port": 10001, "ok": False, "error": "unsupported share format"},
            )
        )
    )
    async with XrayTunConnectCli(binary).start([MSK, BROKEN]) as group:
        msk, broken = group.tunnels
    assert (msk.config, msk.port, msk.protocol, msk.ok, msk.error) == (
        MSK,
        10000,
        "vless",
        True,
        None,
    )
    assert (broken.config, broken.port, broken.ok) == (BROKEN, 10001, False)
    assert broken.error == "unsupported share format"


async def test_leaving_the_context_stops_the_binary(tunnels_script, tmp_path: Path):
    binary = tunnels_script(str(ReportLine.of({"index": 0, "port": 10000, "ok": True})))
    async with XrayTunConnectCli(binary).start([MSK]):
        assert not (tmp_path / "stopped").exists()
    assert (tmp_path / "stopped").exists()


async def test_exit_without_report_is_a_failure(tunnels_script):
    with pytest.raises(XrayTunConnectFailed, match="exited with code 1"):
        async with XrayTunConnectCli(tunnels_script(None, exit_code=1)).start([MSK]):
            pass


async def test_start_needs_at_least_one_config(tunnels_script):
    with pytest.raises(ValueError, match="no configs"):
        async with XrayTunConnectCli(tunnels_script(None)).start([]):
            pass


async def test_real_binary_serves_socks_ports_until_the_context_ends(real_xray_tun_connect: Path):
    unreachable_but_valid = VpnConfigLine.from_str(
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:9?type=tcp&security=none#local"
    )
    start_port = LocalPort.unused().number
    async with XrayTunConnectCli(real_xray_tun_connect).start(
        [unreachable_but_valid, BROKEN], TunnelOptions(start_port=start_port)
    ) as group:
        valid, broken = group.tunnels
        assert valid.ok and valid.port == start_port and valid.protocol == "vless"
        assert not broken.ok and broken.port == start_port + 1
        assert LocalPort(valid.port).accepts_connections()
    assert not LocalPort(valid.port).accepts_connections()
