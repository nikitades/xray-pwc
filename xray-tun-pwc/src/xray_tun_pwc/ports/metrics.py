from collections.abc import Sequence

from attrs import Factory, field, frozen
from prometheus_client import CollectorRegistry, Gauge, start_http_server

from xray_tun_pwc.app import MetricsServer, TunnelMetrics
from xray_tun_pwc.domain.entities import TunnelCheck


@frozen
class PrometheusMetrics(MetricsServer, TunnelMetrics):
    port: int
    instance: str
    host: str = "0.0.0.0"
    registry: CollectorRegistry = field(factory=CollectorRegistry, eq=False)
    tunnel_up: Gauge = field(
        init=False,
        eq=False,
        default=Factory(
            lambda self: Gauge(
                "xrc_tunnel_up",
                "1 when at least one site opened through the tunnel; "
                'perfect="true" when every site opened',
                ["instance", "tunnel", "stable_id", "perfect"],
                registry=self.registry,
            ),
            takes_self=True,
        ),
    )

    def start_serving(self) -> None:
        start_http_server(self.port, self.host, registry=self.registry)

    def record(self, checks: Sequence[TunnelCheck]) -> None:
        self.tunnel_up.clear()
        for check in checks:
            config = check.tunnel.config
            perfect = "true" if check.perfect else "false"
            self.tunnel_up.labels(self.instance, config.code, config.stable_id, perfect).set(
                int(check.success)
            )
