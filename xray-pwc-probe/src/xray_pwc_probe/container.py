from lagom import Container

from xray_pwc_probe.app import Browser, MetricsServer, TunnelMetrics, XrayPwcConnect
from xray_pwc_probe.domain.schedule import CheckSchedule
from xray_pwc_probe.entrypoints.cli import Settings
from xray_pwc_probe.fakes import FakeBrowser, FakeMetrics, FakeXrayPwcConnect
from xray_pwc_probe.ports.browser import PlaywrightBrowser
from xray_pwc_probe.ports.metrics import PrometheusMetrics
from xray_pwc_probe.ports.xray_pwc_connect import XrayPwcConnectCli


def build_container(settings: Settings) -> Container:
    container = Container()
    container[Settings] = settings
    container[CheckSchedule] = CheckSchedule(settings.check_interval, settings.check_jitter)

    container[XrayPwcConnect] = XrayPwcConnectCli(binary=settings.connect_bin)
    container[Browser] = PlaywrightBrowser(catalog=settings.get_site_catalog())

    metrics = PrometheusMetrics(port=settings.http_port, instance=settings.instance)
    container[TunnelMetrics] = metrics
    container[MetricsServer] = metrics

    return container


def build_test_container(settings: Settings) -> Container:
    container = Container()
    container[Settings] = settings
    container[CheckSchedule] = CheckSchedule(settings.check_interval, settings.check_jitter)

    container[XrayPwcConnect] = FakeXrayPwcConnect()
    container[Browser] = FakeBrowser(catalog=settings.get_site_catalog())

    metrics = FakeMetrics()
    container[TunnelMetrics] = metrics
    container[MetricsServer] = metrics

    return container.clone()
