import asyncio
import signal
import socket
from datetime import timedelta
from importlib.metadata import version as installed_version
from pathlib import Path

from attrs import define, field
from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from xray_pwc_probe.app import (
    MetricsServer,
    TunnelMetrics,
    XrayPwcConnectFailed,
    XrayPwcConnectUnavailable,
)
from xray_pwc_probe.app.check import CheckConfigsUsecase, CheckRequest
from xray_pwc_probe.app.xray_pwc_connect_version import (
    CheckXrayPwcConnectVersionRequest,
    CheckXrayPwcConnectVersionUsecase,
    XrayPwcConnectMismatch,
)
from xray_pwc_probe.domain.entities import TunnelCheck, VpnConfigLine
from xray_pwc_probe.domain.schedule import CheckSchedule
from xray_pwc_probe.domain.sites import SiteCatalog
from xray_pwc_probe.log import ERROR, INFO, log


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
    connect_bin: Path | None = None

    def get_version(self) -> str:
        return installed_version("xray-pwc-probe")

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
    check_xray_pwc_connect_version: CheckXrayPwcConnectVersionUsecase
    check_configs: CheckConfigsUsecase
    metrics: TunnelMetrics
    metrics_server: MetricsServer
    schedule: CheckSchedule
    stopped: asyncio.Event = field(factory=asyncio.Event, init=False)

    async def run(self) -> int:
        try:
            await self.check_xray_pwc_connect_version.handle(
                CheckXrayPwcConnectVersionRequest(self.settings.get_version())
            )
        except (XrayPwcConnectUnavailable, XrayPwcConnectMismatch) as e:
            log(ERROR, "connect_unavailable", err=e)
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
        try:
            checks = await self.check_configs.handle(CheckRequest(configs))
        except XrayPwcConnectFailed as e:
            # Not one link's fault: the round is skipped, the metrics keep the last one.
            log(ERROR, "round_failed", err=e)
            return ()
        self.metrics.record(checks)
        for index, check in enumerate(checks):
            log(check.log_level, "check", index=index, **check.log_fields())
        return checks

    def introduction(self, configs: tuple[VpnConfigLine, ...]) -> dict[str, str | int]:
        return {
            "version": self.settings.get_version(),
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
