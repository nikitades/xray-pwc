import asyncio
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from attrs import define, field, frozen
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from xray_pwc_probe.app import Browser, BrowsingSession
from xray_pwc_probe.domain.sites import Site, SiteCatalog, SiteVisit, SiteVisitError

NAVIGATION_TIMEOUT_MS = 30_000


class VisitFailed(Exception):
    pass


class GaveUp(Exception):
    def __init__(self, attempts: int, reason: str) -> None:
        self.attempts, self.reason = attempts, reason
        super().__init__(f"gave up after {attempts} attempts: {reason}")


@frozen
class Backoff:
    max_attempts: int = 3
    base_delay: float = 2.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def delay_after(self, attempt: int) -> float:
        return self.base_delay * 2 ** (attempt - 1)

    async def run(self, action: Callable[[], Awaitable[None]]) -> int:
        for attempt in range(1, self.max_attempts + 1):
            try:
                await action()
                return attempt
            except (PlaywrightError, VisitFailed) as e:
                if attempt == self.max_attempts:
                    raise GaveUp(attempt, str(e).splitlines()[0]) from e
                await self.sleep(self.delay_after(attempt))
        raise ValueError(f"max_attempts must be positive, got {self.max_attempts}")


@define(eq=False)
class PlaywrightSession(BrowsingSession):
    page: Page
    browser: PlaywrightBrowser

    async def check_heavy_sites(self) -> tuple[SiteVisit, ...]:
        catalog = self.browser.catalog
        return tuple(
            [
                await self.visit(self.browser.rng.choice(catalog.heavy))
                for _ in range(catalog.heavy_visits)
            ]
        )

    async def check_target_sites(self) -> tuple[SiteVisit, ...]:
        return tuple([await self.visit(site) for site in self.browser.catalog.targets])

    async def visit(self, site: Site) -> SiteVisit:
        try:
            attempts = await self.browser.backoff.run(lambda: self.load(site))
        except GaveUp as e:
            return SiteVisit.failed(
                site, site.url, attempts=e.attempts, error=SiteVisitError.from_raw(e.reason)
            )
        return SiteVisit.succeeded(site, site.url, attempts=attempts)

    async def load(self, site: Site) -> None:
        response = await self.page.goto(site.url, wait_until=site.loaded_at)
        status = response.status if response else None
        if not site.accepts_status(status):
            raise VisitFailed(f"HTTP {status} from {site.url}")


@frozen
class PlaywrightBrowser(Browser):
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
                page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
                yield PlaywrightSession(page, self)
            finally:
                await browser.close()
