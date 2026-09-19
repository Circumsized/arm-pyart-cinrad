"""
Security regression tests for pyart.io.remote (FU-02).

Covers two SSRF guard defects in ``_BaseSource._validate_url`` /
``CmaMusicSource.fetch``:

* e690e4 (CWE-918): ``CmaMusicSource.fetch`` passed ``allow_private=True``,
  short-circuiting the guard so any http/https URL was fetched.
* d9214c (CWE-918): the guard only rejected a hardcoded list of literal
  hostnames/IPs, so any DNS name (including names resolving to loopback,
  RFC1918, or the 169.254.169.254 cloud metadata address) was allowed.

The fix resolves the hostname and classifies *every* resolved address,
refuses non-resolving hosts, and no longer bypasses the guard for MUSIC
downloads. Redirect re-validation (per hop) was already implemented and is
asserted here as well.
"""

import socket

import pytest

pytest.importorskip("pyart")
pytest.importorskip("requests")

from pyart.io.remote import (  # noqa: E402
    CmaMusicSource,
    RemoteDataError,
    _BaseSource,
)


def _fake_getaddrinfo(ip):
    def _inner(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    return _inner


@pytest.mark.parametrize(
    "url,ip",
    [
        # DNS names that resolve to internal space (DNS-rebinding style).
        ("http://localtest.me/", "127.0.0.1"),
        ("http://internal-metadata.corp/", "169.254.169.254"),
        ("https://sneaky.example.com/", "10.0.0.5"),
        ("http://a.example.com/", "192.168.1.10"),
        ("http://b.example.com/", "172.16.0.9"),
        # Raw internal literals.
        ("http://127.0.0.1:6379/", "127.0.0.1"),
        ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("http://[::1]/admin", "::1"),
        ("http://[fc00::1]/", "fc00::1"),
        ("http://[fe80::1]/", "fe80::1"),
        # IPv4-mapped IPv6 loopback.
        ("http://[::ffff:127.0.0.1]/", "::ffff:127.0.0.1"),
    ],
)
def test_validate_url_rejects_internal_destinations(monkeypatch, url, ip):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(ip))
    assert _BaseSource._validate_url(url) is False


@pytest.mark.parametrize(
    "host",
    ["2130706433", "017700000001", "0x7f.0x0.0x0.0x1", "127.1"],
)
def test_validate_url_rejects_alternative_ip_literal_encodings(host):
    """Decimal/octal/hex/short-form loopback literals must not pass."""
    assert _BaseSource._validate_url(f"http://{host}/") is False


def test_validate_url_allows_public_destination(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert _BaseSource._validate_url("https://example.com/data.nc") is True


def test_validate_url_rejects_non_http_schemes():
    assert _BaseSource._validate_url("ftp://example.com/x") is False
    assert _BaseSource._validate_url("file:///etc/passwd") is False
    assert _BaseSource._validate_url("gopher://example.com/") is False


def test_validate_url_rejects_unresolvable_host(monkeypatch):
    def _boom(host, *args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert _BaseSource._validate_url("http://nonexistent.invalid/") is False


def test_http_get_refuses_internal_url_without_any_connection(monkeypatch, tmp_path):
    """No socket may be opened for an internal destination."""
    import requests

    opened = []

    def _spy(url, *args, **kwargs):
        opened.append(url)
        raise AssertionError("requests.get must not be called for internal URLs")

    monkeypatch.setattr(requests, "get", _spy)
    src = _BaseSource()
    with pytest.raises(RemoteDataError, match="Refusing to request unsafe URL"):
        src._http_get("http://127.0.0.1:6379/")
    assert opened == []


def test_http_get_revalidates_every_redirect_hop(monkeypatch):
    """A validated public URL redirecting inward must be refused."""
    import requests

    class _Resp:
        is_redirect = True
        content = b""

        def __init__(self, location):
            self.headers = {"Location": location}

        def raise_for_status(self):
            pass

    def _host_aware_getaddrinfo(host, *args, **kwargs):
        # Only the initial host is public; the redirect target IP literal
        # must resolve to itself so classification is actually exercised.
        ip = "93.184.216.34" if host == "example.com" else host
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    hops = []

    def _spy(url, *args, **kwargs):
        hops.append(url)
        return _Resp("http://169.254.169.254/latest/meta-data/")

    monkeypatch.setattr(requests, "get", _spy)
    monkeypatch.setattr(socket, "getaddrinfo", _host_aware_getaddrinfo)
    src = _BaseSource()
    with pytest.raises(RemoteDataError, match="Refusing to request unsafe URL"):
        src._http_get("https://example.com/start")
    assert hops == ["https://example.com/start"]


def test_cma_music_fetch_no_longer_bypasses_the_guard(monkeypatch, tmp_path):
    """e690e4: MUSIC downloads must run through the same SSRF guard."""
    import requests

    opened = []

    def _spy(url, *args, **kwargs):
        opened.append(url)
        raise AssertionError("requests.get must not be called for internal URLs")

    monkeypatch.setattr(requests, "get", _spy)
    src = CmaMusicSource()
    key = {
        "dataCode": "SEVP_AWR_L2_ROBS",
        "staId": "Z9971",
        "timeRange": "[2026-01-01 00:00:00,2026-01-01 00:10:00]",
        "url": "http://127.0.0.1:6379/",
    }
    with pytest.raises(RemoteDataError, match="Refusing to request unsafe URL"):
        src.fetch(key, dest=str(tmp_path / "cached.bin"))
    assert opened == []
