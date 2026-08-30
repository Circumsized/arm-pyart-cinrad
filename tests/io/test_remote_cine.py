"""Offline CineSource tests (no network)."""

import os
from datetime import datetime, timedelta

import pytest

from pyart.io.remote import CineSource, RemoteDataError, get_source, list_sources


@pytest.fixture
def cine_tree(tmp_path):
    files = {
        "BJ_SAMPLE_202006010000.cine": "beijing",
        "BJ_SAMPLE_202006010030.cine": "beijing",
        "SH_SAMPLE_202006010000.cine": "shanghai",
    }
    for name, sub in files.items():
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(b"cinrad-data")
    return tmp_path


def test_cine_registered():
    assert "cine" in list_sources()


def test_cine_list_sites(cine_tree):
    src = CineSource(root=str(cine_tree))
    sites = src.list_sites()
    # site derived from filename prefix before '_'
    assert "BJ" in sites and "SH" in sites


def test_cine_list_files_filters(cine_tree):
    src = CineSource(root=str(cine_tree))
    # set a fixed mtime for deterministic filtering
    ts = datetime(2020, 6, 1, 0, 0).timestamp()
    for name in ("BJ_SAMPLE_202006010000.cine",
                 "BJ_SAMPLE_202006010030.cine",
                 "SH_SAMPLE_202006010000.cine"):
        os.utime(os.path.join(cine_tree, "beijing" if name.startswith("BJ")
                              else "shanghai", name), (ts, ts))
    start = datetime(2020, 6, 1, 0, 0)
    end = datetime(2020, 6, 1, 1, 0)
    files = src.list_files("BJ", start, end, timedelta(minutes=30))
    assert len(files) == 2


def test_cine_fetch_returns_path(cine_tree):
    src = CineSource(root=str(cine_tree))
    p = str(cine_tree / "beijing" / "BJ_SAMPLE_202006010000.cine")
    assert src.fetch(p) == p


def test_cine_fetch_missing_raises(tmp_path):
    src = CineSource(root=str(tmp_path))
    with pytest.raises(RemoteDataError):
        src.fetch(str(tmp_path / "missing.cine"))


def test_cine_read_delegates(cine_tree, monkeypatch):
    src = CineSource(root=str(cine_tree))
    p = str(cine_tree / "beijing" / "BJ_SAMPLE_202006010000.cine")
    monkeypatch.setattr("pyart.io.auto_read.read",
                        lambda local, **kw: "RADAR-OBJECT")
    assert src.read(p) == "RADAR-OBJECT"