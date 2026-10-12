import socket
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from attrs import define, field, frozen
from packaging.version import Version

from xray_tun_pwc.app import (
    DEFAULT_TUNNEL_OPTIONS,
    Browser,
    BrowsingSession,
    MetricsServer,
    TunnelMetrics,
    TunnelOptions,
    XrayTunConnect,
)
from xray_tun_pwc.domain.entities import SocksTunnel, TunnelCheck, TunnelGroup, VpnConfigLine
from xray_tun_pwc.domain.sites import Site, SiteCatalog, SiteVisit, SiteVisitError


@define
class FakeXrayTunConnect(XrayTunConnect):
    reported_version: str = "0.5.0"
    broken_tags: frozenset[str] = frozenset()
    started_with: tuple[VpnConfigLine, ...] = ()
    running: bool = False

    async def version(self) -> Version:
        return Version(self.reported_version)

    @asynccontextmanager
    async def start(
        self, configs: Sequence[VpnConfigLine], options: TunnelOptions = DEFAULT_TUNNEL_OPTIONS
    ) -> AsyncGenerator[TunnelGroup]:
        self.started_with, self.running = tuple(configs), True
        try:
            yield TunnelGroup(
                listen=options.listen,
                tunnels=tuple(
                    self.tunnel_for(config, options.start_port + index)
                    for index, config in enumerate(configs)
                ),
            )
        finally:
            self.running = False

    def tunnel_for(self, config: VpnConfigLine, port: int) -> SocksTunnel:
        if config.tag in self.broken_tags:
            return SocksTunnel.broken(config, port, error="unsupported share format")
        return SocksTunnel.alive(config, port, protocol="vless")


@define
class FakeBrowsingSession(BrowsingSession):
    catalog: SiteCatalog
    failing_sites: frozenset[str]

    async def check_heavy_sites(self) -> tuple[SiteVisit, ...]:
        heavy = self.catalog.heavy
        return tuple(self.visit(heavy[i % len(heavy)]) for i in range(self.catalog.heavy_visits))

    async def check_target_sites(self) -> tuple[SiteVisit, ...]:
        return tuple(self.visit(site) for site in self.catalog.targets)

    def visit(self, site: Site) -> SiteVisit:
        if site.name in self.failing_sites:
            return SiteVisit.failed(
                site, site.url, attempts=3, error=SiteVisitError.from_raw("net::ERR_TIMED_OUT")
            )
        return SiteVisit.succeeded(site, site.url, attempts=1)


@define
class FakeBrowser(Browser):
    catalog: SiteCatalog
    failing_sites: frozenset[str] = frozenset()
    opened_ports: list[int] = field(factory=list)

    @asynccontextmanager
    async def open(
        self, socks_port: int, socks_host: str = "127.0.0.1"
    ) -> AsyncGenerator[FakeBrowsingSession]:
        self.opened_ports.append(socks_port)
        yield FakeBrowsingSession(self.catalog, self.failing_sites)


@frozen
class LocalPort:
    number: int

    @classmethod
    def unused(cls) -> LocalPort:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return cls(probe.getsockname()[1])

    def accepts_connections(self) -> bool:
        with socket.socket() as client:
            client.settimeout(1)
            return client.connect_ex(("127.0.0.1", self.number)) == 0


@define
class FakeMetrics(MetricsServer, TunnelMetrics):
    serving: bool = False
    recorded: list[tuple[TunnelCheck, ...]] = field(factory=list)

    def start_serving(self) -> None:
        self.serving = True

    def record(self, checks: Sequence[TunnelCheck]) -> None:
        self.recorded.append(tuple(checks))
