from attrs import define, frozen

from xray_tun_pwc.app import AbstractUsecase, Browser, XrayTunConnect
from xray_tun_pwc.domain.entities import SocksTunnel, TunnelCheck, VpnConfigLine


@frozen
class CheckRequest:
    configs: tuple[VpnConfigLine, ...]


@define(eq=False)
class CheckConfigsUsecase(AbstractUsecase[CheckRequest, tuple[TunnelCheck, ...]]):
    xray_tun_connect: XrayTunConnect
    browser: Browser

    async def handle(self, req: CheckRequest) -> tuple[TunnelCheck, ...]:
        async with self.xray_tun_connect.start(req.configs) as group:
            return tuple([await self.check_tunnel(t, group.listen) for t in group.tunnels])

    async def check_tunnel(self, tunnel: SocksTunnel, listen: str) -> TunnelCheck:
        if not tunnel.ok:
            return TunnelCheck(tunnel, visits=())
        async with self.browser.open(tunnel.port, listen) as session:
            heavy = await session.check_heavy_sites()
            targets = await session.check_target_sites()
        return TunnelCheck(tunnel, visits=(*heavy, *targets))
