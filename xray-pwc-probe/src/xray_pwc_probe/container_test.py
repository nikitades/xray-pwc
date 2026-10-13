from pathlib import Path

from xray_pwc_probe.app import Browser, MetricsServer, TunnelMetrics, XrayPwcConnect
from xray_pwc_probe.app.xray_pwc_connect_version import CheckXrayPwcConnectVersionUsecase
from xray_pwc_probe.container import build_container, build_test_container
from xray_pwc_probe.domain.schedule import CheckSchedule
from xray_pwc_probe.entrypoints.cli import CliApp, Settings
from xray_pwc_probe.fakes import FakeBrowser, FakeMetrics, FakeXrayPwcConnect
from xray_pwc_probe.ports.browser import PlaywrightBrowser
from xray_pwc_probe.ports.metrics import PrometheusMetrics
from xray_pwc_probe.ports.xray_pwc_connect import XrayPwcConnectCli

BINARY = Path("/opt/bin/xray-pwc-connect")


def test_production_container_uses_real_adapters_configured_from_settings():
    container = build_container(Settings(connect_bin=BINARY, http_port=9321, instance="probe-1"))
    assert container[XrayPwcConnect] == XrayPwcConnectCli(binary=BINARY)
    assert isinstance(container[Browser], PlaywrightBrowser)
    metrics = container[TunnelMetrics]
    assert isinstance(metrics, PrometheusMetrics)
    assert (metrics.port, metrics.instance) == (9321, "probe-1")


def test_metrics_are_recorded_and_served_by_the_same_object():
    container = build_container(Settings())
    assert container[TunnelMetrics] is container[MetricsServer]


def test_schedule_comes_from_settings():
    container = build_container(Settings())
    schedule = container[CheckSchedule]
    assert (schedule.interval.total_seconds(), schedule.jitter.total_seconds()) == (300, 120)


def test_cli_app_is_assembled_from_the_production_container():
    settings = Settings(connect_bin=BINARY)
    app = build_container(settings)[CliApp]
    assert app.settings is settings
    assert isinstance(app.check_xray_pwc_connect_version, CheckXrayPwcConnectVersionUsecase)
    assert app.check_xray_pwc_connect_version.xray_pwc_connect == XrayPwcConnectCli(binary=BINARY)


def test_test_container_swaps_every_adapter_for_a_fake():
    container = build_test_container(Settings())
    assert isinstance(container[XrayPwcConnect], FakeXrayPwcConnect)
    assert isinstance(container[Browser], FakeBrowser)
    assert isinstance(container[TunnelMetrics], FakeMetrics)
    assert container[TunnelMetrics] is container[MetricsServer]
