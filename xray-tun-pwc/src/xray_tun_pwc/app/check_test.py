from lagom import Container

from xray_tun_pwc.app import Browser, XrayTunConnect
from xray_tun_pwc.app.check import CheckConfigsUsecase, CheckRequest
from xray_tun_pwc.entrypoints.cli import Settings
from xray_tun_pwc.fakes import FakeBrowser, FakeXrayTunConnect


async def test_every_alive_tunnel_goes_through_the_whole_scenario(
    container: Container, settings: Settings
):
    tunnels, browser = FakeXrayTunConnect(), FakeBrowser(settings.get_site_catalog())
    container[XrayTunConnect] = tunnels
    container[Browser] = browser

    checks = await container[CheckConfigsUsecase].handle(CheckRequest(settings.get_config()))

    assert [c.tunnel.config.tag for c in checks] == ["msk", "spb"]
    assert browser.opened_ports == [10000, 10001]
    assert all(c.success and c.perfect for c in checks)
    assert [v.site.name for v in checks[0].visits] == [
        "en.wikipedia.org", "thesun.co.uk", "site123.com", "instagram.com", "facebook.com",
    ]  # fmt: skip


async def test_broken_link_is_reported_without_opening_a_browser(
    container: Container, settings: Settings
):
    browser = FakeBrowser(settings.get_site_catalog())
    container[XrayTunConnect] = FakeXrayTunConnect(broken_tags=frozenset({"msk"}))
    container[Browser] = browser

    msk, spb = await container[CheckConfigsUsecase].handle(CheckRequest(settings.get_config()))

    assert not msk.success and msk.visits == ()
    assert msk.tunnel.error == "unsupported share format"
    assert spb.perfect
    assert browser.opened_ports == [10001]


async def test_blocked_site_fails_only_its_own_visit(container: Container, settings: Settings):
    container[XrayTunConnect] = FakeXrayTunConnect()
    container[Browser] = FakeBrowser(
        settings.get_site_catalog(), failing_sites=frozenset({"instagram.com"})
    )

    checks = await container[CheckConfigsUsecase].handle(CheckRequest(settings.get_config()))

    failed = {v.site.name for c in checks for v in c.visits if not v.ok}
    assert failed == {"instagram.com"}
    assert all(c.success and not c.perfect for c in checks)


async def test_tunnels_are_stopped_after_the_check(container: Container, settings: Settings):
    tunnels = FakeXrayTunConnect()
    container[XrayTunConnect] = tunnels
    container[Browser] = FakeBrowser(settings.get_site_catalog())

    await container[CheckConfigsUsecase].handle(CheckRequest(settings.get_config()))

    assert tunnels.started_with == settings.get_config()
    assert not tunnels.running
