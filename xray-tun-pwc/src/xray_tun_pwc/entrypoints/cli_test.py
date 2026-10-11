import asyncio
import socket
from pathlib import Path

import pytest
from lagom import Container
from packaging.version import Version

from xray_tun_pwc.app import Browser, TunnelMetrics, XrayTunConnect
from xray_tun_pwc.domain.entities import VpnConfigLine
from xray_tun_pwc.entrypoints.cli import CliApp, Settings
from xray_tun_pwc.fakes import FakeBrowser, FakeMetrics, FakeXrayTunConnect


def test_defaults():
    s = Settings()
    assert s.config_path == Path("config.txt")
    assert s.instance == socket.gethostname()
    assert (s.check_interval.total_seconds(), s.check_jitter.total_seconds()) == (300, 120)
    assert s.xray_tun_connect_bin is None


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("PWC_XRAY_TUN_CONNECT_BIN", "/opt/bin/xray-tun-connect")
    assert Settings().xray_tun_connect_bin == Path("/opt/bin/xray-tun-connect")


def test_kwargs_beat_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PWC_CONFIG_PATH", "/etc/pwc/links.txt")
    assert Settings(config_path=tmp_path / "t.txt").config_path == tmp_path / "t.txt"


def test_min_xray_tun_connect_version_is_a_constant(monkeypatch):
    monkeypatch.setenv("PWC_MIN_XRAY_TUN_CONNECT_VERSION", "9.9.9")
    assert Settings().get_min_xray_tun_connect_version() == Version("0.1.0")


def test_config_lines_override_the_file(tmp_path):
    line = VpnConfigLine(value="vless://u@h:443#t", tag="t")
    s = Settings(config_path=tmp_path / "missing.txt", config_lines=(line,))
    assert s.get_config() == (line,)


def test_get_config_skips_comments_and_blank_lines(tmp_path):
    p = tmp_path / "config.txt"
    p.write_text(
        "# top\n\nvless://u@h:443?security=tls#msk-1\n  \n  # indented comment\ntrojan://p@h:443#spb\n"
    )
    assert [c.tag for c in Settings(config_path=p).get_config()] == ["msk-1", "spb"]


def test_get_config_missing_file_names_resolved_path(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        Settings(config_path=tmp_path / "nope.txt").get_config()
    assert str(tmp_path) in str(exc.value)


async def test_cli_app_refuses_an_old_xray_tun_connect(container: Container, capsys):
    container[XrayTunConnect] = FakeXrayTunConnect(reported_version="0.0.1")
    assert await container[CliApp].run() == 1
    assert "older than the required 0.1.0" in capsys.readouterr().err


async def test_cli_app_prints_one_line_per_tunnel(container: Container, settings: Settings, capsys):
    container[XrayTunConnect] = FakeXrayTunConnect(broken_tags=frozenset({"spb"}))
    container[Browser] = FakeBrowser(
        settings.get_site_catalog(), failing_sites=frozenset({"instagram.com"})
    )
    app = container[CliApp]

    await app.run_once(settings.get_config())

    assert capsys.readouterr().out.splitlines() == [
        "msk - partial[instagram.com: ERR_TIMED_OUT]",
        "spb - failed[tunnel: unsupported share format]",
    ]


async def test_cli_app_introduces_itself_then_checks_until_stopped(container: Container, capsys):
    app = container[CliApp]
    metrics = container[TunnelMetrics]
    assert isinstance(metrics, FakeMetrics)

    running = asyncio.create_task(app.run())
    while not metrics.recorded:
        await asyncio.sleep(0.01)
    app.stop()

    assert await running == 0
    assert metrics.serving
    intro, *results = capsys.readouterr().out.splitlines()
    assert intro.startswith("xray-tun-pwc ")
    assert "| 2 tunnels | every PT5M + up to PT2M" in intro
    assert results == ["msk - OK", "spb - OK"]


def test_intervals_are_read_as_iso_8601(monkeypatch):
    monkeypatch.setenv("PWC_CHECK_INTERVAL", "PT10M")
    monkeypatch.setenv("PWC_CHECK_JITTER", "PT30S")
    s = Settings()
    assert (s.check_interval.total_seconds(), s.check_jitter.total_seconds()) == (600, 30)


def test_instance_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("PWC_INSTANCE", "spb-probe-1")
    assert Settings().instance == "spb-probe-1"


def test_sites_are_configurable_as_json_lists(monkeypatch):
    monkeypatch.setenv("PWC_HEAVY_SITES", '["https://example.com/big-page"]')
    monkeypatch.setenv("PWC_TARGET_SITES", '["https://www.youtube.com/watch?v=x", "https://t.me/"]')
    monkeypatch.setenv("PWC_HEAVY_VISITS", "5")
    catalog = Settings().get_site_catalog()
    assert [s.url for s in catalog.heavy] == ["https://example.com/big-page"]
    assert [s.name for s in catalog.targets] == ["youtube.com", "t.me"]
    assert catalog.targets[0].url == "https://www.youtube.com/watch?v=x"
    assert catalog.heavy_visits == 5


def test_defaults_open_three_heavy_pages_then_instagram_and_facebook():
    catalog = Settings().get_site_catalog()
    assert catalog.heavy_visits == 3
    assert [s.name for s in catalog.heavy] == ["en.wikipedia.org", "thesun.co.uk", "site123.com"]
    assert [s.name for s in catalog.targets] == ["instagram.com", "facebook.com"]


def test_site_urls_are_validated(monkeypatch):
    monkeypatch.setenv("PWC_TARGET_SITES", '["not a url"]')
    with pytest.raises(ValueError):
        Settings()
