import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from attrs import define, field, frozen
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError

from xray_pwc_probe.app import Browser
from xray_pwc_probe.domain.sites import Site, SiteCatalog
from xray_pwc_probe.ports.visiting import (
    NAVIGATION_TIMEOUT_SECONDS,
    Backoff,
    VisitFailed,
    VisitingSession,
)

# DPI in Russia stalls a TCP flow after about 16 KB: a longer body went past it.
MIN_BODY_BYTES = 16 * 1024


@define(eq=False)
class CurlSession(VisitingSession):
    session: AsyncSession

    async def load(self, site: Site) -> None:
        try:
            response = await self.session.get(site.url)
        except RequestsError as e:
            raise VisitFailed(str(e)) from e
        if not 200 <= response.status_code < 300:
            raise VisitFailed(f"HTTP {response.status_code} from {site.url}")
        if len(response.content) <= MIN_BODY_BYTES:
            raise VisitFailed(f"short body: {len(response.content)} bytes from {site.url}")


@frozen
class CurlBrowser(Browser):
    """The LIGHT check policy: plain requests through curl_cffi, which looks like
    Chrome to the network (TLS and HTTP/2 fingerprint) but runs no JavaScript and
    takes megabytes of memory, not hundreds. A page counts as opened on a 2xx
    answer longer than MIN_BODY_BYTES, heavy and target sites alike."""

    catalog: SiteCatalog
    backoff: Backoff = field(factory=Backoff)
    rng: random.Random = field(factory=random.Random, eq=False)
    impersonate: str = "chrome"

    @asynccontextmanager
    async def open(
        self, socks_port: int, socks_host: str = "127.0.0.1"
    ) -> AsyncGenerator[CurlSession]:
        # socks5h: names are resolved at the far end, as Chromium does with a SOCKS5 proxy.
        async with AsyncSession(
            proxy=f"socks5h://{socks_host}:{socks_port}",
            impersonate=self.impersonate,
            timeout=NAVIGATION_TIMEOUT_SECONDS,
        ) as session:
            yield CurlSession(self.catalog, self.backoff, self.rng, session)
