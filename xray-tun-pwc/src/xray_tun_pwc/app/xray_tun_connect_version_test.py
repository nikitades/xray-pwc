import pytest
from lagom import Container
from packaging.version import Version

from xray_tun_pwc.app import XrayTunConnect
from xray_tun_pwc.app.xray_tun_connect_version import (
    CheckXrayTunConnectVersionRequest,
    CheckXrayTunConnectVersionUsecase,
    XrayTunConnectTooOld,
)
from xray_tun_pwc.fakes import FakeXrayTunConnect

MINIMUM = CheckXrayTunConnectVersionRequest(Version("0.0.1"))


@pytest.mark.parametrize("reported", ["0.0.1", "1.4.0"])
async def test_accepts_equal_and_newer(container: Container, reported: str):
    container[XrayTunConnect] = FakeXrayTunConnect(reported_version=reported)
    usecase = container[CheckXrayTunConnectVersionUsecase]
    assert await usecase.handle(MINIMUM) == Version(reported)


async def test_rejects_older(container: Container):
    container[XrayTunConnect] = FakeXrayTunConnect(reported_version="0.0.0")
    with pytest.raises(XrayTunConnectTooOld) as exc:
        await container[CheckXrayTunConnectVersionUsecase].handle(MINIMUM)
    assert (exc.value.actual, exc.value.minimum) == (Version("0.0.0"), Version("0.0.1"))
