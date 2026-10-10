import hashlib
from urllib.parse import unquote, urlsplit

from attrs import frozen

from xray_tun_pwc.domain.sites import SiteVisit


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


@frozen
class TunnelCheck:
    tunnel: SocksTunnel
    visits: tuple[SiteVisit, ...]

    @property
    def success(self) -> bool:
        return self.tunnel.ok and any(v.ok for v in self.visits)

    @property
    def perfect(self) -> bool:
        return self.tunnel.ok and bool(self.visits) and all(v.ok for v in self.visits)

    def summary(self) -> str:
        code = self.tunnel.config.code
        if not self.tunnel.ok:
            return f"{code} - failed[tunnel: {self.tunnel.error}]"
        if self.perfect:
            return f"{code} - OK"
        problems = ", ".join(v.to_log() for v in self.visits if not v.ok)
        return f"{code} - {'partial' if self.success else 'failed'}[{problems}]"
