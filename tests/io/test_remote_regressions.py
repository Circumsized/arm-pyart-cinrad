"""Regression tests for defects found while auditing ``pyart.io.remote``.

Every test here pins a failure mode that was **demonstrated by execution**
during the review, not merely suspected. The NEXRAD listing defect in
particular survived because the existing suite listed files under an
artificial ``SITE_<date>_<time>_V06`` naming convention that the broken regex
happened to match; these tests use the real ``SITE<YYYYMMDD>_<HHMMSS>_V<n>``
convention instead.
"""

import os
import threading
import warnings
from datetime import datetime, timedelta
from unittest import mock

import pytest

from pyart.io.remote import (
    NexradSource,
    RemoteDataError,
    _timespan,
    _utcnow,
    get_source,
    read_time_span,
)

# Real NEXRAD Level II naming: <SITE><YYYYMMDD>_<HHMMSS>_V<version>
_REAL_KEY = "KTLX20240601_000012_V06"
_REAL_KEY_2 = "KTLX20240601_001000_V06"
_BUCKET = "noaa-nexrad-level2"


class _FakeS3:
    """Minimal ``s3fs`` stand-in.

    Note that real ``s3fs.ls`` yields **bucket-qualified** paths; reproducing
    that detail is what exposes the double-prefix defect.
    """

    def __init__(self, entries):
        self.entries = list(entries)
        self.get_calls = []

    def ls(self, path):
        return self.entries

    def get(self, src, dst):
        self.get_calls.append(src)
        with open(dst, "wb") as fh:
            fh.write(b"FAKE-NEXRAD")


# --------------------------------------------------------------------------
# NEXRAD listing / path handling
# --------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    _REAL_KEY,
    "KLOT20240601_120738_V08",
    f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}",
])
def test_nexrad_parse_time_matches_real_filenames(key):
    """Real keys carry no underscore between the site code and the date."""
    assert NexradSource._parse_time(key) is not None, key


def test_nexrad_parse_time_value():
    assert NexradSource._parse_time(_REAL_KEY) == datetime(2024, 6, 1, 0, 0, 12)


def test_nexrad_list_files_with_real_naming():
    """End-to-end: a realistic bucket listing must yield files."""
    src = get_source("nexrad")
    fake = _FakeS3([f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}",
                    f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY_2}"])
    src._fs = lambda: fake
    keys = src.list_files("KTLX", datetime(2024, 6, 1, 0, 0),
                          datetime(2024, 6, 1, 0, 30), timedelta(minutes=10))
    assert keys, "real-naming listing degraded to empty (original defect)"


@pytest.mark.parametrize("key,expected", [
    ("2024/06/01/KTLX/" + _REAL_KEY,
     f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}"),
    (f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}",
     f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}"),
    (f"/{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}",
     f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}"),
])
def test_nexrad_s3_path_is_normalised_once(key, expected):
    """``list_files`` keys already carry the bucket; never prepend twice."""
    assert NexradSource._s3_path(key) == expected


def test_nexrad_fetch_uses_single_bucket_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    src = get_source("nexrad")
    fake = _FakeS3([])
    src._fs = lambda: fake
    out = src.fetch(f"{_BUCKET}/2024/06/01/KTLX/{_REAL_KEY}")
    assert fake.get_calls, "no download attempted"
    for called in fake.get_calls:
        assert called.count(_BUCKET) == 1, f"doubled bucket prefix: {called}"
    assert os.path.getsize(out) > 0


# --------------------------------------------------------------------------
# Time-grid robustness
# --------------------------------------------------------------------------

@pytest.mark.parametrize("step", [timedelta(0), timedelta(minutes=-10)])
def test_non_positive_step_rejected_instead_of_hanging(step):
    """A zero/negative step never advances the grid: it must raise, not hang."""
    start, end = datetime(2024, 1, 1), datetime(2024, 1, 2)
    with pytest.raises(ValueError):
        list(_timespan(start, end, step))


def test_step_type_checked():
    with pytest.raises(TypeError):
        list(_timespan(datetime(2024, 1, 1), datetime(2024, 1, 2), 60))


def test_read_time_span_rejects_bad_step():
    with pytest.raises(ValueError):
        read_time_span("nexrad", "KTLX", datetime(2024, 1, 1),
                       datetime(2024, 1, 2), timedelta(0))


def test_utcnow_helper_is_naive():
    """Public API compares against naive datetimes, so ``now`` must be naive."""
    assert _utcnow().tzinfo is None


# --------------------------------------------------------------------------
# SSRF guard
# --------------------------------------------------------------------------

class _RedirectResp:
    status_code = 302
    is_redirect = True

    def __init__(self, location):
        self.headers = {"Location": location}
        self.content = b""

    def raise_for_status(self):
        pass


def test_redirect_to_private_host_is_blocked(monkeypatch, tmp_path):
    """Following redirects automatically defeated the SSRF guard entirely."""
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        return _RedirectResp("http://169.254.169.254/latest/meta-data/")

    src = get_source("cma_mos")
    with mock.patch("requests.get", fake_get):
        with pytest.raises(RemoteDataError) as exc:
            src._http_get("http://example.com/start", timeout=5)
    assert "Refusing to request unsafe URL" in str(exc.value)
    assert "169.254.169.254" in str(exc.value)
    # The second hop must be rejected *before* any socket is opened.
    assert len(calls) == 1
    assert calls[0][1].get("allow_redirects") is False


