import random

import pytest
from attrs import define, field

from xray_tun_pwc.domain.sites import HeavySite, LightSite, NetError, SiteCatalog
from xray_tun_pwc.fakes import LocalPort
from xray_tun_pwc.ports.browser import Backoff, GaveUp, PlaywrightBrowser, VisitFailed

WIKIPEDIA = HeavySite.at("https://en.wikipedia.org/wiki/Special:Random")
INSTAGRAM = LightSite.at("https://www.instagram.com/")


def test_heavy_site_counts_any_page_that_reached_dom_content_loaded():
    assert WIKIPEDIA.loaded_at == "domcontentloaded"
    assert WIKIPEDIA.accepts_status(403)


def test_target_site_needs_status_200():
    assert INSTAGRAM.loaded_at == "commit"
    assert INSTAGRAM.accepts_status(200)
    assert not INSTAGRAM.accepts_status(429)
    assert not INSTAGRAM.accepts_status(None)


@define
class RecordedSleep:
    slept: list[float] = field(factory=list)

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


@define
class FlakyAction:
    failures_before_success: int
    reason: str = "HTTP 503"
    calls: int = 0

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise VisitFailed(self.reason)


async def test_backoff_doubles_the_delay_until_success():
    sleep = RecordedSleep()
    backoff = Backoff(max_attempts=4, base_delay=2.0, sleep=sleep)
    assert await backoff.run(FlakyAction(failures_before_success=2)) == 3
    assert sleep.slept == [2.0, 4.0]


async def test_backoff_gives_up_with_the_first_line_of_the_last_error():
    backoff = Backoff(max_attempts=3, base_delay=1.0, sleep=RecordedSleep())
    always_failing = FlakyAction(failures_before_success=99, reason="HTTP 429\ncall log")
    with pytest.raises(GaveUp) as exc:
        await backoff.run(always_failing)
    assert (exc.value.attempts, exc.value.reason) == (3, "HTTP 429")
    assert always_failing.calls == 3


async def test_dead_tunnel_fails_every_site_after_all_retries(chromium_installed):
    catalog = SiteCatalog.from_urls(
        heavy=[WIKIPEDIA.url, "https://www.thesun.co.uk/"],
        targets=[INSTAGRAM.url, "https://www.facebook.com/"],
        heavy_visits=4,
    )
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
