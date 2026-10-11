import pytest
from attrs import frozen

from xray_tun_pwc.domain.entities import SocksTunnel, TunnelCheck, VpnConfigLine
from xray_tun_pwc.domain.sites import HeavySite, SiteVisit, SiteVisitError
from xray_tun_pwc.ports.metrics import PrometheusMetrics

WIKIPEDIA = HeavySite.at("https://en.wikipedia.org/wiki/Special:Random")
MSK = VpnConfigLine.from_str("vless://u@msk.example:443#msk")
SPB = VpnConfigLine.from_str("trojan://p@spb.example:443#spb")
OPENED = SiteVisit.succeeded(WIKIPEDIA, WIKIPEDIA.url, attempts=1)
TIMED_OUT = SiteVisit.failed(
    WIKIPEDIA, WIKIPEDIA.url, attempts=3, error=SiteVisitError.from_raw("net::ERR_TIMED_OUT")
)
MSK_HEALTHY = TunnelCheck(SocksTunnel.alive(MSK, 10000, "vless"), (OPENED,))
SPB_HEALTHY = TunnelCheck(SocksTunnel.alive(SPB, 10001, "trojan"), (OPENED,))
SPB_FAILING = TunnelCheck(SocksTunnel.alive(SPB, 10001, "trojan"), (TIMED_OUT,))
SPB_PARTIAL = TunnelCheck(SocksTunnel.alive(SPB, 10001, "trojan"), (OPENED, TIMED_OUT))
RATE_LIMITED = SiteVisit.failed(
    WIKIPEDIA, WIKIPEDIA.url, attempts=3, error=SiteVisitError.from_raw("HTTP 429 from x")
)
SPB_ANSWERED_BUT_NOTHING_OPENED = TunnelCheck(
    SocksTunnel.alive(SPB, 10001, "trojan"), (RATE_LIMITED, RATE_LIMITED)
)


BROKEN = TunnelCheck(SocksTunnel.broken(SPB, 10001, "unsupported share format"), ())


@frozen
class Scrape:
    metrics: PrometheusMetrics

    def stage(self, config: VpnConfigLine) -> float | None:
        labels = {"instance": self.metrics.instance, "tunnel": config.code}
        return self.metrics.registry.get_sample_value(
            "pwc_tunnel_stage", labels | {"stable_id": config.stable_id}
        )

    def errors(self, config: VpnConfigLine) -> dict[str, float]:
        return {
            sample.labels["error_type"]: sample.value
            for family in self.metrics.registry.collect()
            for sample in family.samples
            if sample.name == "pwc_tunnel_error"
            and sample.labels["tunnel"] == config.code
            and sample.labels["stable_id"] == config.stable_id
            and sample.labels["instance"] == self.metrics.instance
        }


@pytest.mark.parametrize(
    ("check", "stage"),
    [
        (BROKEN, 0),
        (SPB_FAILING, 1),
        (SPB_PARTIAL, 2),
        (SPB_ANSWERED_BUT_NOTHING_OPENED, 2),
        (SPB_HEALTHY, 3),
    ],
    ids=["link broken", "unreachable", "partial", "answered only", "complete"],
)
def test_stage_is_exported_as_its_rank(check: TunnelCheck, stage: int):
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([check])
    assert Scrape(metrics).stage(SPB) == stage


def test_a_tunnel_keeps_one_series_when_its_stage_changes():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_HEALTHY])
    metrics.record([SPB_PARTIAL])
    samples = [
        sample
        for family in metrics.registry.collect()
        for sample in family.samples
        if sample.name == "pwc_tunnel_stage"
    ]
    assert len(samples) == 1
    assert samples[0].value == 2


def test_tunnels_removed_from_the_config_disappear():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([MSK_HEALTHY, SPB_HEALTHY])
    metrics.record([MSK_HEALTHY])
    assert Scrape(metrics).stage(MSK) == 3
    assert Scrape(metrics).stage(SPB) is None


RESET = SiteVisit.failed(
    WIKIPEDIA,
    WIKIPEDIA.url,
    attempts=3,
    error=SiteVisitError.from_raw("net::ERR_CONNECTION_RESET at x"),
)
SPB_RESET = TunnelCheck(SocksTunnel.alive(SPB, 10001, "trojan"), (OPENED, RESET, TIMED_OUT))


def test_a_healthy_tunnel_has_no_error_series():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_HEALTHY])
    assert Scrape(metrics).errors(SPB) == {}


def test_every_error_kind_of_the_last_check_is_one():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_RESET])
    assert Scrape(metrics).errors(SPB) == {"ERR_CONNECTION_RESET": 1, "ERR_TIMED_OUT": 1}


def test_a_gone_error_drops_to_zero():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_RESET])
    metrics.record([SPB_FAILING])
    assert Scrape(metrics).errors(SPB) == {"ERR_CONNECTION_RESET": 0, "ERR_TIMED_OUT": 1}
    metrics.record([SPB_HEALTHY])
    assert Scrape(metrics).errors(SPB) == {"ERR_CONNECTION_RESET": 0, "ERR_TIMED_OUT": 0}


def test_a_broken_link_reports_link_broken():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([BROKEN])
    assert Scrape(metrics).errors(SPB) == {"link_broken": 1}


def test_errors_of_tunnels_removed_from_the_config_disappear():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([MSK_HEALTHY, SPB_RESET])
    metrics.record([MSK_HEALTHY])
    assert Scrape(metrics).errors(SPB) == {}
    metrics.record([MSK_HEALTHY, SPB_HEALTHY])
    assert Scrape(metrics).errors(SPB) == {}
