"""NmcCnSource / CmaMosSource tests with mocked HTTP (no network)."""

from datetime import datetime, timedelta

import pytest

from pyart.io.remote import (
    CmaMosSource,
    NmcCnSource,
    get_source,
    list_sources,
)


class _FakeResp:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_nmc_cn_registered():
    assert "nmc_cn" in list_sources()


def test_cma_mos_registered():
    assert "cma_mos" in list_sources()


def test_nmc_list_sites_unreachable_returns_empty(monkeypatch):
    src = NmcCnSource()
    monkeypatch.setattr(src, "_is_reachable",
                        lambda url, timeout=8.0: False)
    with pytest.warns(RuntimeWarning):
        sites = src.list_sites()
    assert sites == {}


def test_nmc_list_sites_reachable(monkeypatch):
    src = NmcCnSource()
    monkeypatch.setattr(src, "_is_reachable",
                        lambda url, timeout=8.0: True)
    sites = src.list_sites()
    assert "nationwide" in sites
    assert sites["nationwide"].country == "CN"


def test_nmc_list_files_grid(monkeypatch):
    src = NmcCnSource()
    monkeypatch.setattr(src, "_is_reachable",
                        lambda url, timeout=8.0: True)
    start = datetime(2020, 6, 1, 0, 0)
    end = datetime(2020, 6, 1, 2, 0)
    step = timedelta(hours=1)
    files = src.list_files("nationwide", start, end, step)
    assert len(files) == 2
    assert all(f.endswith(".png") for f in files)
    assert "2020060100" in files[0]


def test_nmc_list_files_unknown_region():
    src = NmcCnSource()
    assert src.list_files("atlantis", datetime(2020, 1, 1),
                          datetime(2020, 1, 1, 1),
                          timedelta(hours=1)) == []


def test_nmc_fetch_caches_and_read_returns_path(monkeypatch, tmp_path):
    src = NmcCnSource()
    monkeypatch.setattr(src, "_http_get",
                        lambda url, timeout=15.0: b"PNG-DATA")
    key = "http://www.nmc.cn/publish/radar/all/2020060100.png"
    out = src.fetch(key, dest=str(tmp_path / "cached.png"))
    assert out == str(tmp_path / "cached.png")
    with open(out, "rb") as fh:
        assert fh.read() == b"PNG-DATA"
    # read returns the local path (raster, not a Radar)
    assert src.read(key, dest=out) == out


def test_nmc_fetch_uses_cache(monkeypatch, tmp_path):
    src = NmcCnSource()
    local = tmp_path / "cached.png"
    local.write_bytes(b"already")
    monkeypatch.setattr(src, "_http_get",
                        lambda *a, **k: pytest.fail("should not download"))
    out = src.fetch("http://x/x.png", dest=str(local))
    assert out == str(local)


def test_cma_list_sites_reachable(monkeypatch):
    src = CmaMosSource()
    monkeypatch.setattr(src, "_is_reachable",
                        lambda url, timeout=8.0: True)
    sites = src.list_sites()
    assert "beijing" in sites


def test_cma_list_files_grid(monkeypatch):
    src = CmaMosSource()
    start = datetime(2020, 6, 1, 0, 0)
    end = datetime(2020, 6, 1, 1, 0)
    step = timedelta(minutes=30)
    files = src.list_files("shanghai", start, end, step)
    assert len(files) == 2
    assert "20200601" in files[0]


def test_cma_read_falls_back_to_path(monkeypatch, tmp_path):
    src = CmaMosSource()
    monkeypatch.setattr(src, "_http_get",
                        lambda url, timeout=15.0: b"not-cinrad")
    key = "http://data.cma.cn/radar/sh/20200601/0000.bin"
    out = src.read(key, dest=str(tmp_path / "cma.bin"))
    # read_cinrad will fail on bogus bytes -> fallback returns local path
    assert out == str(tmp_path / "cma.bin")


def test_cma_read_uses_read_cinrad(monkeypatch, tmp_path):
    src = CmaMosSource()
    monkeypatch.setattr(src, "_http_get",
                        lambda url, timeout=15.0: b"fake-cinrad")
    monkeypatch.setattr(
        "pyart.io.cinrad_bridge.read_cinrad",
        lambda local, **kw: "RADAR-FROM-CINRAD")
    out = src.read("http://data.cma.cn/radar/sh/20200601/0000.bin",
                   dest=str(tmp_path / "cma.bin"))
    assert out == "RADAR-FROM-CINRAD"
