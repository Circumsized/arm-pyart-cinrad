"""CmaMusicSource (天擎 MUSIC) tests — fully mocked, no network."""

from datetime import datetime, timedelta

import pytest

from pyart.io.remote import CmaMusicSource, RadarSite, RemoteDataError


def _fake_client(entries=None):
    class _FakeClient:
        def __init__(self):
            self.entries = entries or []

        def getRadaFileByTimeRangeAndStaId(self, **kwargs):
            return self.entries

    return _FakeClient()


def _music_source(user_id="u", api_key="k", server_id=None, station_table=None):
    return CmaMusicSource(station_table=station_table, user_id=user_id,
                          api_key=api_key, server_id=server_id)


def test_cma_music_registered():
    from pyart.io.remote import list_sources
    assert "cma_music" in list_sources()


def test_list_sites_uses_station_table():
    src = _music_source(station_table={
        "Z9532": RadarSite("Z9532", 30.0, 120.0, 50.0, "S", "CN"),
    })
    assert src.list_sites()["Z9532"].band == "S"


def test_list_sites_no_creds_empty(monkeypatch):
    monkeypatch.delenv("CMA_MUSIC_USER_ID", raising=False)
    monkeypatch.delenv("CMA_MUSIC_API_KEY", raising=False)
    src = CmaMusicSource()
    with pytest.warns(RuntimeWarning):
        sites = src.list_sites()
    assert sites == {}


def test_fetch_missing_creds_raises(monkeypatch, tmp_path):
    src = CmaMusicSource()
    key = {"dataCode": "RADA_L2_FMT", "staId": "Z9532",
           "timeRange": "(2020-01-01 00:00:00,2020-01-01 00:00:00]"}
    with pytest.raises(RemoteDataError):
        src.fetch(key, dest=str(tmp_path / "f.bin"))


def test_fetch_uses_documented_interface(monkeypatch, tmp_path):
    entries = [{"url": "http://music.example/radar.bin"}]
    src = _music_source()
    monkeypatch.setattr(src, "_client",
                        lambda timeout=15.0: _fake_client(entries))
    downloaded = {}

    def fake_http(url, timeout=15.0):
        downloaded["url"] = url
        return b"CINRAD-BYTES"

    monkeypatch.setattr(src, "_http_get", fake_http)
    key = {"dataCode": "RADA_L2_FMT", "staId": "Z9532",
           "timeRange": "(2020-01-01 00:00:00,2020-01-01 00:00:00]"}
    out = src.fetch(key, dest=str(tmp_path / "f.bin"))
    assert downloaded["url"] == "http://music.example/radar.bin"
    with open(out, "rb") as fh:
        assert fh.read() == b"CINRAD-BYTES"


def test_fetch_direct_url_key(monkeypatch, tmp_path):
    src = _music_source()
    downloaded = {}

    def fake_http(url, timeout=15.0):
        downloaded["url"] = url
        return b"DATA"

    monkeypatch.setattr(src, "_http_get", fake_http)
    key = {"dataCode": "RADA_L2_FMT", "staId": "Z9532",
           "timeRange": "(2020-01-01 00:00:00,2020-01-01 00:00:00]",
           "url": "http://direct.example/x.bin"}
    src.fetch(key, dest=str(tmp_path / "f.bin"))
    assert downloaded["url"] == "http://direct.example/x.bin"


def test_read_returns_cinrad(monkeypatch, tmp_path):
    src = _music_source()
    monkeypatch.setattr(src, "fetch",
                        lambda key, dest=None: str(tmp_path / "x.bin"))
    monkeypatch.setattr(
        "pyart.io.cinrad_bridge.read_cinrad",
        lambda local, **kw: "RADAR-FROM-CINRAD")
    key = {"dataCode": "RADA_L2_FMT", "staId": "Z9532", "timeRange": "()"}
    assert src.read(key) == "RADAR-FROM-CINRAD"


def test_read_fallback_path(monkeypatch, tmp_path):
    src = _music_source()
    local = str(tmp_path / "x.bin")
    monkeypatch.setattr(src, "fetch", lambda key, dest=None: local)
    monkeypatch.setattr(
        "pyart.io.cinrad_bridge.read_cinrad",
        lambda local, **kw: (_ for _ in ()).throw(RuntimeError("decode")))
    key = {"dataCode": "RADA_L2_FMT", "staId": "Z9532", "timeRange": "()"}
    assert src.read(key) == local


def test_list_files_grid():
    src = _music_source()
    start = datetime(2020, 6, 1, 0, 0)
    end = datetime(2020, 6, 1, 1, 0)
    step = timedelta(minutes=30)
    keys = src.list_files("Z9532", start, end, step)
    assert len(keys) == 2
    assert keys[0]["staId"] == "Z9532"
