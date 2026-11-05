import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

from attrs import field, frozen

type LoadMilestone = Literal["commit", "domcontentloaded"]


class CheckPolicy(StrEnum):
    """How a probe loads pages. HEAVY: Chromium, pages as a person sees them.
    LIGHT: plain HTTP requests with Chrome's TLS and HTTP/2 fingerprint, no
    JavaScript, for hosts without the memory for a browser."""

    HEAVY = "heavy"
    LIGHT = "light"


@frozen
class Site(ABC):
    name: str
    url: str

    loaded_at: ClassVar[LoadMilestone]

    @classmethod
    def at(cls, url: str, **fields: Any) -> Self:
        host = urlsplit(url).hostname
        if not host:
            raise ValueError(f"not a site URL: {url!r}")
        return cls(name=host.removeprefix("www."), url=url, **fields)

    @abstractmethod
    def accepts_status(self, status: int | None) -> bool: ...


@frozen
class HeavySite(Site):
    """A site to warm a tunnel up with: a lot of bytes, any answer. Its tags are
    the check policies that can load it."""

    tags: frozenset[CheckPolicy] = field(default=frozenset(), converter=frozenset)
    loaded_at = "domcontentloaded"

    def accepts_status(self, status: int | None) -> bool:
        return True


@frozen
class TargetSite(Site):
    loaded_at = "commit"

    def accepts_status(self, status: int | None) -> bool:
        return status == 200


@frozen
class SiteCatalog:
    heavy: tuple[HeavySite, ...]
    targets: tuple[TargetSite, ...]
    heavy_visits: int

    @classmethod
    def from_urls(
        cls,
        heavy_real: Sequence[str],
        heavy_cffi: Sequence[str],
        targets: Sequence[str],
        heavy_visits: int,
    ) -> SiteCatalog:
        """One list of heavy sites from two: a URL of heavy_real can be loaded by
        Chromium (HEAVY), one of heavy_cffi without JavaScript (LIGHT). A URL in
        both, character for character, is one site that both can load."""
        if heavy_visits < 0:
            raise ValueError(f"heavy_visits must not be negative, got {heavy_visits}")
        tags: dict[str, set[CheckPolicy]] = {}
        for urls, policy in ((heavy_real, CheckPolicy.HEAVY), (heavy_cffi, CheckPolicy.LIGHT)):
            for url in urls:
                tags.setdefault(url, set()).add(policy)
        return cls(
            heavy=tuple(HeavySite.at(url, tags=policies) for url, policies in tags.items()),
            targets=tuple(TargetSite.at(url) for url in targets),
            heavy_visits=heavy_visits,
        )

    def for_policy(self, policy: CheckPolicy) -> SiteCatalog:
        """The catalog one check policy works with: the heavy sites it can load.
        Target sites are for every policy."""
        heavy = tuple(site for site in self.heavy if policy in site.tags)
        if self.heavy_visits and not heavy:
            raise ValueError(
                f"check policy {policy} has no heavy sites, but heavy_visits is {self.heavy_visits}"
            )
        return SiteCatalog(heavy=heavy, targets=self.targets, heavy_visits=self.heavy_visits)


@frozen
class SiteVisitError(ABC):
    raw: str

    @classmethod
    def from_raw(cls, raw: str) -> SiteVisitError:
        kinds: tuple[type[SiteVisitError], ...] = (
            HttpStatus,
            ShortBody,
            NetError,
            CurlFailure,
            VisitTimeout,
        )
        return next((kind(raw) for kind in kinds if kind.matches(raw)), UnrecognizedError(raw))

    @classmethod
    @abstractmethod
    def matches(cls, raw: str) -> bool: ...

    @property
    @abstractmethod
    def kind(self) -> str:
        """A short name of the error, the same for every error of this kind."""

    @property
    @abstractmethod
    def proves_connection(self) -> bool: ...

    def to_log(self) -> str:
        return self.kind


