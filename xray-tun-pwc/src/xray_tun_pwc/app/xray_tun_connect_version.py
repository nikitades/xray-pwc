from attrs import define, frozen
from packaging.version import Version

from xray_tun_pwc.app import AbstractUsecase, XrayTunConnect


class XrayTunConnectTooOld(Exception):
    def __init__(self, actual: Version, minimum: Version) -> None:
        self.actual, self.minimum = actual, minimum
        super().__init__(f"xray-tun-connect {actual} is older than the required {minimum}")


@frozen
class CheckXrayTunConnectVersionRequest:
    min_version: Version


@define(eq=False)
class CheckXrayTunConnectVersionUsecase(
    AbstractUsecase[CheckXrayTunConnectVersionRequest, Version]
):
    xray_tun_connect: XrayTunConnect

    async def handle(self, req: CheckXrayTunConnectVersionRequest) -> Version:
        actual = await self.xray_tun_connect.version()
        if actual < req.min_version:
            raise XrayTunConnectTooOld(actual, req.min_version)
        return actual
