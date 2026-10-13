from attrs import define, frozen

from xray_pwc_probe.app import AbstractUsecase, Browser, XrayPwcConnect
from xray_pwc_probe.domain.entities import SocksTunnel, TunnelCheck, VpnConfigLine


@frozen
class CheckRequest:
    configs: tuple[VpnConfigLine, ...]


@define(eq=False)
class CheckConfigsUsecase(AbstractUsecase[CheckRequest, tuple[TunnelCheck, ...]]):
    xray_pwc_connect: XrayPwcConnect
    browser: Browser

    async def handle(self, req: CheckRequest) -> tuple[TunnelCheck, ...]:
        async with self.xray_pwc_connect.start(req.configs) as group:
            return tuple([await self.check_tunnel(t, group.listen) for t in group.tunnels])

    async def check_tunnel(self, tunnel: SocksTunnel, listen: str) -> TunnelCheck:
        if not tunnel.ok:
            return TunnelCheck(tunnel, visits=())
        async with self.browser.open(tunnel.port, listen) as session:
            heavy = await session.check_heavy_sites()
            targets = await session.check_target_sites()
        return TunnelCheck(tunnel, visits=(*heavy, *targets))
