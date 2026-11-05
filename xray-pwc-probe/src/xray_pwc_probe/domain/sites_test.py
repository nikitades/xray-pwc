import pytest

from xray_pwc_probe.domain.sites import (
    CheckPolicy,
    CurlFailure,
    HeavySite,
    HttpStatus,
    NetError,
    ShortBody,
    SiteCatalog,
    SiteVisitError,
    TargetSite,
    UnrecognizedError,
    VisitTimeout,
)

WIKI = "https://en.wikipedia.org/wiki/Special:Random"
THE_SUN = "https://www.thesun.co.uk/"
CLOUDFLARE = "https://speed.cloudflare.com/__down?bytes=1000000"


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
        ("short body: 812 bytes from https://x", ShortBody, "short_body", "short_body"),
        (
            "Failed to perform, curl: (7) Failed to connect to x:443 over proxy 127.0.0.1",
            CurlFailure,
            "CURLE_COULDNT_CONNECT",
            "CURLE_COULDNT_CONNECT",
        ),
        (
            "Failed to perform, curl: (28) Operation timed out after 30001 milliseconds",
            CurlFailure,
            "timeout",
            "timeout",
        ),
        ("Failed to perform, curl: (99) something new", CurlFailure, "CURLE_99", "CURLE_99"),
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
    site = TargetSite.at("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert site.name == "youtube.com"
    assert site.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_site_needs_a_host():
    with pytest.raises(ValueError, match="not a site URL"):
        HeavySite.at("/just/a/path")


def test_catalog_keeps_heavy_and_target_sites_apart():
    catalog = SiteCatalog.from_urls(
        heavy_real=[WIKI], heavy_cffi=[], targets=["https://www.instagram.com/"], heavy_visits=3
    )
    assert all(isinstance(site, HeavySite) for site in catalog.heavy)
    assert all(isinstance(site, TargetSite) for site in catalog.targets)


def test_catalog_tags_each_heavy_site_with_the_lists_it_came_from():
    catalog = SiteCatalog.from_urls(
        heavy_real=[WIKI, THE_SUN], heavy_cffi=[CLOUDFLARE, WIKI], targets=[], heavy_visits=3
    )
    assert {site.url: site.tags for site in catalog.heavy} == {
        WIKI: {CheckPolicy.HEAVY, CheckPolicy.LIGHT},
        THE_SUN: {CheckPolicy.HEAVY},
        CLOUDFLARE: {CheckPolicy.LIGHT},
    }


def test_catalog_compares_urls_exactly():
    catalog = SiteCatalog.from_urls(
        heavy_real=[WIKI], heavy_cffi=[WIKI + "?"], targets=[], heavy_visits=3
    )
    assert [site.tags for site in catalog.heavy] == [{CheckPolicy.HEAVY}, {CheckPolicy.LIGHT}]


@pytest.mark.parametrize(
    ("policy", "urls"),
    [(CheckPolicy.HEAVY, [WIKI, THE_SUN]), (CheckPolicy.LIGHT, [WIKI, CLOUDFLARE])],
)
def test_a_policy_gets_its_heavy_sites_and_every_target(policy: CheckPolicy, urls: list[str]):
    catalog = SiteCatalog.from_urls(
        heavy_real=[WIKI, THE_SUN],
        heavy_cffi=[WIKI, CLOUDFLARE],
        targets=["https://www.instagram.com/"],
        heavy_visits=3,
    ).for_policy(policy)
    assert [site.url for site in catalog.heavy] == urls
    assert [site.name for site in catalog.targets] == ["instagram.com"]
    assert catalog.heavy_visits == 3


def test_a_policy_without_heavy_sites_is_refused():
    catalog = SiteCatalog.from_urls(
        heavy_real=[THE_SUN], heavy_cffi=[], targets=["https://t.me/"], heavy_visits=3
    )
    with pytest.raises(ValueError, match="check policy light has no heavy sites"):
        catalog.for_policy(CheckPolicy.LIGHT)


def test_a_policy_may_skip_heavy_sites():
    catalog = SiteCatalog.from_urls(
        heavy_real=[], heavy_cffi=[], targets=["https://t.me/"], heavy_visits=0
    )
    assert catalog.for_policy(CheckPolicy.LIGHT).heavy == ()


def test_catalog_refuses_negative_heavy_visits():
    with pytest.raises(ValueError, match="must not be negative"):
        SiteCatalog.from_urls(heavy_real=[WIKI], heavy_cffi=[], targets=[], heavy_visits=-1)


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
        ("short body: 812 bytes from x", True),
        ("curl: (7) Failed to connect", False),
        ("curl: (97) proxy handshake failed", False),
        ("curl: (28) Operation timed out", False),
        ("curl: (35) TLS connect error", True),
        ("curl: (60) SSL certificate problem", True),
    ],
)
def test_only_answers_from_beyond_the_tunnel_prove_a_connection(raw: str, proves: bool):
    assert SiteVisitError.from_raw(raw).proves_connection is proves
