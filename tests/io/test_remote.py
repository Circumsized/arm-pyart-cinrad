"""Contract tests for the pyart.io.remote acquisition layer."""

from datetime import datetime, timedelta

import pytest

from pyart.io.remote import (
    RadarSite,
    RadarSource,
    RemoteDataError,
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
    monkeypatch.setattr("pyart.io.remote._utcnow", lambda: fixed)
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


def test_cache_path_sanitizes_separators():
    # CR-004: backslash/forward-slash traversal keys stay inside cache dir
    import os
    base = _BaseSource()
    for key in ("..\\..\\evil", "../../evil", "C:\\Windows\\x"):
        p = base.cache_path(key)
        real = os.path.realpath(p)
        real_base = os.path.realpath(base.cache_dir())
        assert real.startswith(real_base + os.sep) or real == real_base


def test_cache_path_stays_inside_cache_dir():
    # CR-004: defense-in-depth — realpath must never escape cache dir
    import os
    base = _BaseSource()
    real_base = os.path.realpath(base.cache_dir())
    for key in ("a/b/c.bin", "a\\b\\c.bin", "x:y", "normal.bin"):
        real = os.path.realpath(base.cache_path(key))
        assert real.startswith(real_base + os.sep) or real == real_base


def test_validate_url_rejects_unsafe():
    # CR-005: SSRF guard
    base = _BaseSource()
    assert base._validate_url("file:///etc/passwd") is False
    assert base._validate_url("http://127.0.0.1/x") is False
    assert base._validate_url("http://169.254.169.254/latest") is False
    assert base._validate_url("ftp://example.com/x") is False
    assert base._validate_url("http://example.com/x") is True


def test_validate_url_allow_private():
    base = _BaseSource()
    assert base._validate_url("http://10.0.0.1/x", allow_private=True) is True


def test_atomic_write(tmp_path):
    # CR-008: atomic write produces complete content and no .tmp leftover
    base = _BaseSource()
    target = tmp_path / "f.bin"
    base._atomic_write(str(target), b"hello")
    assert target.read_bytes() == b"hello"
    assert not (tmp_path / "f.bin.tmp").exists()
