import random

from xray_pwc_probe.domain.sites import (
    CheckPolicy,
    HeavySite,
    NetError,
    SiteCatalog,
    TargetSite,
)
from xray_pwc_probe.fakes import LocalPort
from xray_pwc_probe.ports.browser import PlaywrightBrowser
from xray_pwc_probe.ports.visiting import Backoff

WIKIPEDIA = HeavySite.at("https://en.wikipedia.org/wiki/Special:Random")
INSTAGRAM = TargetSite.at("https://www.instagram.com/")


def test_heavy_site_counts_any_page_that_reached_dom_content_loaded():
    assert WIKIPEDIA.loaded_at == "domcontentloaded"
    assert WIKIPEDIA.accepts_status(403)


def test_target_site_needs_status_200():
    assert INSTAGRAM.loaded_at == "commit"
    assert INSTAGRAM.accepts_status(200)
    assert not INSTAGRAM.accepts_status(429)
    assert not INSTAGRAM.accepts_status(None)


async def test_dead_tunnel_fails_every_site_after_all_retries(chromium_installed):
    catalog = SiteCatalog.from_urls(
        heavy_real=[WIKIPEDIA.url, "https://www.thesun.co.uk/"],
        heavy_cffi=[],
        targets=[INSTAGRAM.url, "https://www.facebook.com/"],
        heavy_visits=4,
    ).for_policy(CheckPolicy.HEAVY)
    browser = PlaywrightBrowser(
        catalog, backoff=Backoff(max_attempts=2, base_delay=0.01), rng=random.Random(1)
    )
    async with browser.open(LocalPort.unused().number) as session:
        heavy = await session.check_heavy_sites()
        targets = await session.check_target_sites()

    assert len(heavy) == 4
    assert {v.site for v in heavy} <= set(catalog.heavy)
    assert [v.site for v in targets] == list(catalog.targets)
    for visit in (*heavy, *targets):
        assert not visit.ok
        assert visit.attempts == 2
        assert isinstance(visit.error, NetError)
        assert visit.error.kind == "ERR_PROXY_CONNECTION_FAILED"
