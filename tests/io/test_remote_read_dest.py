"""Lock the ``read(key, dest=None, **kwargs)`` contract for remote sources."""

from pyart.io.remote import NmcCnSource


def test_read_dest_forwarded(monkeypatch, tmp_path):
    src = NmcCnSource()

    def fake_http(url, timeout=15.0):
        return b"PNG-DATA"

    monkeypatch.setattr(src, "_http_get", fake_http)
    dest = str(tmp_path / "cached.png")
    out = src.read("http://x/x.png", dest=dest)
    # dest is respected: fetch returns the provided local path
    assert out == dest


def test_read_dest_none_uses_cache(monkeypatch, tmp_path):
    src = NmcCnSource()
    monkeypatch.setattr(src, "_http_get", lambda url, timeout=15.0: b"PNG-DATA")
    # Redirect the cache directory into tmp_path so the result is deterministic
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path / "cache"))
    out = src.read("http://x/day/2020060100.png")
    assert out.endswith("2020060100.png")
    assert str(tmp_path) in out


def test_read_dest_not_leaked_to_reader(monkeypatch, tmp_path):
    # CmaMosSource.read passes **kwargs to read_cinrad; dest must not leak.
    from pyart.io.remote import CmaMosSource

    src = CmaMosSource()
    seen = {}

    monkeypatch.setattr(src, "_http_get", lambda url, timeout=15.0: b"fake-cinrad")
    monkeypatch.setattr(
        "pyart.io.cinrad_bridge.read_cinrad",
        lambda local, **kw: seen.setdefault("kw", kw),
    )
    dest = str(tmp_path / "cma.bin")
    src.read("http://data.cma.cn/radar/sh/20200601/0000.bin", dest=dest)
    assert "dest" not in seen.get("kw", {})
