import pytest

from xray_tun_pwc.domain.sites import (
    ConnectionReset,
    DnsFailure,
    HeavySite,
    HttpStatus,
    LightSite,
    SiteCatalog,
    SiteVisitError,
    TlsFailure,
    TunnelDown,
    UnrecognizedError,
    VisitTimeout,
)


@pytest.mark.parametrize(
    ("raw", "kind", "log"),
    [
        ("Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://x", TunnelDown, "tunnel"),
        ("Page.goto: net::ERR_SOCKS_CONNECTION_FAILED at https://x", TunnelDown, "tunnel"),
        ("Page.goto: Timeout 30000ms exceeded.", VisitTimeout, "timeout"),
        ("Page.goto: net::ERR_TIMED_OUT at https://x", VisitTimeout, "timeout"),
        ("Page.goto: net::ERR_CONNECTION_RESET at https://x", ConnectionReset, "reset"),
        ("Page.goto: net::ERR_EMPTY_RESPONSE at https://x", ConnectionReset, "reset"),
        ("Page.goto: net::ERR_SSL_PROTOCOL_ERROR at https://x", TlsFailure, "tls"),
        ("Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://x", TlsFailure, "tls"),
        ("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x", DnsFailure, "dns"),
        ("HTTP 429 from https://www.instagram.com/", HttpStatus, "HTTP 429"),
        ("HTTP None from https://www.instagram.com/", HttpStatus, "HTTP None"),
        ("something nobody expected", UnrecognizedError, "something nobody expected"),
    ],
)
def test_raw_errors_become_typed_errors(raw: str, kind: type[SiteVisitError], log: str):
    error = SiteVisitError.from_raw(raw)
    assert type(error) is kind
    assert error.to_log() == log
    assert error.raw == raw


def test_site_name_is_its_host_without_www():
    site = LightSite.at("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert site.name == "youtube.com"
    assert site.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_site_needs_a_host():
    with pytest.raises(ValueError, match="not a site URL"):
        HeavySite.at("/just/a/path")


def test_catalog_keeps_heavy_and_target_sites_apart():
    catalog = SiteCatalog.from_urls(
        heavy=["https://en.wikipedia.org/wiki/Special:Random"],
        targets=["https://www.instagram.com/"],
        heavy_visits=3,
    )
    assert all(isinstance(site, HeavySite) for site in catalog.heavy)
    assert all(isinstance(site, LightSite) for site in catalog.targets)


def test_catalog_refuses_heavy_visits_without_heavy_sites():
    with pytest.raises(ValueError, match="list of heavy sites is empty"):
        SiteCatalog.from_urls(heavy=[], targets=["https://t.me/"], heavy_visits=3)


def test_catalog_allows_skipping_heavy_sites():
    catalog = SiteCatalog.from_urls(heavy=[], targets=["https://t.me/"], heavy_visits=0)
    assert catalog.heavy == ()


@pytest.mark.parametrize(
    ("kind", "proves"),
    [
        (TunnelDown, False),
        (VisitTimeout, False),
        (ConnectionReset, False),
        (DnsFailure, False),
        (UnrecognizedError, False),
        (TlsFailure, True),
        (HttpStatus, True),
    ],
)
def test_only_answers_from_beyond_the_tunnel_prove_a_connection(
    kind: type[SiteVisitError], proves: bool
):
    assert kind.proves_connection is proves
