import stat
from pathlib import Path

import pytest
from lagom import Container

from xray_pwc_probe.container import build_test_container
from xray_pwc_probe.entrypoints.cli import Settings
from xray_pwc_probe.ports.xray_pwc_connect import INSTALL_HINT, XrayPwcConnectBinary

LINKS = "vless://u@msk.example:443?security=tls#msk\ntrojan://p@spb.example:443#spb\n"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    links = tmp_path / "links.txt"
    links.write_text(LINKS)
    return Settings(config_path=links)


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_test_container(settings)


@pytest.fixture
def version_script(tmp_path: Path):
    def make(output: str, exit_code: int = 0) -> Path:
        script = tmp_path / "xray-pwc-connect"
        script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\nexit {exit_code}\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    return make


@pytest.fixture
def real_xray_pwc_connect() -> Path:
    installed = XrayPwcConnectBinary.installed()
    if installed is None:
        pytest.skip(INSTALL_HINT)
    return installed.path


@pytest.fixture
def tunnels_script(tmp_path: Path):
    def make(report: str | None, exit_code: int = 0) -> Path:
        print_report = f"printf '%s\\n' '{report}'\n" if report is not None else ""
        script = tmp_path / "xray-pwc-connect"
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > {tmp_path}/args\n"
            f'while IFS= read -r line && [ -n "$line" ]; do echo "$line" >> {tmp_path}/links; done\n'
            f"{print_report}"
            f"[ {exit_code} -ne 0 ] && exit {exit_code}\n"
            "cat > /dev/null\n"
            f"touch {tmp_path}/stopped\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    return make


@pytest.fixture
def chromium_installed() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Chromium not installed: uv run playwright install chromium")