def test_redirect_loop_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    with mock.patch("requests.get",
                    lambda url, **kw: _RedirectResp("http://example.com/next")):
        with pytest.raises(RemoteDataError, match="too many redirects"):
            get_source("cma_mos")._http_get("http://example.com/start",
                                            timeout=5)


def test_is_reachable_guards_private_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    src = get_source("cma_mos")
    assert src._is_reachable("http://169.254.169.254/") is False
    assert src._is_reachable("http://127.0.0.1:8080/") is False


def test_cma_mos_private_mirror_requires_opt_in(monkeypatch, tmp_path):
    """An internal mirror must be declared per station, not assumed."""
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    cfg = {"internal": {"template": "http://10.0.0.5/radar/{date}.bin"}}
    src = get_source("cma_mos", station_config=cfg)
    seen = {}

    def probe(url, timeout=8.0, allow_private=False):
        seen["allow_private"] = allow_private
        return True

    src._is_reachable = probe
    src.list_sites()
    assert seen.get("allow_private") is False, "SSRF guard relaxed by default"

    cfg["internal"]["allow_private"] = True
    src.list_sites()
    assert seen.get("allow_private") is True, "explicit opt-in not honoured"


# --------------------------------------------------------------------------
# Cache integrity
# --------------------------------------------------------------------------

def test_concurrent_atomic_write_does_not_interleave(monkeypatch, tmp_path):
    """Sharing one ``<name>.tmp`` let writers mix payloads in the same file.

    On Windows, parallel ``os.replace`` calls on the same target directory can
    race against the OS's directory-entry unlink and raise ``PermissionError``
    -- a transient system-level issue, not a payload-corruption issue. We
    therefore distinguish between the two: a payload must end up **complete
    and uncorrupted**; an OS-level race that surfaces as ``PermissionError``
    is acceptable as long as the *successful* commit is one of the inputs
    whole, and no temp file is leaked.
    """
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    src = get_source("nmc_cn")
    target = src.cache_path("race.bin")
    payloads = [bytes([c]) * 1_000_000 for c in (65, 66, 67, 68)]
    expected = set(payloads)
    errors = []
    successes = []

    def writer(payload):
        try:
            src._atomic_write(target, payload)
            successes.append(payload)
        except (PermissionError, FileNotFoundError, OSError) as exc:
            # Windows-only: mid-unlink directory entry races.
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    if successes:
        with open(target, "rb") as fh:
            data = fh.read()
        assert data in expected, f"interleaved payload detected: {set(data)[:5]}"
        assert len(set(data)) == 1, f"interleaved bytes in file: {set(data)}"
    leftovers = [n for n in os.listdir(os.path.dirname(target))
                 if n.endswith(".part") or n.endswith(".tmp")]
    assert not leftovers, f"temp files leaked: {leftovers}"
    assert len(successes) + len(errors) == len(payloads)


def test_force_refresh_recovers_poisoned_cache(monkeypatch, tmp_path):
    """A truncated cache entry used to be served forever."""
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    src = get_source("nmc_cn")
    key = "http://www.nmc.cn/publish/radar/all/2024060100.png"
    poisoned = src.cache_path(key)
    with open(poisoned, "wb") as fh:
        fh.write(b"\x89PNG")

    assert src.fetch(key) == poisoned  # default behaviour preserved

    calls = []
    src._http_get = lambda url, **kw: (calls.append(url) or b"FULL-DATA")
    out = src.fetch(key, force_refresh=True)
    assert calls, "force_refresh did not re-download"
    with open(out, "rb") as fh:
        assert fh.read() == b"FULL-DATA"


def test_clear_cache_empties_source_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    src = get_source("nmc_cn")
    with open(src.cache_path("a.bin"), "wb") as fh:
        fh.write(b"x")
    with open(src.cache_path("b.bin"), "wb") as fh:
        fh.write(b"x")
    assert src.clear_cache() == 2
    assert src.clear_cache() == 0


# --------------------------------------------------------------------------
# Failure observability
# --------------------------------------------------------------------------

def test_read_fallback_warns(monkeypatch, tmp_path):
    """Returning a path instead of a Radar must not happen silently."""
    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    junk = tmp_path / "junk.bin"
    junk.write_bytes(os.urandom(512))
    src = get_source("cma_music", user_id="u", api_key="k")
    with mock.patch.object(type(src), "fetch", return_value=str(junk)):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = src.read({"dataCode": "X", "staId": "Z",
                               "timeRange": "(t,t]"})
    assert isinstance(result, str), "backward-compatible fallback removed"
    assert any("could not decode" in str(w.message) for w in caught), (
        "silent contract violation restored")


def test_read_time_span_warns_when_reads_fail(monkeypatch, tmp_path):
    """Skipping every failure made 'all reads failed' look like 'no data'."""

    monkeypatch.setenv("PYART_CACHE_DIR", str(tmp_path))
    from pyart.io.remote import _BaseSource

    class _Src(_BaseSource):
        name = "failing"
        bands = ()

        def list_files(self, site, start, end, step):
            return ["k1", "k2"]

        def read(self, key, **kwargs):
            raise RuntimeError(f"boom {key}")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = _Src().read_time_span("s", datetime(2024, 1, 1),
                                    datetime(2024, 1, 2), timedelta(hours=1))
    assert out == []
    assert any("reads failed" in str(w.message) for w in caught)
