import asyncio

from xray_tun_pwc.container import build_container
from xray_tun_pwc.entrypoints.cli import CliApp, Settings


def main() -> None:
    container = build_container(Settings())
    raise SystemExit(asyncio.run(container[CliApp].run()))
