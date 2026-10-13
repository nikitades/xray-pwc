import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from attrs import frozen

from xray_pwc_probe.app import (
    TunnelOptions,
    XrayLogLevel,
    XrayPwcConnectFailed,
    XrayPwcConnectUnavailable,
)
from xray_pwc_probe.domain.entities import VpnConfigLine
from xray_pwc_probe.domain.release import Release
from xray_pwc_probe.fakes import LocalPort
from xray_pwc_probe.ports.xray_pwc_connect import XrayPwcConnectBinary, XrayPwcConnectCli


async def test_parses_version(version_script):
    assert (
        await XrayPwcConnectCli(version_script("1.1.0-dev.3f9c2ab")).version()
        == "1.1.0-dev.3f9c2ab"
    )


async def test_missing_binary_explains_how_to_install(tmp_path: Path):
    with pytest.raises(XrayPwcConnectUnavailable, match="not found") as exc:
        await XrayPwcConnectCli(tmp_path / "nope").version()
    assert "go install github.com/nikitades/xray-pwc/xray-pwc-connect" in str(exc.value)
    assert "PWC_CONNECT_BIN" in str(exc.value)


async def test_nothing_installed_anywhere(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GOBIN", str(tmp_path / "gobin"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    with pytest.raises(XrayPwcConnectUnavailable, match="not found"):
        await XrayPwcConnectCli().version()


async def test_non_zero_exit(version_script):
    with pytest.raises(XrayPwcConnectUnavailable, match="exited 3"):
        await XrayPwcConnectCli(version_script("boom", exit_code=3)).version()


async def test_garbage_output(version_script):
    with pytest.raises(XrayPwcConnectUnavailable, match="not a version"):
        await XrayPwcConnectCli(version_script("hello")).version()


def test_go_install_wins_over_path(monkeypatch, version_script, tmp_path: Path):
    on_path = version_script("1.0.0")
    gobin = tmp_path / "gobin"
    gobin.mkdir()
    installed_by_go = gobin / "xray-pwc-connect"
    installed_by_go.write_bytes(on_path.read_bytes())
    installed_by_go.chmod(0o755)

    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setenv("GOBIN", str(gobin))
    assert XrayPwcConnectBinary.installed() == XrayPwcConnectBinary(installed_by_go)

    monkeypatch.setenv("GOBIN", str(tmp_path / "empty"))
    assert XrayPwcConnectBinary.installed() == XrayPwcConnectBinary(on_path)


def test_gopath_defaults_to_home_go(monkeypatch, version_script, tmp_path: Path):
    home_go_bin = tmp_path / "go" / "bin"
    home_go_bin.mkdir(parents=True)
    installed_by_go = home_go_bin / "xray-pwc-connect"
    installed_by_go.write_bytes(version_script("1.0.0").read_bytes())
    installed_by_go.chmod(0o755)

    monkeypatch.delenv("GOBIN", raising=False)
    monkeypatch.delenv("GOPATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert XrayPwcConnectBinary.installed() == XrayPwcConnectBinary(installed_by_go)


async def test_against_real_binary(real_xray_pwc_connect: Path):
    assert Release.of(await XrayPwcConnectCli(real_xray_pwc_connect).version())


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
    async with XrayPwcConnectCli(binary).start([MSK, BROKEN], options):
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
    async with XrayPwcConnectCli(binary).start([MSK, BROKEN]):
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
    async with XrayPwcConnectCli(binary).start([MSK, BROKEN]) as group:
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
    async with XrayPwcConnectCli(binary).start([MSK]):
        assert not (tmp_path / "stopped").exists()
    assert (tmp_path / "stopped").exists()


async def test_exit_without_report_is_a_failure(tunnels_script):
    with pytest.raises(XrayPwcConnectFailed, match="exited with code 1"):
        async with XrayPwcConnectCli(tunnels_script(None, exit_code=1)).start([MSK]):
            pass


async def test_start_needs_at_least_one_config(tunnels_script):
    with pytest.raises(ValueError, match="no configs"):
        async with XrayPwcConnectCli(tunnels_script(None)).start([]):
            pass


async def test_real_binary_serves_socks_ports_until_the_context_ends(real_xray_pwc_connect: Path):
    unreachable_but_valid = VpnConfigLine.from_str(
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:9?type=tcp&security=none#local"
    )
    start_port = LocalPort.unused().number
    async with XrayPwcConnectCli(real_xray_pwc_connect).start(
        [unreachable_but_valid, BROKEN], TunnelOptions(start_port=start_port)
    ) as group:
        valid, broken = group.tunnels
        assert valid.ok and valid.port == start_port and valid.protocol == "vless"
        assert not broken.ok and broken.port == start_port + 1
        assert LocalPort(valid.port).accepts_connections()
    assert not LocalPort(valid.port).accepts_connections()
