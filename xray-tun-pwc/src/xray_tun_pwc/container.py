from lagom import Container

from xray_tun_pwc.app import Browser, MetricsServer, TunnelMetrics, XrayTunConnect
from xray_tun_pwc.domain.schedule import CheckSchedule
from xray_tun_pwc.entrypoints.cli import Settings
from xray_tun_pwc.fakes import FakeBrowser, FakeMetrics, FakeXrayTunConnect
from xray_tun_pwc.ports.browser import PlaywrightBrowser
from xray_tun_pwc.ports.metrics import PrometheusMetrics
from xray_tun_pwc.ports.xray_tun_connect import XrayTunConnectCli


def build_container(settings: Settings) -> Container:
    container = Container()
    container[Settings] = settings
    container[CheckSchedule] = CheckSchedule(settings.check_interval, settings.check_jitter)

    container[XrayTunConnect] = XrayTunConnectCli(binary=settings.xray_tun_connect_bin)
    container[Browser] = PlaywrightBrowser(catalog=settings.get_site_catalog())

    metrics = PrometheusMetrics(port=settings.http_port, instance=settings.instance)
    container[TunnelMetrics] = metrics
    container[MetricsServer] = metrics

    return container


def build_test_container(settings: Settings) -> Container:
    container = Container()
    container[Settings] = settings
    container[CheckSchedule] = CheckSchedule(settings.check_interval, settings.check_jitter)

    container[XrayTunConnect] = FakeXrayTunConnect()
    container[Browser] = FakeBrowser(catalog=settings.get_site_catalog())

    metrics = FakeMetrics()
    container[TunnelMetrics] = metrics
    container[MetricsServer] = metrics

    return container.clone()
