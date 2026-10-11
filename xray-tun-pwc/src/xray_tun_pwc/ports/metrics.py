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
    tunnel_stage: Gauge = field(
        init=False,
        eq=False,
        default=Factory(
            lambda self: Gauge(
                "pwc_tunnel_stage",
                "How far the last check got: 0 link broken, 1 unreachable, 2 partial, 3 complete",
                ["instance", "tunnel", "stable_id"],
                registry=self.registry,
            ),
            takes_self=True,
        ),
    )

    tunnel_error: Gauge = field(
        init=False,
        eq=False,
        default=Factory(
            lambda self: Gauge(
                "pwc_tunnel_error",
                "1 if the last check met this kind of error, 0 if an earlier check did",
                ["instance", "tunnel", "stable_id", "error_type"],
                registry=self.registry,
            ),
            takes_self=True,
        ),
    )
    # Error kinds seen per tunnel since start, so a gone error drops to 0, not out of sight.
    seen_errors: dict[tuple[str, str], frozenset[str]] = field(init=False, eq=False, factory=dict)

    def start_serving(self) -> None:
        start_http_server(self.port, self.host, registry=self.registry)

    def record(self, checks: Sequence[TunnelCheck]) -> None:
        self.tunnel_stage.clear()
        for check in checks:
            config = check.tunnel.config
            self.tunnel_stage.labels(self.instance, config.code, config.stable_id).set(
                check.reach.rank
            )
        self.record_errors(checks)

    def record_errors(self, checks: Sequence[TunnelCheck]) -> None:
        self.tunnel_error.clear()
        seen: dict[tuple[str, str], frozenset[str]] = {}
        for check in checks:
            config = check.tunnel.config
            key = (config.code, config.stable_id)
            current = check.error_kinds
            seen[key] = self.seen_errors.get(key, frozenset()) | current
            for kind in seen[key]:
                self.tunnel_error.labels(self.instance, *key, kind).set(int(kind in current))
        # Tunnels removed from the config forget their errors.
        self.seen_errors.clear()
        self.seen_errors.update(seen)
