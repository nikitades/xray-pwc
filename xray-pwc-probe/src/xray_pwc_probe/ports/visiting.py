import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from attrs import define, frozen

from xray_pwc_probe.app import BrowsingSession
from xray_pwc_probe.domain.sites import Site, SiteCatalog, SiteVisit, SiteVisitError

# Both ways of loading a page give up on it after this long.
NAVIGATION_TIMEOUT_SECONDS = 30


class VisitFailed(Exception):
    """One attempt to load a page failed; the message is a raw SiteVisitError."""


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
            except VisitFailed as e:
                if attempt == self.max_attempts:
                    raise GaveUp(attempt, str(e).splitlines()[0]) from e
                await self.sleep(self.delay_after(attempt))
        raise ValueError(f"max_attempts must be positive, got {self.max_attempts}")


@define(eq=False)
class VisitingSession(BrowsingSession, ABC):
    """The sites of one check, in order, with retries; how a page loads is up
    to the subclass."""

    catalog: SiteCatalog
    backoff: Backoff
    rng: random.Random

    async def check_heavy_sites(self) -> tuple[SiteVisit, ...]:
        return tuple(
            [
                await self.visit(self.rng.choice(self.catalog.heavy))
                for _ in range(self.catalog.heavy_visits)
            ]
        )

    async def check_target_sites(self) -> tuple[SiteVisit, ...]:
        return tuple([await self.visit(site) for site in self.catalog.targets])

    async def visit(self, site: Site) -> SiteVisit:
        try:
            attempts = await self.backoff.run(lambda: self.load(site))
        except GaveUp as e:
            return SiteVisit.failed(
                site, site.url, attempts=e.attempts, error=SiteVisitError.from_raw(e.reason)
            )
        return SiteVisit.succeeded(site, site.url, attempts=attempts)

    @abstractmethod
    async def load(self, site: Site) -> None:
        """Loads the page once; raises VisitFailed if it did not count as opened."""
