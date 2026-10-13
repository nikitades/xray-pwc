import pytest
from lagom import Container

from xray_pwc_probe.app import XrayPwcConnect
from xray_pwc_probe.app.xray_pwc_connect_version import (
    CheckXrayPwcConnectVersionRequest,
    CheckXrayPwcConnectVersionUsecase,
    XrayPwcConnectMismatch,
)
from xray_pwc_probe.fakes import FakeXrayPwcConnect

PROBE = CheckXrayPwcConnectVersionRequest("1.1.0.dev0+3f9c2ab41d0e")


@pytest.mark.parametrize("reported", ["1.1.0", "1.1.0-dev.3f9c2ab41d0e", "1.1.0-dev.0123456789ab"])
async def test_accepts_the_same_release(container: Container, reported: str):
    container[XrayPwcConnect] = FakeXrayPwcConnect(reported_version=reported)
    usecase = container[CheckXrayPwcConnectVersionUsecase]
    assert await usecase.handle(PROBE) == reported


@pytest.mark.parametrize("reported", ["1.0.9", "1.1.1", "2.1.0"])
async def test_rejects_another_release(container: Container, reported: str):
    container[XrayPwcConnect] = FakeXrayPwcConnect(reported_version=reported)
    with pytest.raises(XrayPwcConnectMismatch) as exc:
        await container[CheckXrayPwcConnectVersionUsecase].handle(PROBE)
    assert (exc.value.actual, exc.value.expected) == (reported, PROBE.expected)
