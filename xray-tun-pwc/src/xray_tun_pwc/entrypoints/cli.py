import asyncio
import signal
import socket
from datetime import timedelta
from importlib.metadata import version as installed_version
from pathlib import Path

from attrs import define, field
from packaging.version import Version
from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from xray_tun_pwc.app import MetricsServer, TunnelMetrics, XrayTunConnectUnavailable
from xray_tun_pwc.app.check import CheckConfigsUsecase, CheckRequest
from xray_tun_pwc.app.xray_tun_connect_version import (
    CheckXrayTunConnectVersionRequest,
    CheckXrayTunConnectVersionUsecase,
    XrayTunConnectTooOld,
)
from xray_tun_pwc.domain.entities import TunnelCheck, VpnConfigLine
from xray_tun_pwc.domain.schedule import CheckSchedule
from xray_tun_pwc.domain.sites import SiteCatalog
from xray_tun_pwc.log import ERROR, INFO, log


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PWC_", frozen=True)

    config_path: Path = Path("config.txt")
    config_lines: tuple[VpnConfigLine, ...] = ()
    instance: str = Field(default_factory=socket.gethostname)
    http_port: int = 8000
    check_interval: timedelta = timedelta(minutes=5)
    check_jitter: timedelta = timedelta(minutes=2)
    heavy_sites: tuple[HttpUrl, ...] = (
        HttpUrl("https://en.wikipedia.org/wiki/Special:Random"),
        HttpUrl("https://www.thesun.co.uk/"),
        HttpUrl("https://www.site123.com/website-examples"),
    )
    heavy_visits: int = Field(default=3, ge=0)
    target_sites: tuple[HttpUrl, ...] = (
        HttpUrl("https://www.instagram.com/"),
        HttpUrl("https://www.facebook.com/"),
    )
    xray_tun_connect_bin: Path | None = None

    def get_min_xray_tun_connect_version(self) -> Version:
        return Version("0.5.0")

    def get_site_catalog(self) -> SiteCatalog:
        return SiteCatalog.from_urls(
            heavy=[str(url) for url in self.heavy_sites],
            targets=[str(url) for url in self.target_sites],
            heavy_visits=self.heavy_visits,
        )

    def get_config(self) -> tuple[VpnConfigLine, ...]:
        if self.config_lines:
            return self.config_lines

        if not self.config_path.is_file():
            raise FileNotFoundError(f"config file not found: {self.config_path.resolve()}")

        lines = tuple(
            VpnConfigLine.from_str(raw)
            for raw in self.config_path.read_text(encoding="utf-8").splitlines()
            if raw.strip() and not raw.lstrip().startswith("#")
        )
        if not lines:
            raise ValueError(f"no valid links in {self.config_path.resolve()}")
        return lines


@define(eq=False)
class CliApp:
    settings: Settings
    check_xray_tun_connect_version: CheckXrayTunConnectVersionUsecase
    check_configs: CheckConfigsUsecase
    metrics: TunnelMetrics
    metrics_server: MetricsServer
    schedule: CheckSchedule
    stopped: asyncio.Event = field(factory=asyncio.Event, init=False)

    async def run(self) -> int:
        try:
            await self.check_xray_tun_connect_version.handle(
                CheckXrayTunConnectVersionRequest(self.settings.get_min_xray_tun_connect_version())
            )
        except (XrayTunConnectUnavailable, XrayTunConnectTooOld) as e:
            log(ERROR, "xtc_unavailable", err=e)
            return 1

        configs = self.settings.get_config()
        self.metrics_server.start_serving()
        log(INFO, "start", **self.introduction(configs))

        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stop_signal, self.stop)
        try:
            while not self.stopped.is_set():
                await self.run_once(configs)
                await self.wait(self.schedule.next_delay())
        finally:
            for stop_signal in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(stop_signal)
        return 0

    async def run_once(self, configs: tuple[VpnConfigLine, ...]) -> tuple[TunnelCheck, ...]:
        checks = await self.check_configs.handle(CheckRequest(configs))
        self.metrics.record(checks)
        for index, check in enumerate(checks):
            log(check.log_level, "check", index=index, **check.log_fields())
        return checks

    def introduction(self, configs: tuple[VpnConfigLine, ...]) -> dict[str, str | int]:
        return {
            "version": installed_version("xray-tun-pwc"),
            "instance": self.settings.instance,
            "links": len(configs),
            "interval": self.schedule.interval_iso(),
            "jitter": self.schedule.jitter_iso(),
        }

    def stop(self) -> None:
        self.stopped.set()

    async def wait(self, delay: timedelta) -> None:
        try:
            await asyncio.wait_for(self.stopped.wait(), delay.total_seconds())
        except TimeoutError:
            pass
