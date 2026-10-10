import pytest
from attrs import frozen

from xray_tun_pwc.domain.entities import Reach, SocksTunnel, TunnelCheck, VpnConfigLine
from xray_tun_pwc.domain.sites import HeavySite, LightSite, Site, SiteVisit, SiteVisitError

WIKIPEDIA = HeavySite.at("https://en.wikipedia.org/wiki/Special:Random")
INSTAGRAM = LightSite.at("https://www.instagram.com/")

MSK = VpnConfigLine.from_str("vless://u@msk.example:443?security=tls#msk")


def test_stable_id_ignores_the_remark():
    renamed = VpnConfigLine.from_str("vless://u@msk.example:443?security=tls#moscow-1")
    other_server = VpnConfigLine.from_str("vless://u@spb.example:443?security=tls#msk")
    assert MSK.stable_id == renamed.stable_id
    assert MSK.stable_id != other_server.stable_id
    assert len(MSK.stable_id) == 16


def test_code_falls_back_to_stable_id_without_a_remark():
    unnamed = VpnConfigLine.from_str("vless://u@msk.example:443?security=tls")
    assert MSK.code == "msk"
    assert unnamed.code == unnamed.stable_id


def test_summary_of_a_healthy_tunnel():
    tunnel = SocksTunnel.alive(MSK, 10000, "vless")
    check = TunnelCheck(tunnel, (SiteVisit.succeeded(WIKIPEDIA, WIKIPEDIA.url, attempts=1),))
    assert check.success and check.perfect
    assert check.summary() == "msk - OK"


def test_one_opened_site_is_a_success_but_not_perfect():
    tunnel = SocksTunnel.alive(MSK, 10000, "vless")
    check = TunnelCheck(
        tunnel,
        (
            SiteVisit.succeeded(WIKIPEDIA, WIKIPEDIA.url, attempts=1),
            SiteVisit.failed(
                INSTAGRAM, INSTAGRAM.url, 3, SiteVisitError.from_raw("HTTP 429 from x")
            ),
        ),
    )
    assert check.success and not check.perfect
    assert check.summary() == "msk - partial[instagram.com: HTTP 429]"


def test_summary_lists_every_failed_site_of_a_connected_tunnel():
    tunnel = SocksTunnel.alive(MSK, 10000, "vless")
    check = TunnelCheck(
        tunnel,
        (
            SiteVisit.failed(
                WIKIPEDIA, WIKIPEDIA.url, 3, SiteVisitError.from_raw("net::ERR_TIMED_OUT")
            ),
            SiteVisit.failed(
                INSTAGRAM, INSTAGRAM.url, 3, SiteVisitError.from_raw("HTTP 429 from x")
            ),
        ),
    )
    assert check.success and not check.perfect
    assert check.summary() == "msk - partial[en.wikipedia.org: timeout, instagram.com: HTTP 429]"


def test_summary_of_an_unreachable_tunnel():
    check = TunnelCheck(
        SocksTunnel.alive(MSK, 10000, "vless"),
        (
            SiteVisit.failed(
                WIKIPEDIA, WIKIPEDIA.url, 3, SiteVisitError.from_raw("net::ERR_TIMED_OUT")
            ),
        ),
    )
    assert not check.success and not check.perfect
    assert check.summary() == "msk - failed[en.wikipedia.org: timeout]"


def test_summary_of_a_broken_link():
    check = TunnelCheck(SocksTunnel.broken(MSK, 10000, "unsupported share format"), ())
    assert not check.success and not check.perfect
    assert check.summary() == "msk - failed[tunnel: unsupported share format]"


ALIVE = SocksTunnel.alive(MSK, 10000, "vless")
OPENED = SiteVisit.succeeded(WIKIPEDIA, WIKIPEDIA.url, attempts=1)


@frozen
class FailedVisit:
    raw: str

    def on(self, site: Site) -> SiteVisit:
        return SiteVisit.failed(site, site.url, 3, SiteVisitError.from_raw(self.raw))


SOCKS_REFUSED = FailedVisit("net::ERR_SOCKS_CONNECTION_FAILED at https://x")
TIMED_OUT = FailedVisit("net::ERR_TIMED_OUT at https://x")
RATE_LIMITED = FailedVisit("HTTP 429 from https://x")


@pytest.mark.parametrize(
    ("check", "reach"),
    [
        (
            TunnelCheck(SocksTunnel.broken(MSK, 10000, "unsupported share format"), ()),
            Reach.LINK_BROKEN,
        ),
        (TunnelCheck(ALIVE, ()), Reach.UNREACHABLE),
        (
            TunnelCheck(ALIVE, (SOCKS_REFUSED.on(WIKIPEDIA), SOCKS_REFUSED.on(INSTAGRAM))),
            Reach.UNREACHABLE,
        ),
        (TunnelCheck(ALIVE, (TIMED_OUT.on(WIKIPEDIA), TIMED_OUT.on(INSTAGRAM))), Reach.UNREACHABLE),
        (
            TunnelCheck(ALIVE, (TIMED_OUT.on(WIKIPEDIA), RATE_LIMITED.on(INSTAGRAM))),
            Reach.CONNECTED,
        ),
        (TunnelCheck(ALIVE, (TIMED_OUT.on(WIKIPEDIA), OPENED)), Reach.CONNECTED),
        (TunnelCheck(ALIVE, (OPENED, OPENED)), Reach.PERFECT),
    ],
    ids=[
        "link broken",
        "nothing visited",
        "socks refused",
        "only timeouts",
        "site answered",
        "one page opened",
        "all opened",
    ],
)
def test_reach_is_the_furthest_stage_the_evidence_proves(check: TunnelCheck, reach: Reach):
    assert check.reach is reach


@pytest.mark.parametrize(
    ("reach", "success", "perfect"),
    [
        (Reach.LINK_BROKEN, False, False),
        (Reach.UNREACHABLE, False, False),
        (Reach.CONNECTED, True, False),
        (Reach.PERFECT, True, True),
    ],
)
def test_reach_decides_success_and_perfect(reach: Reach, success: bool, perfect: bool):
    assert (reach.success, reach.perfect) == (success, perfect)


def test_stages_go_from_full_failure_to_full_success():
    ranks = [reach.rank for reach in Reach]
    assert ranks == sorted(ranks)
