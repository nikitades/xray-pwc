import pytest

from xray_pwc_probe.domain.sites import (
    HeavySite,
    HttpStatus,
    LightSite,
    NetError,
    SiteCatalog,
    SiteVisitError,
    UnrecognizedError,
    VisitTimeout,
)


@pytest.mark.parametrize(
    ("raw", "kind", "name", "log"),
    [
        (
            "Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://x",
            NetError,
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_PROXY_CONNECTION_FAILED",
        ),
        (
            "Page.goto: net::ERR_CONNECTION_RESET at https://x",
            NetError,
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_RESET",
        ),
        (
            "Page.goto: net::ERR_CONNECTION_CLOSED at https://x",
            NetError,
            "ERR_CONNECTION_CLOSED",
            "ERR_CONNECTION_CLOSED",
        ),
        (
            "Page.goto: net::ERR_EMPTY_RESPONSE at https://x",
            NetError,
            "ERR_EMPTY_RESPONSE",
            "ERR_EMPTY_RESPONSE",
        ),
        ("Page.goto: net::ERR_TIMED_OUT at https://x", NetError, "ERR_TIMED_OUT", "ERR_TIMED_OUT"),
        (
            "Page.goto: net::ERR_SSL_PROTOCOL_ERROR at https://x",
            NetError,
            "ERR_SSL_PROTOCOL_ERROR",
            "ERR_SSL_PROTOCOL_ERROR",
        ),
        ("Page.goto: Timeout 30000ms exceeded.", VisitTimeout, "timeout", "timeout"),
        ("HTTP 429 from https://www.instagram.com/", HttpStatus, "HTTP 429", "HTTP 429"),
        ("HTTP None from https://www.instagram.com/", HttpStatus, "HTTP None", "HTTP None"),
        (
            "something nobody expected",
            UnrecognizedError,
            "unrecognized",
            "something nobody expected",
        ),
    ],
)
def test_raw_errors_become_typed_errors(raw: str, kind: type[SiteVisitError], name: str, log: str):
    error = SiteVisitError.from_raw(raw)
    assert type(error) is kind
    assert error.kind == name
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
    ("raw", "proves"),
    [
        ("net::ERR_PROXY_CONNECTION_FAILED", False),
        ("net::ERR_SOCKS_CONNECTION_FAILED", False),
        ("net::ERR_TUNNEL_CONNECTION_FAILED", False),
        ("net::ERR_TIMED_OUT", False),
        ("net::ERR_CONNECTION_RESET", False),
        ("net::ERR_NAME_NOT_RESOLVED", False),
        ("Timeout 30000ms exceeded.", False),
        ("something nobody expected", False),
        ("net::ERR_SSL_PROTOCOL_ERROR", True),
        ("net::ERR_CERT_AUTHORITY_INVALID", True),
        ("HTTP 429 from x", True),
    ],
)
def test_only_answers_from_beyond_the_tunnel_prove_a_connection(raw: str, proves: bool):
    assert SiteVisitError.from_raw(raw).proves_connection is proves
