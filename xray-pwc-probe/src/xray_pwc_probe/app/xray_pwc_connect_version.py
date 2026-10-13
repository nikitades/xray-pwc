from attrs import define, frozen

from xray_pwc_probe.app import AbstractUsecase, XrayPwcConnect
from xray_pwc_probe.domain.release import Release


class XrayPwcConnectMismatch(Exception):
    def __init__(self, actual: str, expected: str) -> None:
        self.actual, self.expected = actual, expected
        super().__init__(
            f"xray-pwc-connect {actual} does not match xray-pwc-probe {expected}: "
            "both must come from the same release"
        )


@frozen
class CheckXrayPwcConnectVersionRequest:
    # The probe's own version.
    expected: str


@define(eq=False)
class CheckXrayPwcConnectVersionUsecase(AbstractUsecase[CheckXrayPwcConnectVersionRequest, str]):
    xray_pwc_connect: XrayPwcConnect

    async def handle(self, req: CheckXrayPwcConnectVersionRequest) -> str:
        actual = await self.xray_pwc_connect.version()
        if Release.of(actual) != Release.of(req.expected):
            raise XrayPwcConnectMismatch(actual, req.expected)
        return actual
