from xray_tun_pwc.domain.entities import SocksTunnel, TunnelCheck, VpnConfigLine
from xray_tun_pwc.domain.sites import HeavySite, LightSite, SiteVisit, SiteVisitError

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


def test_summary_lists_every_failed_site():
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
    assert not check.success and not check.perfect
    assert check.summary() == "msk - failed[en.wikipedia.org: timeout, instagram.com: HTTP 429]"


def test_summary_of_a_broken_link():
    check = TunnelCheck(SocksTunnel.broken(MSK, 10000, "unsupported share format"), ())
    assert not check.success and not check.perfect
    assert check.summary() == "msk - failed[tunnel: unsupported share format]"
