import asyncio
import random
import socket
from asyncio import StreamReader, StreamWriter
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from xray_pwc_probe.domain.sites import CheckPolicy, CurlFailure, SiteCatalog
from xray_pwc_probe.fakes import LocalPort
from xray_pwc_probe.ports.curl_browser import MIN_BODY_BYTES, CurlBrowser
from xray_pwc_probe.ports.visiting import Backoff

FAST = Backoff(max_attempts=2, base_delay=0.01)


async def pipe(reader: StreamReader, writer: StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    writer.close()


async def socks5(client: StreamReader, reply: StreamWriter) -> None:
    """A SOCKS5 proxy that serves one CONNECT, no auth: the tunnel of a link."""
    _, methods = await client.readexactly(2)
    await client.readexactly(methods)
    reply.write(b"\x05\x00")
    _, _, _, address_type = await client.readexactly(4)
    if address_type == 1:
        host = socket.inet_ntoa(await client.readexactly(4))
    else:
        host = (await client.readexactly((await client.readexactly(1))[0])).decode()
    port = int.from_bytes(await client.readexactly(2))
    upstream, upstream_writer = await asyncio.open_connection(host, port)
    reply.write(b"\x05\x00\x00\x01" + bytes(6))
    await asyncio.gather(pipe(client, upstream_writer), pipe(upstream, reply))


def http_site(routes: dict[str, tuple[int, int]]):
    """An HTTP server answering each path with (status, body size)."""

    async def handle(reader: StreamReader, writer: StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        status, size = routes.get(request.split(b" ")[1].decode(), (404, 0))
        head = f"HTTP/1.1 {status} X\r\nContent-Length: {size}\r\nConnection: close\r\n\r\n"
        writer.write(head.encode() + b"x" * size)
        await writer.drain()
        writer.close()

    return handle


@asynccontextmanager
async def serving(handler) -> AsyncGenerator[int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


async def test_a_page_counts_on_a_2xx_answer_longer_than_16_kb():
    routes = {
        "/big": (200, MIN_BODY_BYTES + 1),
        "/exactly-16-kb": (200, MIN_BODY_BYTES),
        "/no-content": (204, 0),
        "/unavailable": (503, MIN_BODY_BYTES * 2),
    }
    async with serving(http_site(routes)) as site_port, serving(socks5) as socks_port:
        site = f"http://127.0.0.1:{site_port}"
        catalog = SiteCatalog.from_urls(
            heavy_real=[],
            heavy_cffi=[f"{site}/big"],
            targets=[f"{site}/exactly-16-kb", f"{site}/no-content", f"{site}/unavailable"],
            heavy_visits=2,
        ).for_policy(CheckPolicy.LIGHT)
        browser = CurlBrowser(catalog, backoff=FAST, rng=random.Random(1))
        async with browser.open(socks_port) as session:
            heavy = await session.check_heavy_sites()
            targets = await session.check_target_sites()

    assert [(v.ok, v.attempts) for v in heavy] == [(True, 1), (True, 1)]
    assert [(v.error.kind if v.error else None, v.proves_connection) for v in targets] == [
        ("short_body", True),
        ("short_body", True),
        ("HTTP 503", True),
    ]
    assert all(v.attempts == 2 for v in targets)


async def test_dead_tunnel_fails_every_site_after_all_retries():
    catalog = SiteCatalog.from_urls(
        heavy_real=[],
        heavy_cffi=["https://speed.cloudflare.com/__down?bytes=1000000"],
        targets=["https://www.instagram.com/"],
        heavy_visits=2,
    ).for_policy(CheckPolicy.LIGHT)
    browser = CurlBrowser(catalog, backoff=FAST)
    async with browser.open(LocalPort.unused().number) as session:
        visits = (*await session.check_heavy_sites(), *await session.check_target_sites())

    assert len(visits) == 3
    for visit in visits:
        assert not visit.ok
        assert visit.attempts == 2
        assert isinstance(visit.error, CurlFailure)
        assert visit.error.kind == "CURLE_COULDNT_CONNECT"
        assert not visit.proves_connection
