import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from attrs import define, field, frozen
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from xray_pwc_probe.app import Browser
from xray_pwc_probe.domain.sites import Site, SiteCatalog
from xray_pwc_probe.ports.visiting import (
    NAVIGATION_TIMEOUT_SECONDS,
    Backoff,
    VisitFailed,
    VisitingSession,
)


@define(eq=False)
class PlaywrightSession(VisitingSession):
    page: Page

    async def load(self, site: Site) -> None:
        try:
            response = await self.page.goto(site.url, wait_until=site.loaded_at)
        except PlaywrightError as e:
            raise VisitFailed(str(e)) from e
        status = response.status if response else None
        if not site.accepts_status(status):
            raise VisitFailed(f"HTTP {status} from {site.url}")


@frozen
class PlaywrightBrowser(Browser):
    """The HEAVY check policy: a fresh headless Chromium per tunnel."""

    catalog: SiteCatalog
    backoff: Backoff = field(factory=Backoff)
    rng: random.Random = field(factory=random.Random, eq=False)

    @asynccontextmanager
    async def open(
        self, socks_port: int, socks_host: str = "127.0.0.1"
    ) -> AsyncGenerator[PlaywrightSession]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                proxy={"server": f"socks5://{socks_host}:{socks_port}"}
            )
            try:
                page = await browser.new_page()
                page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_SECONDS * 1000)
                yield PlaywrightSession(self.catalog, self.backoff, self.rng, page)
            finally:
                await browser.close()
