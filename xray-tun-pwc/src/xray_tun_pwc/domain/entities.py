import hashlib
from enum import Enum
from urllib.parse import unquote, urlsplit

from attrs import frozen

from xray_tun_pwc.domain.sites import SiteVisit

# The error kind of a link the tunnel could not start from; site errors have their own kinds.
LINK_BROKEN_KIND = "link_broken"


@frozen
class VpnConfigLine:
    value: str
    tag: str

    @classmethod
    def from_str(cls, raw: str) -> VpnConfigLine:
        line = raw.strip()
        if not line or line.startswith("#"):
            raise ValueError("Malformed line")
        return cls(value=line, tag=unquote(urlsplit(line).fragment))

    @property
    def stable_id(self) -> str:
        connection_parameters = self.value.split("#", 1)[0]
        return hashlib.sha256(connection_parameters.encode()).hexdigest()[:16]

    @property
    def code(self) -> str:
        return self.tag or self.stable_id


@frozen
class SocksTunnel:
    config: VpnConfigLine
    port: int
    protocol: str
    ok: bool
    error: str | None

    @classmethod
    def alive(cls, config: VpnConfigLine, port: int, protocol: str) -> SocksTunnel:
        return cls(config, port, protocol, ok=True, error=None)

    @classmethod
    def broken(
        cls, config: VpnConfigLine, port: int, error: str, protocol: str = ""
    ) -> SocksTunnel:
        return cls(config, port, protocol, ok=False, error=error)


@frozen
class TunnelGroup:
    listen: str
    tunnels: tuple[SocksTunnel, ...]


class Reach(Enum):
    LINK_BROKEN = (0, "link_broken")
    UNREACHABLE = (1, "unreachable")
    PARTIAL = (2, "partial")
    COMPLETE = (3, "ok")

    def __init__(self, rank: int, result: str) -> None:
        self.rank, self.result = rank, result

    @property
    def success(self) -> bool:
        return self.rank >= Reach.PARTIAL.rank

    @property
    def perfect(self) -> bool:
        return self is not Reach.PARTIAL


@frozen
class TunnelCheck:
    tunnel: SocksTunnel
    visits: tuple[SiteVisit, ...]

    @property
    def reach(self) -> Reach:
        if not self.tunnel.ok:
            return Reach.LINK_BROKEN
        if not any(visit.proves_connection for visit in self.visits):
            return Reach.UNREACHABLE
        if not all(visit.ok for visit in self.visits):
            return Reach.PARTIAL
        return Reach.COMPLETE

    @property
    def success(self) -> bool:
        return self.reach.success

    @property
    def perfect(self) -> bool:
        return self.reach.perfect

    @property
    def error_kinds(self) -> frozenset[str]:
        if not self.tunnel.ok:
            return frozenset({LINK_BROKEN_KIND})
        return frozenset(visit.error.kind for visit in self.visits if visit.error)

    @property
    def problems(self) -> tuple[str, ...]:
        if not self.tunnel.ok:
            return (f"tunnel: {self.tunnel.error}",)
        return tuple(visit.to_log() for visit in self.visits if not visit.ok)

    @property
    def log_level(self) -> str:
        if self.reach is Reach.COMPLETE:
            return "INFO"
        return "WARN" if self.reach is Reach.PARTIAL else "ERROR"

    def log_fields(self) -> dict[str, str | int]:
        fields: dict[str, str | int] = {
            "name": self.tunnel.config.code,
            "result": self.reach.result,
            "stage": self.reach.rank,
        }
        if self.problems:
            fields["failed"] = ", ".join(self.problems)
        return fields
