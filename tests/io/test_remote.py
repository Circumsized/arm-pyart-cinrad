"""Contract tests for the pyart.io.remote acquisition layer."""

from datetime import datetime, timedelta

import pytest

from pyart.io.remote import (
    RemoteDataError,
    RadarSite,
    RadarSource,
    _BaseSource,
    get_source,
    list_sources,
    read_time_span,
    register_source,
)


class _DummySource(_BaseSource):
    """In-memory source used to exercise the protocol/registry plumbing."""

    name = "dummy"
    bands = ("X",)

    def __init__(self):
        self._files = {
            "Z_KMTX": [
                ("dummy/Z_KMTX_20200101_000000", 0),
                ("dummy/Z_KMTX_20200101_010000", 1),
                ("dummy/Z_KMTX_20200101_020000", 2),
            ],
        }

    def list_sites(self):
        return {"Z_KMTX": RadarSite("KMTX", 40.0, -120.0, 1000.0, "X", "US")}

    def list_files(self, site, start, end, step):
        return [k for k, _ in self._files.get(site, [])][:2]

    def fetch(self, key, dest=None):
        return key

    def read(self, key, **kwargs):
        return {"key": key, "kwargs": kwargs}


def test_radar_source_protocol_is_runtime_checkable():
    src = _DummySource()
    assert isinstance(src, RadarSource)


def test_registry_register_and_list():
    if "dummy" in list_sources():
        pass  # already registered by a prior import
    else:
        register_source(_DummySource)
    assert "dummy" in list_sources()


def test_get_source_returns_instance():
    register_source(_DummySource)
    src = get_source("dummy")
    assert isinstance(src, _DummySource)
    assert src.name == "dummy"
    assert src.bands == ("X",)


def test_get_source_unknown_raises():
    with pytest.raises(KeyError):
        get_source("does-not-exist")


def test_register_source_requires_name():
    class _Noname:
        pass

    with pytest.raises(ValueError):
        register_source(_Noname)


def test_read_time_span_with_name_string():
    register_source(_DummySource)
    start = datetime(2020, 1, 1, 0, 0)
    end = datetime(2020, 1, 1, 3, 0)
    step = timedelta(hours=1)
    radars = read_time_span("dummy", "Z_KMTX", start, end, step, scans=(0, 1))
    assert len(radars) == 2
    assert radars[0]["key"].endswith("000000")
    assert radars[0]["kwargs"] == {"scans": (0, 1)}


def test_read_time_span_end_now(monkeypatch):
    register_source(_DummySource)
    fixed = datetime(2025, 1, 1, 12, 0)

    class _FixedDummy(_DummySource):
        def read_time_span(self, site, start, end, step, **kwargs):
            assert end is fixed
            return ["ok"]

    register_source(_FixedDummy)
    monkeypatch.setattr(
        "pyart.io.remote.datetime",
        type("DT", (), {"utcnow": staticmethod(lambda: fixed)}),
    )
    out = read_time_span("dummy", "Z_KMTX", fixed, "now", timedelta(hours=1))
    assert out == ["ok"]


def test_base_source_methods_raise_not_implemented():
    base = _BaseSource()
    with pytest.raises(NotImplementedError):
        base.list_sites()
    with pytest.raises(NotImplementedError):
        base.list_files("s", 0, 1, 1)
    with pytest.raises(NotImplementedError):
        base.fetch("k")
    with pytest.raises(NotImplementedError):
        base.read("k")


def test_base_source_retry_raises_remote_error():
    base = _BaseSource()

    def _boom(timeout=None):
        raise OSError("nope")

    with pytest.raises(RemoteDataError):
        base._retry(_boom, retries=1, backoff=0.01)


def test_radar_site_namedtuple():
    s = RadarSite("A", 1.0, 2.0, 3.0, "S", "CN")
    assert s.name == "A"
    assert s.band == "S"
    assert s.country == "CN"
