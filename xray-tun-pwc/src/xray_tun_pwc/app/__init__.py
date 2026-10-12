from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from attrs import frozen
from packaging.version import Version

from xray_tun_pwc.domain.entities import TunnelCheck, TunnelGroup, VpnConfigLine
from xray_tun_pwc.domain.sites import SiteVisit


class AbstractUsecase[TReq, TRes](ABC):
    @abstractmethod
    async def handle(self, req: TReq) -> TRes: ...


class MetricsServer(Protocol):
    def start_serving(self) -> None: ...


class TunnelMetrics(Protocol):
    def record(self, checks: Sequence[TunnelCheck]) -> None: ...


class XrayTunConnectUnavailable(Exception):
    pass


class XrayTunConnectFailed(Exception):
    pass


class XrayLogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    NONE = "none"


@frozen
class TunnelOptions:
    start_port: int = 10000
    listen: str = "127.0.0.1"
    # xray-core's own lines are not in the log format; its warnings include a
    # "started" line on every check.
    xray_log_level: XrayLogLevel = XrayLogLevel.ERROR
    ready_timeout: timedelta = timedelta(seconds=10)


DEFAULT_TUNNEL_OPTIONS = TunnelOptions()


class XrayTunConnect(Protocol):
    async def version(self) -> Version: ...

    def start(
        self, configs: Sequence[VpnConfigLine], options: TunnelOptions = DEFAULT_TUNNEL_OPTIONS
    ) -> AbstractAsyncContextManager[TunnelGroup]: ...


class BrowsingSession(Protocol):
    async def check_heavy_sites(self) -> tuple[SiteVisit, ...]: ...

    async def check_target_sites(self) -> tuple[SiteVisit, ...]: ...


class Browser(Protocol):
    def open(
        self, socks_port: int, socks_host: str = "127.0.0.1"
    ) -> AbstractAsyncContextManager[BrowsingSession]: ...
