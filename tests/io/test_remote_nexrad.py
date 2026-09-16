"""NexradSource tests using a fake s3fs filesystem (no network)."""

from datetime import datetime, timedelta

import pytest

from pyart.io.remote import NexradSource, get_source, list_sources


class _FakeFS:
    """Minimal stand-in for s3fs.S3FileSystem with a canned ls response."""

    def __init__(self, listing):
        # listing: dict prefix -> list of entries
        self._listing = listing

    def ls(self, path):
        # s3fs returns paths like "noaa-nexrad-level2/2020/01/01/KLOT/..."
        return self._listing.get(path, [])


def test_nexrad_registered():
    assert "nexrad" in list_sources()


def test_nexrad_get_source():
    src = get_source("nexrad")
    assert isinstance(src, NexradSource)
    assert src.bands == ("S",)


def test_nexrad_parse_time():
    key = "2020/01/01/KLOT/KLOT_20200101_000001_V06.gz"
    t = NexradSource._parse_time(key)
    assert t == datetime(2020, 1, 1, 0, 0, 1)


def test_nexrad_parse_time_bad():
    assert NexradSource._parse_time("not-a-nexrad-key") is None


def test_nexrad_list_sites():
    sites = NexradSource().list_sites()
    assert "KLOT" in sites
    assert sites["KLOT"].band == "S"


def test_nexrad_list_files_mock(monkeypatch):
    # Two days of KLOT data, hourly files
    keys = []
    for day in ("20200101",):
        for hh in ("000000", "003000", "010000", "013000", "020000"):
            keys.append(f"noaa-nexrad-level2/2020/01/01/KLOT/KLOT_{day}_{hh}_V06.gz")
    listing = {"noaa-nexrad-level2/2020/01/01/KLOT": keys}

    src = NexradSource()
    monkeypatch.setattr(src, "_fs", lambda: _FakeFS(listing))

    start = datetime(2020, 1, 1, 0, 0)
    end = datetime(2020, 1, 1, 3, 0)
    step = timedelta(hours=1)
    files = src.list_files("KLOT", start, end, step)

    # 3 hourly grid points -> 3 files, nearest to each hour
    assert len(files) == 3
    assert all("KLOT_20200101" in f for f in files)
    # nearest to 00:00 is 000000, to 01:00 is 010000, to 02:00 is 020000
    assert "_000000_" in files[0]
    assert "_010000_" in files[1]
    assert "_020000_" in files[2]


def test_nexrad_list_files_missing_day(monkeypatch):
    # Empty listing simulates a day with no data
    src = NexradSource()
    monkeypatch.setattr(src, "_fs", lambda: _FakeFS({}))
    start = datetime(2020, 1, 1, 0, 0)
    end = datetime(2020, 1, 1, 1, 0)
    assert src.list_files("KABCD", start, end, timedelta(minutes=30)) == []


def test_nexrad_fetch_uses_cache(monkeypatch, tmp_path):
    src = NexradSource()
    # Pretend the file is already cached.
    local = tmp_path / "KLOT_20200101_000000_V06.gz"
    local.write_bytes(b"already-here")
    out = src.fetch("2020/01/01/KLOT/KLOT_20200101_000000_V06.gz", dest=str(local))
    assert out == str(local)
    # _fs must not be called when the file is cached
    monkeypatch.setattr(src, "_fs", lambda: pytest.fail("should not touch s3"))


def test_nexrad_read_delegates(monkeypatch, tmp_path):
    src = NexradSource()

    captured = {}

    def fake_read(local, **kwargs):
        captured["local"] = local
        captured["kwargs"] = kwargs
        return "RADAR"

    # fetch returns a fake local path
    monkeypatch.setattr(src, "fetch", lambda key, dest=None: str(tmp_path / "fake.gz"))
    monkeypatch.setattr("pyart.io.nexrad_archive.read_nexrad_archive", fake_read)

    out = src.read("2020/01/01/KLOT/KLOT_20200101_000000_V06.gz", scans=(0, 1))
    assert out == "RADAR"
    assert captured["kwargs"]["scans"] == [0, 1]
    assert captured["kwargs"]["storage_options"] == {"anon": True}


def test_nexrad_missing_s3fs(monkeypatch):
    src = NexradSource()
    import sys

    monkeypatch.setitem(sys.modules, "s3fs", None)
    with pytest.raises(ImportError):
        src._fs()
