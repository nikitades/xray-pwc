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


@frozen
class Scrape:
    metrics: PrometheusMetrics

    def tunnel_up(self, config: VpnConfigLine) -> dict[str, float]:
        return {
            sample.labels["perfect"]: sample.value
            for family in self.metrics.registry.collect()
            for sample in family.samples
            if sample.name == "pwc_tunnel_up"
            and sample.labels["instance"] == self.metrics.instance
            and sample.labels["tunnel"] == config.code
            and sample.labels["stable_id"] == config.stable_id
        }


def test_healthy_tunnel_is_up_and_perfect():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([MSK_HEALTHY])
    assert Scrape(metrics).tunnel_up(MSK) == {"true": 1}


def test_failing_tunnel_is_down_and_not_perfect():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_FAILING])
    assert Scrape(metrics).tunnel_up(SPB) == {"false": 0}


def test_partial_tunnel_is_up_but_not_perfect():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_PARTIAL])
    assert Scrape(metrics).tunnel_up(SPB) == {"false": 1}


def test_connected_tunnel_is_up_even_when_no_page_opened():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_ANSWERED_BUT_NOTHING_OPENED])
    assert Scrape(metrics).tunnel_up(SPB) == {"false": 1}


def test_a_tunnel_has_one_series_even_when_perfect_flips():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([SPB_HEALTHY])
    metrics.record([SPB_PARTIAL])
    assert Scrape(metrics).tunnel_up(SPB) == {"false": 1}


def test_tunnels_removed_from_the_config_disappear():
    metrics = PrometheusMetrics(port=0, instance="probe-1")
    metrics.record([MSK_HEALTHY, SPB_HEALTHY])
    metrics.record([MSK_HEALTHY])
    assert Scrape(metrics).tunnel_up(SPB) == {}