@frozen
class NetError(SiteVisitError):
    """A Chromium network error. Every code is its own kind: ERR_CONNECTION_RESET,
    ERR_CONNECTION_CLOSED and ERR_EMPTY_RESPONSE are three different errors."""

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\bnet::(ERR_[A-Z0-9_]+)")
    # A failed TLS handshake or a bad certificate means the site itself answered.
    PROVING_PREFIXES: ClassVar[tuple[str, ...]] = ("ERR_SSL_", "ERR_CERT_")

    @classmethod
    def matches(cls, raw: str) -> bool:
        return bool(cls.PATTERN.search(raw))

    @property
    def kind(self) -> str:
        match = self.PATTERN.search(self.raw)
        return match[1] if match else self.raw

    @property
    def proves_connection(self) -> bool:
        return self.kind.startswith(self.PROVING_PREFIXES)


@frozen
class CurlFailure(SiteVisitError):
    """A libcurl error of a LIGHT check, by its code. A timeout is the same kind
    as Playwright's."""

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\bcurl: \((\d+)\)")
    NAMES: ClassVar[dict[int, str]] = {
        5: "CURLE_COULDNT_RESOLVE_PROXY",
        6: "CURLE_COULDNT_RESOLVE_HOST",
        7: "CURLE_COULDNT_CONNECT",
        28: "timeout",
        35: "CURLE_SSL_CONNECT_ERROR",
        52: "CURLE_GOT_NOTHING",
        55: "CURLE_SEND_ERROR",
        56: "CURLE_RECV_ERROR",
        60: "CURLE_PEER_FAILED_VERIFICATION",
        92: "CURLE_HTTP2_STREAM",
        97: "CURLE_PROXY",
    }
    # As with Chromium: a failed TLS handshake or a bad certificate came from the site.
    PROVING: ClassVar[frozenset[int]] = frozenset({35, 60})

    @classmethod
    def matches(cls, raw: str) -> bool:
        return bool(cls.PATTERN.search(raw))

    @property
    def code(self) -> int:
        match = self.PATTERN.search(self.raw)
        return int(match[1]) if match else -1

    @property
    def kind(self) -> str:
        return self.NAMES.get(self.code, f"CURLE_{self.code}")

    @property
    def proves_connection(self) -> bool:
        return self.code in self.PROVING


@frozen
class VisitTimeout(SiteVisitError):
    """Playwright gave up waiting for the page, Chromium reported no error."""

    @classmethod
    def matches(cls, raw: str) -> bool:
        return "Timeout" in raw

    @property
    def kind(self) -> str:
        return "timeout"

    @property
    def proves_connection(self) -> bool:
        return False


@frozen
class HttpStatus(SiteVisitError):
    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^HTTP (\d+|None)\b")

    @classmethod
    def matches(cls, raw: str) -> bool:
        return bool(cls.PATTERN.match(raw))

    @property
    def kind(self) -> str:
        match = self.PATTERN.match(self.raw)
        return f"HTTP {match[1]}" if match else self.raw

    @property
    def proves_connection(self) -> bool:
        return True


@frozen
class ShortBody(SiteVisitError):
    """A LIGHT check got an answer of too few bytes to have passed the point
    where DPI stalls a flow."""

    @classmethod
    def matches(cls, raw: str) -> bool:
        return raw.startswith("short body")

    @property
    def kind(self) -> str:
        return "short_body"

    @property
    def proves_connection(self) -> bool:
        return True


@frozen
class UnrecognizedError(SiteVisitError):
    @classmethod
    def matches(cls, raw: str) -> bool:
        return True

    @property
    def kind(self) -> str:
        return "unrecognized"

    @property
    def proves_connection(self) -> bool:
        return False

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

    @property
    def proves_connection(self) -> bool:
        return self.error is None or self.error.proves_connection

    def to_log(self) -> str:
        return f"{self.site.name}: {self.error.to_log()}" if self.error else f"{self.site.name}: ok"
