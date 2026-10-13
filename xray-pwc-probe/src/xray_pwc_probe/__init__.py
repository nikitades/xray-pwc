import asyncio

from xray_pwc_probe.container import build_container
from xray_pwc_probe.entrypoints.cli import CliApp, Settings


def main() -> None:
    container = build_container(Settings())
    raise SystemExit(asyncio.run(container[CliApp].run()))
