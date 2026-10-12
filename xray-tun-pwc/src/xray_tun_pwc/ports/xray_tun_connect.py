import asyncio
import json
import os
import shutil
import sys
from asyncio import StreamReader, StreamWriter
from asyncio.subprocess import PIPE, Process
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, ClassVar

from attrs import frozen
from packaging.version import InvalidVersion, Version

from xray_tun_pwc.app import (
    DEFAULT_TUNNEL_OPTIONS,
    TunnelOptions,
    XrayTunConnect,
    XrayTunConnectFailed,
    XrayTunConnectUnavailable,
)
from xray_tun_pwc.domain.entities import SocksTunnel, TunnelGroup, VpnConfigLine
from xray_tun_pwc.log import relayed

INSTALL_HINT = (
    "Install xray-tun-connect: run `make install` (go install) in the xray-tun-connect source "
    "tree, or download xray-tun-connect-<os>-<arch> from an xray-tun-connect/v* GitHub release "
    "and put it on PATH. Lookup order: $GOBIN, $GOPATH/bin (default ~/go/bin), PATH; "
    "PWC_XRAY_TUN_CONNECT_BIN pins an explicit path."
)

REPORT_GRACE_SECONDS = 10.0
SHUTDOWN_GRACE_SECONDS = 5.0


@frozen
class GoBinDir:
    path: Path

    @classmethod
    def from_environment(cls) -> GoBinDir:
        if gobin := os.environ.get("GOBIN"):
            return cls(Path(gobin))
        first_gopath = os.environ.get("GOPATH", "").split(os.pathsep)[0]
        return cls(Path(first_gopath or Path.home() / "go") / "bin")


@frozen
class XrayTunConnectBinary:
    path: Path

    NAME: ClassVar[str] = "xray-tun-connect"

    @classmethod
    def installed(cls) -> XrayTunConnectBinary | None:
        installed_by_go = GoBinDir.from_environment().path / cls.NAME
        if os.access(installed_by_go, os.X_OK):
            return cls(installed_by_go)
        on_path = shutil.which(cls.NAME)
        return cls(Path(on_path)) if on_path else None


@frozen
class TunnelCommand:
    configs: tuple[VpnConfigLine, ...]
    options: TunnelOptions

    def arguments(self) -> list[str]:
        return [
            "-stdin-links",
            "-start-port", str(self.options.start_port),
            "-listen", self.options.listen,
            "-xray-log-level", self.options.xray_log_level,
            "-ready-timeout", f"{self.options.ready_timeout.total_seconds():g}s",
        ]  # fmt: skip

    def stdin_links(self) -> bytes:
        return "".join(f"{config.value}\n" for config in self.configs).encode() + b"\n"


@frozen
class TunnelReport:
    listen: str
    entries: tuple[dict[str, Any], ...]

    @classmethod
    def parse(cls, line: bytes) -> TunnelReport:
        report = json.loads(line)
        return cls(listen=report["listen"], entries=tuple(report["tunnels"]))

    def group_for(self, configs: Sequence[VpnConfigLine]) -> TunnelGroup:
        return TunnelGroup(
            listen=self.listen,
            tunnels=tuple(
                self.tunnel_from(entry, configs[entry["index"]]) for entry in self.entries
            ),
        )

    def tunnel_from(self, entry: dict[str, Any], config: VpnConfigLine) -> SocksTunnel:
        protocol = entry.get("protocol", "")
        if entry["ok"]:
            return SocksTunnel.alive(config, entry["port"], protocol)
        return SocksTunnel.broken(config, entry["port"], entry.get("error", ""), protocol)


async def relay_log(stderr: StreamReader) -> None:
    """Passes xray-tun-connect's log lines on to ours, marked as its own."""
    async for raw in stderr:
        print(relayed("xtc", raw.decode(errors="replace")), file=sys.stderr, flush=True)


@frozen
class TunnelProcess:
    process: Process
    stdin: StreamWriter
    stdout: StreamReader
    log_relay: asyncio.Task[None]

    @classmethod
    def of(cls, process: Process) -> TunnelProcess:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise XrayTunConnectFailed("xray-tun-connect was started without its pipes")
        return cls(
            process, process.stdin, process.stdout, asyncio.create_task(relay_log(process.stderr))
        )

    async def send_links(self, command: TunnelCommand) -> None:
        self.stdin.write(command.stdin_links())
        await self.stdin.drain()

    async def read_report(
        self, configs: Sequence[VpnConfigLine], options: TunnelOptions
    ) -> TunnelGroup:
        deadline = options.ready_timeout.total_seconds() + REPORT_GRACE_SECONDS
        try:
            line = await asyncio.wait_for(self.stdout.readline(), deadline)
        except TimeoutError as e:
            raise XrayTunConnectFailed(
                f"xray-tun-connect printed no report in {deadline:g}s"
            ) from e
        if not line:
            raise XrayTunConnectFailed(
                f"xray-tun-connect exited with code {await self.process.wait()} "
                "before reporting tunnels"
            )
        return TunnelReport.parse(line).group_for(configs)

    async def shut_down(self) -> None:
        self.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), SHUTDOWN_GRACE_SECONDS)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
        try:
            await asyncio.wait_for(self.log_relay, SHUTDOWN_GRACE_SECONDS)
        except TimeoutError:
            self.log_relay.cancel()


@frozen
class XrayTunConnectCli(XrayTunConnect):
    binary: Path | None = None
    timeout: float = 10.0

    async def version(self) -> Version:
        proc = await self.spawn("-version", stdout=PIPE, stderr=PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except TimeoutError as e:
            proc.kill()
            raise XrayTunConnectUnavailable(
                f"xray-tun-connect -version hung for {self.timeout}s"
            ) from e

        if proc.returncode != 0:
            raise XrayTunConnectUnavailable(
                f"xray-tun-connect -version exited {proc.returncode}: "
                f"{err.decode(errors='replace').strip()}"
            )

        raw = out.decode().strip()
        try:
            return Version(raw)
        except InvalidVersion as e:
            raise XrayTunConnectUnavailable(
                f"xray-tun-connect -version printed {raw!r}, not a version"
            ) from e

    @asynccontextmanager
    async def start(
        self, configs: Sequence[VpnConfigLine], options: TunnelOptions = DEFAULT_TUNNEL_OPTIONS
    ) -> AsyncGenerator[TunnelGroup]:
        if not configs:
            raise ValueError("no configs to start tunnels for")
        command = TunnelCommand(tuple(configs), options)
        tunnels = TunnelProcess.of(
            await self.spawn(*command.arguments(), stdin=PIPE, stdout=PIPE, stderr=PIPE)
        )
        try:
            await tunnels.send_links(command)
            yield await tunnels.read_report(configs, options)
        finally:
            await tunnels.shut_down()

    def resolve_binary(self) -> Path:
        if self.binary is not None:
            return self.binary
        installed = XrayTunConnectBinary.installed()
        if installed is None:
            raise XrayTunConnectUnavailable(f"xray-tun-connect binary not found\n{INSTALL_HINT}")
        return installed.path

    async def spawn(self, *args: str, **pipes) -> Process:
        binary = self.resolve_binary()
        try:
            return await asyncio.create_subprocess_exec(str(binary), *args, **pipes)
        except FileNotFoundError as e:
            raise XrayTunConnectUnavailable(
                f"xray-tun-connect binary not found: {binary}\n{INSTALL_HINT}"
            ) from e
