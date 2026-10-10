import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar, Literal, Self
from urllib.parse import urlsplit

from attrs import frozen

type LoadMilestone = Literal["commit", "domcontentloaded"]


@frozen
class Site(ABC):
    name: str
    url: str

    loaded_at: ClassVar[LoadMilestone]

    @classmethod
    def at(cls, url: str) -> Self:
        host = urlsplit(url).hostname
        if not host:
            raise ValueError(f"not a site URL: {url!r}")
        return cls(name=host.removeprefix("www."), url=url)

    @abstractmethod
    def accepts_status(self, status: int | None) -> bool: ...


@frozen
class HeavySite(Site):
    loaded_at = "domcontentloaded"

    def accepts_status(self, status: int | None) -> bool:
        return True


@frozen
class LightSite(Site):
    loaded_at = "commit"

    def accepts_status(self, status: int | None) -> bool:
        return status == 200


@frozen
class SiteCatalog:
    heavy: tuple[HeavySite, ...]
    targets: tuple[LightSite, ...]
    heavy_visits: int

    @classmethod
    def from_urls(
        cls, heavy: Sequence[str], targets: Sequence[str], heavy_visits: int
    ) -> SiteCatalog:
        if heavy_visits < 0:
            raise ValueError(f"heavy_visits must not be negative, got {heavy_visits}")
        if heavy_visits and not heavy:
            raise ValueError("heavy_visits is set, but the list of heavy sites is empty")
        return cls(
            heavy=tuple(HeavySite.at(url) for url in heavy),
            targets=tuple(LightSite.at(url) for url in targets),
            heavy_visits=heavy_visits,
        )


@frozen
class SiteVisitError(ABC):
    raw: str

    @classmethod
    def from_raw(cls, raw: str) -> SiteVisitError:
        kinds: tuple[type[SiteVisitError], ...] = (
            HttpStatus,
            TunnelDown,
            VisitTimeout,
            ConnectionReset,
            TlsFailure,
            DnsFailure,
        )
        return next((kind(raw) for kind in kinds if kind.matches(raw)), UnrecognizedError(raw))

    @classmethod
    @abstractmethod
    def matches(cls, raw: str) -> bool: ...

    @abstractmethod
    def to_log(self) -> str: ...


@frozen
class MarkedVisitError(SiteVisitError):
    markers: ClassVar[tuple[str, ...]]
    label: ClassVar[str]

    @classmethod
    def matches(cls, raw: str) -> bool:
        return any(marker in raw for marker in cls.markers)

    def to_log(self) -> str:
        return self.label


@frozen
class TunnelDown(MarkedVisitError):
    markers = ("ERR_PROXY_CONNECTION_FAILED", "ERR_SOCKS_CONNECTION_FAILED", "ERR_TUNNEL")
    label = "tunnel"


@frozen
class VisitTimeout(MarkedVisitError):
    markers = ("Timeout", "ERR_TIMED_OUT", "ERR_CONNECTION_TIMED_OUT")
    label = "timeout"


@frozen
class ConnectionReset(MarkedVisitError):
    markers = ("ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_EMPTY_RESPONSE")
    label = "reset"


@frozen
class TlsFailure(MarkedVisitError):
    markers = ("ERR_SSL", "ERR_CERT")
    label = "tls"


@frozen
class DnsFailure(MarkedVisitError):
    markers = ("ERR_NAME_NOT_RESOLVED",)
    label = "dns"


@frozen
class HttpStatus(SiteVisitError):
    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^HTTP (\d+|None)\b")

    @classmethod
    def matches(cls, raw: str) -> bool:
        return bool(cls.PATTERN.match(raw))

    def to_log(self) -> str:
        match = self.PATTERN.match(self.raw)
        return f"HTTP {match[1]}" if match else self.raw


@frozen
class UnrecognizedError(SiteVisitError):
    @classmethod
    def matches(cls, raw: str) -> bool:
        return True

    def to_log(self) -> str:
        return self.raw


@frozen
class SiteVisit:
    site: Site
    url: str
    ok: bool
    attempts: int
    error: SiteVisitError | None

    @classmethod
    def succeeded(cls, site: Site, url: str, attempts: int) -> SiteVisit:
        return cls(site, url, ok=True, attempts=attempts, error=None)

    @classmethod
    def failed(cls, site: Site, url: str, attempts: int, error: SiteVisitError) -> SiteVisit:
        return cls(site, url, ok=False, attempts=attempts, error=error)

    def to_log(self) -> str:
        return f"{self.site.name}: {self.error.to_log()}" if self.error else f"{self.site.name}: ok"
