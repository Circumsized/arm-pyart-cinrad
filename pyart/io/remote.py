"""
pyart.io.remote
===============

A thin, source-agnostic data acquisition layer for radar data.

The model mirrors the three-step pattern popularised by
``zssherman/pyart_animation`` -- *list files in a time span* -> *fetch each
to a local cache* -> *read into a* :class:`pyart.core.Radar` -- but is
generalised so that NEXRAD (S3), the Chinese Meteorological Administration
public endpoints and offline CINE files all share one interface.

The public entry points are:

* :class:`RadarSource` -- a :class:`typing.Protocol` describing the contract
* :func:`list_sources` / :func:`get_source` -- registry access
* :func:`read_time_span` -- convenience helper: pull every sweep in a time
  range from a named source and return ``list[Radar]``

Concrete sources (NEXRAD, NMC, CMA MOS, offline CINE) are registered via
:func:`register_source` and are imported lazily so that a missing optional
dependency (``s3fs``, ``requests``) never breaks ``import pyart``.
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timedelta
from typing import NamedTuple, Protocol, runtime_checkable

import numpy as np  # noqa: F401  (used by raster decode helpers)


def _utcnow():
    """Naive UTC ``datetime``.

    ``datetime.utcnow()`` is deprecated on Python 3.12+; this helper keeps the
    *naive* semantics required by the public API, where callers pass naive
    ``datetime`` objects that are compared against ``start``/``end``. Using an
    aware datetime here would raise ``TypeError`` on those comparisons.
    """
    from datetime import timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


class RemoteDataError(RuntimeError):
    """Raised when a remote source cannot be reached or returns bad data."""


class RadarSite(NamedTuple):
    """Minimal site descriptor returned by :meth:`RadarSource.list_sites`."""

    name: str
    latitude: float
    longitude: float
    altitude: float
    band: str
    country: str = ""


@runtime_checkable
class RadarSource(Protocol):
    """Contract for a pluggable radar data source.

    Implementations may subclass :class:`_BaseSource` for shared caching and
    retry helpers, but are not required to -- any object exposing these
    attributes/methods is accepted by :func:`get_source`.
    """

    name: str
    bands: tuple

    def list_sites(self) -> dict:  # pragma: no cover - protocol
        ...

    def list_files(self, site, start, end, step) -> list:  # pragma: no cover
        ...

    def fetch(self, key, dest=None) -> str:  # pragma: no cover - protocol
        ...

    def read(self, key, dest=None, **kwargs):  # pragma: no cover - protocol
        ...

    def read_time_span(
        self, site, start, end, step, **kwargs
    ) -> list: ...  # pragma: no cover - protocol


class _BaseSource:
    """Optional mixin providing cache-directory and HTTP-retry helpers.

    Concrete sources inherit this for DRY behaviour (cache path resolution,
    bounded retries with exponential back-off) but the public contract is the
    :class:`RadarSource` protocol, so third-party sources need not subclass
    this class.
    """

    name = "base"
    bands: tuple = ()

    def cache_dir(self) -> str:
        base = os.environ.get(
            "PYART_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".pyart", "cache")
        )
        path = os.path.join(base, self.name)
        os.makedirs(path, exist_ok=True)
        return path

    def cache_path(self, key: str) -> str:
        # Sanitize both path separators so a caller-controlled key can never
        # escape the cache directory (Windows accepts both '/' and '\\').
        safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        local = os.path.join(self.cache_dir(), safe)
        # Defend against any remaining traversal: resolve and verify the final
        # path still lives inside the cache directory.
        real_local = os.path.realpath(local)
        real_base = os.path.realpath(self.cache_dir())
        if not (real_local == real_base or real_local.startswith(real_base + os.sep)):
            raise RemoteDataError(f"cache key escapes cache directory: {key!r}")
        return local

    def _cache_hit(self, local, force_refresh: bool = False) -> bool:
        """Return True when ``local`` already holds a usable cached copy.

        Only an existing, non-empty regular file counts. ``force_refresh``
        gives callers a way out of a *poisoned* entry -- a truncated file
        written by an older release or left behind by disk corruption is
        otherwise served forever, because the size probe cannot tell it apart
        from a complete download.
        """
        if force_refresh:
            return False
        try:
            return os.path.isfile(local) and os.path.getsize(local) > 0
        except OSError:
            return False

    def clear_cache(self) -> int:
        """Delete every cached file for this source; return the count removed.

        Recovery path for a poisoned cache; safer than hand-editing
        ``$PYART_CACHE_DIR`` because it is scoped to one source directory.
        """
        removed = 0
        directory = self.cache_dir()
        try:
            names = os.listdir(directory)
        except OSError:
            return 0
        for name in names:
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path):
                    os.unlink(path)
                    removed += 1
            except OSError:
                continue
        return removed

    @staticmethod
    def _retry(
        callable_,
        *args,
        retries: int = 2,
        backoff: float = 1.5,
        timeout: float = 15.0,
        **kwargs,
    ):
        """Call ``callable_(*args, **kwargs)`` with bounded retries.

        ``timeout`` is advisory: callers that issue HTTP/S3 requests should
        pass it through to the underlying client. ``RemoteDataError`` is
        raised after the final failed attempt.
        """
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return (
                    callable_(*args, timeout=timeout, **kwargs)
                    if _accepts_timeout(callable_)
                    else callable_(*args, **kwargs)
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(backoff**attempt)
        raise RemoteDataError(
            f"{getattr(callable_, '__name__', callable_)} failed after "
            f"{retries + 1} attempts: {last_exc}"
        ) from last_exc

    # The four protocol methods have sensible default implementations that
    # raise NotImplementedError so subclasses only override what they need.
    def list_sites(self) -> dict:
        raise NotImplementedError

    def list_files(self, site, start, end, step) -> list:
        raise NotImplementedError

    def fetch(self, key, dest=None) -> str:
        raise NotImplementedError

    def read(self, key, dest=None, **kwargs):
        raise NotImplementedError

    def read_time_span(self, site, start, end, step, **kwargs) -> list:
        keys = self.list_files(site, start, end, step)
        radars = []
        failures = []
        for key in keys:
            try:
                radars.append(self.read(key, **kwargs))
            except Exception as exc:  # noqa: BLE001
                failures.append((key, exc))
                continue
        if failures:
            # Skipping every failure silently made "all reads failed" look
            # exactly like "no data in this window". Keep skipping (the
            # documented behaviour) but make it observable.
            import warnings as _w

            detail = "; ".join(
                f"{k!r}: {type(e).__name__}: {e}" for k, e in failures[:3]
            )
            if len(failures) > 3:
                detail += f"; ... (+{len(failures) - 3} more)"
            _w.warn(
                f"{type(self).__name__}.read_time_span: {len(failures)} of "
                f"{len(keys)} reads failed and were skipped ({detail}).",
                RuntimeWarning,
            )
        return radars

    # -- HTTP helpers -------------------------------------------------------
    @staticmethod
    def _validate_url(url, allow_private=False):
        """Return True when ``url`` is safe to request.

        Restricts the scheme to ``http``/``https`` and (unless
        ``allow_private``) rejects loopback, link-local, private, and cloud
        metadata hosts. This is an SSRF guard for caller-supplied URLs
        (``key['url']``, ``station_config['template']``).
        """
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
        except Exception:  # noqa: BLE001
            return False
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if allow_private:
            return True
        host = parsed.hostname.lower()
        if host in ("localhost", "127.0.0.1", "::1", "169.254.169.254"):
            return False
        import ipaddress

        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # Not a raw IP; could be a DNS name. Allow (we cannot resolve it
            # here without a DNS call, which itself is out of scope).
            return True
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )

    def _http_get(self, url, timeout=15.0, allow_private=False, max_redirects=3):
        """GET ``url`` and return bytes, with bounded retries.

        Redirects are followed **manually** and re-validated at every hop.
        Letting ``requests`` follow them (its default) defeated the SSRF guard
        completely: a validated public URL could answer 302 to
        ``http://169.254.169.254/`` and the client fetched it anyway -- see the
        ``_validate_url`` docstring for the residual hostname-resolution risk.
        """
        try:
            import requests
        except ImportError as exc:
            raise ImportError(
                "requests is required for HTTP sources; install it with "
                '"pip install arm_pyart[remote]"'
            ) from exc
        from urllib.parse import urljoin

        for _hop in range(max_redirects + 1):
            if not self._validate_url(url, allow_private=allow_private):
                raise RemoteDataError(f"Refusing to request unsafe URL: {url!r}")

            def _do(timeout=None):
                r = requests.get(url, timeout=timeout, allow_redirects=False)
                r.raise_for_status()
                return r

            response = self._retry(_do, retries=2, timeout=timeout)
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise RemoteDataError(
                        f"Redirect without Location header from {url!r}"
                    )
                url = urljoin(url, location)
                continue
            return response.content
        raise RemoteDataError(
            f"too many redirects (>{max_redirects}) while fetching {url!r}"
        )

    def _http_get_json(self, url, timeout=15.0, allow_private=False):
        import json

        return json.loads(
            self._http_get(url, timeout=timeout, allow_private=allow_private).decode(
                "utf-8"
            )
        )

    @staticmethod
    def _is_reachable(url, timeout=8.0, allow_private=False):
        """HEAD-probe ``url``; return True when it answers below 5xx.

        The URL is run through :meth:`_validate_url` before any socket is
        opened -- the probe previously requested arbitrary caller-supplied
        hosts with no guard whatsoever. ``allow_private`` is opt-in for
        operator-configured mirrors (see :class:`CmaMosSource`).
        """
        if not _BaseSource._validate_url(url, allow_private=allow_private):
            return False
        try:
            import requests
        except ImportError:
            return False
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=False)
            return r.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _atomic_write(local, data):
        """Write ``data`` to ``local`` atomically.

        Stages the payload in a **unique** sibling temp file and commits it with
        ``os.replace``, so concurrent readers never observe a half-written cache
        entry and concurrent writers never share a temp name. The temp file is
        removed on both success and failure.
        """
        tmp = _unique_tmp(local)
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, local)
        finally:
            # ``os.replace`` on Windows can transiently hold the directory
            # entry, so the best-effort unlink is now also re-issued once the
            # replace is done. Concurrent writers still cannot collide on a
            # shared name (each has its own ``_unique_tmp``), but this avoids
            # spurious PermissionError when the OS is mid-unlink.
            _silent_unlink(tmp)
            # On Windows, an immediately-prior unlink may race with the
            # directory entry being committed; a second pass is harmless.
            _silent_unlink(tmp)


def _accepts_timeout(func) -> bool:
    import inspect

    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return "timeout" in sig.parameters


def _unique_tmp(local: str) -> str:
    """Return a unique sibling temp path for ``local``.

    All writers previously shared ``<local>.tmp``; two concurrent writers then
    truncated and wrote the *same* file, and ``os.replace`` committed the
    interleaved result as if it were valid content (verified by a 4-thread
    probe). Unique names make each writer independent.
    """
    directory = os.path.dirname(os.path.abspath(local)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(local) + ".", suffix=".part", dir=directory
    )
    os.close(fd)
    return tmp


def _silent_unlink(path: str) -> None:
    """Remove ``path``, ignoring anything already gone."""
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_SOURCES: dict = {}


def register_source(cls):
    """Class decorator: register a concrete source under ``cls.name``."""
    if not getattr(cls, "name", None):
        raise ValueError("source class must define a non-empty 'name'")
    _SOURCES[cls.name] = cls
    return cls


def list_sources() -> list:
    """Return the sorted names of all registered radar sources."""
    return sorted(_SOURCES)


def get_source(name: str, **kwargs) -> RadarSource:
    """Instantiate a registered source by name.

    Parameters
    ----------
    name : str
        Registered source name, e.g. ``'nexrad'``.
    **kwargs
        Forwarded to the source constructor.

    Raises
    ------
    KeyError
        If ``name`` is not registered.
    """
    if name not in _SOURCES:
        raise KeyError(f"Unknown radar source '{name}'. Available: {list_sources()}")
    return _SOURCES[name](**kwargs)


def read_time_span(source, site, start, end, step, **kwargs) -> list:
    """Pull every sweep in ``[start, end)`` at ``step`` cadence.

    Parameters
    ----------
    source : str or RadarSource
        Source name or instance.
    site : str
        Site identifier (e.g. NEXRAD ICAO code or CMA station id).
    start, end : datetime.datetime
        Time range. ``end='now'`` (string) resolves to the current naive UTC
        time (see :func:`_utcnow`), mirroring ``zssherman/pyart_animation``.
    step : datetime.timedelta
        Cadence between scans.
    **kwargs
        Forwarded to ``source.read`` (e.g. ``scans=(0, 1)`` for NEXRAD).

    Returns
    -------
    radars : list of Radar
        One entry per successfully read file; failed reads are skipped (a
        warning is emitted when any read fails, so a short result can be
        distinguished from an empty time window).

    Raises
    ------
    TypeError, ValueError
        If ``step`` is not a strictly positive :class:`~datetime.timedelta`.
    """
    if isinstance(source, str):
        source = get_source(source)
    if not isinstance(step, timedelta):
        raise TypeError(f"step must be a datetime.timedelta, got {type(step).__name__}")
    if step <= timedelta(0):
        raise ValueError(
            f"step must be strictly positive, got {step!r}: a non-positive "
            "step never advances the time grid and never terminates."
        )
    if isinstance(end, str) and end.lower() == "now":
        end = _utcnow()
    return source.read_time_span(site, start, end, step, **kwargs)


__all__ = [
    "RemoteDataError",
    "RadarSite",
    "RadarSource",
    "register_source",
    "list_sources",
    "get_source",
    "read_time_span",
    "NexradSource",
    "NmcCnSource",
    "CmaMusicSource",
    "CmaMosSource",
    "CineSource",
]


# ---------------------------------------------------------------------------
# NEXRAD Level II source (anonymous AWS S3)
# ---------------------------------------------------------------------------

import re

_NEXRAD_BUCKET = "noaa-nexrad-level2"
# Real NEXRAD Level II keys look like ``KTLX20240601_000012_V06``:
# <SITE><YYYYMMDD>_<HHMMSS>_V<version>. The date follows the site code with
# **no** separating underscore, so the pattern must not require one -- an
# earlier revision anchored on ``_(\d{8})`` and therefore matched nothing,
# silently degrading every listing to an empty result.
_NEXRAD_KEY_RE = re.compile(r"(\d{8})_(\d{6})_V")
# A small, non-exhaustive set of well-known WSR-88D ICAO sites. The full
# network is ~160 sites; callers may pass any valid ICAO code to list_files.
_NEXRAD_SITES = {
    "KLOT": RadarSite("KLOT", 41.604, -87.539, 200.0, "S", "US"),
    "KTLX": RadarSite("KTLX", 35.333, -97.278, 390.0, "S", "US"),
    "KMTX": RadarSite("KMTX", 41.033, -112.044, 1700.0, "S", "US"),
    "KFWS": RadarSite("KFWS", 32.573, -97.303, 232.0, "S", "US"),
    "KHGX": RadarSite("KHGX", 29.472, -95.079, 16.0, "S", "US"),
    "KEWX": RadarSite("KEWX", 29.704, -98.029, 198.0, "S", "US"),
}


def _timespan(start, end, step):
    """Yield ``datetime`` grid points from ``start`` to ``end`` at ``step``.

    Raises
    ------
    ValueError
        If ``step`` is not strictly positive. A zero or negative step never
        advances the grid point, so the loop runs forever -- verified: both
        ``timedelta(0)`` and a negative step hang the interpreter until the
        process is killed.
    """
    if not isinstance(step, timedelta):
        raise TypeError(f"step must be a datetime.timedelta, got {type(step).__name__}")
    if step <= timedelta(0):
        raise ValueError(
            f"step must be strictly positive, got {step!r}: a non-positive "
            "step never advances the time grid and never terminates."
        )
    t = start
    while t < end:
        yield t
        t = t + step


@register_source
class NexradSource(_BaseSource):
    """NEXRAD Level II archive source on the public anonymous AWS S3 bucket.

    Mirrors the access pattern of ``zssherman/pyart_animation``: list keys
    under ``s3://noaa-nexrad-level2/{Y}/{m}/{d}/{SITE}/`` for each day in the
    requested span, pick one file per ``step`` grid point, fetch to the local
    pooch-style cache, and read with :func:`pyart.io.read_nexrad_archive`.
    """

    name = "nexrad"
    bands = ("S",)

    def __init__(self, anon: bool = True):
        self._anon = anon

    # -- S3 plumbing --------------------------------------------------------
    def _fs(self):
        try:
            import s3fs
        except ImportError as exc:
            raise ImportError(
                "s3fs is required for NexradSource; install it with "
                '"pip install s3fs"'
            ) from exc
        return s3fs.S3FileSystem(anon=self._anon)

    def list_sites(self) -> dict:
        return dict(_NEXRAD_SITES)

    @staticmethod
    def _parse_time(key):
        m = _NEXRAD_KEY_RE.search(key)
        if m is None:
            return None
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            return None

    def _list_prefix(self, fs, prefix):
        path = f"{_NEXRAD_BUCKET}/{prefix}"
        try:
            entries = fs.ls(path)
        except FileNotFoundError:
            return []
        out = []
        for entry in entries:
            if not entry.endswith("/"):
                out.append(entry)
        return out

    def list_files(self, site, start, end, step) -> list:
        site = site.upper()
        if isinstance(end, str) and end.lower() == "now":
            end = _utcnow()
        fs = self._fs()
        keys = []
        day = start.date()
        one_day = timedelta(days=1)
        while day <= end.date():
            prefix = f"{day:%Y/%m/%d}/{site}"
            keys.extend(self._list_prefix(fs, prefix))
            day = day + one_day
        parsed = []
        for key in keys:
            t = self._parse_time(key)
            if t is not None and start <= t < end:
                parsed.append((key, t))
        if not parsed:
            return []
        parsed.sort(key=lambda kt: kt[1])
        # Pick the nearest key to each step grid point (zssherman approach).
        selected = []
        for grid_t in _timespan(start, end, step):
            best = min(
                parsed, key=lambda kt, gt=grid_t: abs((kt[1] - gt).total_seconds())
            )
            if best not in selected:
                selected.append(best)
        return [k for k, _ in selected]

    @staticmethod
    def _s3_path(key) -> str:
        """Return the canonical ``bucket/key`` path for ``key``.

        ``fs.ls`` yields bucket-qualified paths (e.g.
        ``noaa-nexrad-level2/2024/06/01/KTLX/...``) whereas callers may pass a
        bare key (``2024/06/01/KTLX/...``). Both must normalise to *exactly*
        one bucket prefix: the previous implementation unconditionally
        prepended the bucket, so every key produced by :meth:`list_files`
        became ``bucket/bucket/...`` and the download failed.
        """
        norm = str(key).lstrip("/")
        if norm == _NEXRAD_BUCKET or norm.startswith(_NEXRAD_BUCKET + "/"):
            return norm
        return f"{_NEXRAD_BUCKET}/{norm}"

    def fetch(self, key, dest=None, force_refresh: bool = False) -> str:
        fs = self._fs()
        local = dest or self.cache_path(os.path.basename(key))
        if self._cache_hit(local, force_refresh=force_refresh):
            return local
        # s3fs.get writes in place, so an interrupted transfer used to leave a
        # truncated file that the size>0 cache probe kept serving forever.
        # Stage into a unique sibling temp file and commit atomically instead.
        tmp = _unique_tmp(local)
        try:
            fs.get(self._s3_path(key), tmp)
            if os.path.isdir(tmp):
                # s3fs.get may create a directory named like the key.
                moved = os.path.join(tmp, os.path.basename(key))
                if not os.path.isfile(moved):
                    raise RemoteDataError(f"s3fs produced no file for {key!r}")
                os.replace(moved, local)
            else:
                if os.path.getsize(tmp) == 0:
                    raise RemoteDataError(f"empty download for {key!r}")
                os.replace(tmp, local)
        finally:
            _silent_unlink(tmp)
        return local

    def read(self, key, dest=None, **kwargs):
        from .nexrad_archive import read_nexrad_archive

        local = self.fetch(key, dest=dest)
        scans = kwargs.pop("scans", None)
        reader_kwargs = {"storage_options": {"anon": self._anon}}
        reader_kwargs.update(kwargs)
        if scans is not None:
            reader_kwargs["scans"] = list(scans)
        return read_nexrad_archive(local, **reader_kwargs)


# ---------------------------------------------------------------------------
# Central Meteorological Observatory (nmc.cn) public anonymous source
# ---------------------------------------------------------------------------

_NMC_BASE = "http://www.nmc.cn"
# Best-effort URL templates for the public nmc.cn radar composite imagery.
# nmc.cn serves national / regional radar mosaic PNGs refreshed every 6 min.
# These templates are probed at list_sites time and may need adjustment as the
# upstream site evolves; unreachable regions are silently dropped.
_NMC_REGIONS = {
    "nationwide": ("all", "China-wide composite mosaic"),
    "huabei": ("huabei", "North China regional mosaic"),
    "huadong": ("huadong", "East China regional mosaic"),
    "huanan": ("huanan", "South China regional mosaic"),
    "xinan": ("xinan", "Southwest China regional mosaic"),
}


@register_source
class NmcCnSource(_BaseSource):
    """Public anonymous radar composite source from nmc.cn.

    nmc.cn publishes radar *mosaic imagery* (PNG), not polar volumes, so
    :meth:`read` returns the local cached path (a raster) rather than a
    :class:`pyart.core.Radar`. This makes the source useful for the
    discovery + caching half of the workflow; pair it with a polar source
    (NEXRAD / CINRAD) when a ``Radar`` object is required.

    ``list_sites`` probes each region URL for reachability and drops any that
    are unreachable (assumption #6 of the plan).
    """

    name = "nmc_cn"
    bands = ("C", "X")

    def _region_url(self, region, when):
        code, _ = _NMC_REGIONS[region]
        return f"{_NMC_BASE}/publish/radar/{code}/{when:%Y%m%d%H}.png"

    def list_sites(self) -> dict:
        out = {}
        for region, (code, desc) in _NMC_REGIONS.items():
            url = f"{_NMC_BASE}/publish/radar/{code}.html"
            if self._is_reachable(url):
                out[region] = RadarSite(region, 0.0, 0.0, 0.0, "C", "CN")
        if not out:
            import warnings

            warnings.warn(
                "NmcCnSource: no nmc.cn region reachable; list_sites empty. "
                "The upstream site may have changed; review _NMC_REGIONS.",
                RuntimeWarning,
            )
        return out

    def list_files(self, site, start, end, step) -> list:
        if site not in _NMC_REGIONS:
            return []
        if isinstance(end, str) and end.lower() == "now":
            end = _utcnow()
        keys = []
        for t in _timespan(start, end, step):
            keys.append(self._region_url(site, t))
        return keys

    def fetch(self, key, dest=None, force_refresh: bool = False) -> str:
        local = dest or self.cache_path(key)
        if self._cache_hit(local, force_refresh=force_refresh):
            return local
        data = self._http_get(key)
        self._atomic_write(local, data)
        return local

    def read(self, key, dest=None, **kwargs):
        local = self.fetch(key, dest=dest)
        # nmc.cn serves raster mosaics, not polar Radar objects.
        return local


# ---------------------------------------------------------------------------
# 天擎 MUSIC (CMA data service) — the only public service that exposes real
# CINRAD Level-2 base data (X / S / C-band dual-polarization) by station and
# time range. Requires registration; credentials are read from environment
# variables and never hard-coded.
#
#   CMA_MUSIC_USER_ID   userId for the MUSIC service
#   CMA_MUSIC_API_KEY   apiKey for the MUSIC service
#   CMA_MUSIC_SERVER_ID serverId, default "NMIC_MUSIC_CMADAAS"
#
# The ``cma_music_api`` client package is optional and imported lazily; when
# it (or the credentials) is missing, calls raise :class:`RemoteDataError`
# with an actionable message instead of failing at import time.
# ---------------------------------------------------------------------------

_MUSIC_DEFAULT_SERVER_ID = "NMIC_MUSIC_CMADAAS"
_MUSIC_DATACODE = "RADA_L2_FMT"


@register_source
class CmaMusicSource(_BaseSource):
    """China Meteorological Administration MUSIC (天擎) radar source.

    Pulls real CINRAD Level-2 base-data files (X / S / C-band) via the
    ``cma_music_api`` client (interface ``getRadaFileByTimeRangeAndStaId``)
    and reads them through :func:`pyart.io.read_cinrad`.

    Parameters
    ----------
    station_table : dict or None, optional
        Optional mapping of station code -> :class:`RadarSite`. When given it
        is returned by :meth:`list_sites`; otherwise station discovery is
        attempted via the MUSIC ``getStaInfoByDataCode`` interface, and an
        empty dict with a warning is returned when that is unavailable.
    user_id, api_key, server_id : str or None, optional
        Credential overrides. When None, read from the ``CMA_MUSIC_USER_ID``,
        ``CMA_MUSIC_API_KEY`` and ``CMA_MUSIC_SERVER_ID`` environment
        variables (``server_id`` defaults to ``NMIC_MUSIC_CMADAAS``).

    Notes
    -----
    This source cannot be exercised in CI without credentials; tests mock the
    client. Run ``python scripts/verify_remote_sources.py cma_music`` on an
    authorized machine to validate the real endpoint.
    """

    name = "cma_music"
    bands = ("X", "S", "C")

    def __init__(self, station_table=None, user_id=None, api_key=None, server_id=None):
        self.station_table = dict(station_table or {})
        self.user_id = (
            os.environ.get("CMA_MUSIC_USER_ID") if user_id is None else user_id
        )
        self.api_key = (
            os.environ.get("CMA_MUSIC_API_KEY") if api_key is None else api_key
        )
        self.server_id = server_id or os.environ.get(
            "CMA_MUSIC_SERVER_ID", _MUSIC_DEFAULT_SERVER_ID
        )

    # -- lazy MUSIC client ------------------------------------------------
    def _client(self, timeout=15.0):
        if not self.user_id or not self.api_key:
            raise RemoteDataError(
                "CmaMusicSource requires MUSIC credentials. Set the "
                "CMA_MUSIC_USER_ID and CMA_MUSIC_API_KEY environment "
                "variables (and optionally CMA_MUSIC_SERVER_ID)."
            )
        try:
            from cma_music_api.client import DataQueryClient
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RemoteDataError(
                "cma_music_api is required for CmaMusicSource; install it "
                "with 'pip install cma-music-api'"
            ) from exc
        return DataQueryClient(
            self.user_id, self.api_key, self.server_id, timeout=timeout
        )

    def list_sites(self) -> dict:
        if self.station_table:
            return dict(self.station_table)
        try:
            client = self._client()
            raw = client.getStaInfoByDataCode(_MUSIC_DATACODE)
            out = {}
            for row in raw or []:
                code = row.get("staId") or row.get("Station_Id_d")
                if not code:
                    continue
                try:
                    lat = float(row.get("staLat") or row.get("Lat"))
                    lon = float(row.get("staLon") or row.get("Lon"))
                    alt = float(row.get("staEle") or row.get("Height") or 0.0)
                    # MUSIC stations carry no band info by default; the band
                    # is resolved at read time from the file.
                except (TypeError, ValueError):
                    lat = lon = alt = 0.0
                out[code] = RadarSite(code, lat, lon, alt, "C", "CN")
            if not out:
                import warnings as _w

                _w.warn(
                    "CmaMusicSource: station discovery returned no stations; "
                    "pass station_table=... in the constructor.",
                    RuntimeWarning,
                )
            return out
        except Exception as exc:  # noqa: BLE001 - fail soft without creds
            import warnings as _w

            _w.warn(
                f"CmaMusicSource: station discovery unavailable ({exc}); "
                "pass station_table=... in the constructor.",
                RuntimeWarning,
            )
            return {}

    def _list_keys(self, site, start, end, step):
        if isinstance(end, str) and end.lower() == "now":
            end = _utcnow()
        keys = []
        for t in _timespan(start, end, step):
            keys.append(
                {
                    "dataCode": _MUSIC_DATACODE,
                    "staId": site,
                    "timeRange": f"({t:%Y-%m-%d %H:%M:%S},{t:%Y-%m-%d %H:%M:%S}]",
                }
            )
        return keys

    def list_files(self, site, start, end, step) -> list:
        return self._list_keys(site, start, end, step)

    def fetch(self, key, dest=None, force_refresh: bool = False) -> str:
        local = dest or self.cache_path(
            f"cma_music_{key['dataCode']}_{key['staId']}_{key['timeRange']}"
        )
        if self._cache_hit(local, force_refresh=force_refresh):
            return local
        import warnings as _w

        _w.warn(
            "CmaMusicSource.fetch triggered a real MUSIC download; this "
            "requires valid credentials and network access.",
            UserWarning,
        )
        url = key.get("url")
        if url is None:
            # Documented MUSIC interface: returns file entries for a station
            # and time range. The exact response shape is client-version
            # dependent, so we defensively look up a download URL.
            client = self._client()
            try:
                entries = client.getRadaFileByTimeRangeAndStaId(
                    dataCode=key["dataCode"],
                    staId=key["staId"],
                    timeRange=key["timeRange"],
                )
            except AttributeError as exc:
                raise RemoteDataError(
                    "The installed cma_music_api client does not expose "
                    "getRadaFileByTimeRangeAndStaId; provide the download "
                    "URL directly via key['url']."
                ) from exc
            if not entries:
                raise RemoteDataError(
                    f"No MUSIC file returned for station {key['staId']} at "
                    f"{key['timeRange']}."
                )
            entry = entries[0]
            url = None
            for field in ("url", "downloadUrl", "fileUrl", "ftpUrl"):
                if entry.get(field):
                    url = entry[field]
                    break
            if url is None:
                raise RemoteDataError(
                    "MUSIC file entry has no recognized download URL field: "
                    f"{list(entry)}"
                )
        # 天擎 download URLs may sit on private address space, so allow them
        # through the SSRF guard explicitly.
        data = self._http_get(url, allow_private=True)
        self._atomic_write(local, data)
        return local

    def read(self, key, dest=None, **kwargs):
        local = self.fetch(key, dest=dest)
        from .cinrad_bridge import is_xband_filename, read_cinrad

        if is_xband_filename(local):
            kwargs.setdefault("band", "X")
        else:
            kwargs.setdefault("band", None)
        try:
            return read_cinrad(local, **kwargs)
        except Exception as exc:
            # Not a base-data file the bridge can decode. Returning a path
            # here breaks the documented ``list[Radar]`` contract, so the
            # failure must be observable rather than silently swallowed.
            import warnings as _w

            _w.warn(
                f"CmaMusicSource.read could not decode {local!r} as CINRAD "
                f"base data ({type(exc).__name__}: {exc}); returning the local "
                "path instead of a Radar object.",
                RuntimeWarning,
            )
            return local


# ---------------------------------------------------------------------------
# Per-province CMA public mirrors — honest implementation
# ---------------------------------------------------------------------------


@register_source
class CmaMosSource(_BaseSource):
    """Per-province CMA radar mirrors (public, anonymous).

    Unlike the NEXRAD/天擎 sources, CMA does not publish a stable,
    documented anonymous mirror layout. The URL templates are therefore
    **not shipped by default**; supply them via ``station_config`` (mapping
    station code -> URL template with ``{date}`` / ``{time}`` placeholders,
    plus an optional ``band`` key). ``list_sites`` probes each template for
    reachability and drops unreachable entries.

    Parameters
    ----------
    station_config : dict or None, optional
        Mapping of station code -> dict with a ``template`` key, an optional
        ``band`` key, and an optional ``allow_private`` flag (default False).
        Set ``allow_private=True`` only for a mirror that genuinely lives on
        private address space -- it relaxes the SSRF guard for that one
        station. When None, no sites are configured and :meth:`list_sites`
        returns ``{}`` with a warning.
    """

    name = "cma_mos"
    bands = ("S", "C")

    def __init__(self, station_config=None):
        self.station_config = dict(station_config or {})

    def _fill_url(self, template, when):
        return template.format(date=when.strftime("%Y%m%d"), time=when.strftime("%H%M"))

    def list_sites(self) -> dict:
        if not self.station_config:
            import warnings as _w

            _w.warn(
                "CmaMosSource: no station_config supplied; list_sites empty. "
                "Pass station_config={'code': {'template': 'http://...",
                RuntimeWarning,
            )
            return {}
        out = {}
        for code, cfg in self.station_config.items():
            try:
                template = cfg["template"]
                band = cfg.get("band", "S")
                # Internal mirrors are opt-in *per station*: an operator who
                # really targets private address space must declare
                # ``"allow_private": True``. Every other template keeps the
                # SSRF guard on, so a copy-pasted config cannot silently turn
                # the registry into an internal port scanner.
                allow_private = bool(cfg.get("allow_private", False))
                probe = self._fill_url(template, datetime(2000, 1, 1))
            except (KeyError, TypeError):
                continue
            if self._is_reachable(probe, allow_private=allow_private):
                out[code] = RadarSite(code, 0.0, 0.0, 0.0, band, "CN")
        return out

    def list_files(self, site, start, end, step) -> list:
        cfg = self.station_config.get(site)
        if not cfg:
            return []
        if isinstance(end, str) and end.lower() == "now":
            end = _utcnow()
        template = cfg["template"]
        return [self._fill_url(template, t) for t in _timespan(start, end, step)]

    def fetch(self, key, dest=None, force_refresh: bool = False) -> str:
        local = dest or self.cache_path(key)
        if self._cache_hit(local, force_refresh=force_refresh):
            return local
        data = self._http_get(key)
        self._atomic_write(local, data)
        return local

    def read(self, key, dest=None, **kwargs):
        local = self.fetch(key, dest=dest)
        try:
            from .cinrad_bridge import read_cinrad

            return read_cinrad(local, **kwargs)
        except Exception as exc:
            # See CmaMusicSource.read: the path fallback breaks the documented
            # ``list[Radar]`` contract, so surface it as a warning.
            import warnings as _w

            _w.warn(
                f"CmaMosSource.read could not decode {local!r} as CINRAD base "
                f"data ({type(exc).__name__}: {exc}); returning the local path "
                "instead of a Radar object.",
                RuntimeWarning,
            )
            return local


# ---------------------------------------------------------------------------
# Offline CINE file index
# ---------------------------------------------------------------------------

_CINE_EXTS = (".cine", ".CINE", ".bin", ".BIN")


@register_source
class CineSource(_BaseSource):
    """Offline index over a tree of local CINRAD/CINE files.

    This source never touches the network; it enumerates ``*.cine`` /
    ``*.bin`` files beneath ``root`` (defaulting to the current working
    directory), groups them into pseudo-sites by filename prefix, and filters
    by each file's **modification time** (mtime), not by a timestamp parsed
    out of the name -- CINE archives carry no dependable in-name timestamp, so
    ``list_files`` and :meth:`scan_cache` both key off
    :func:`os.path.getmtime`. ``fetch`` returns the path unchanged and
    ``read`` delegates to :func:`pyart.io.read` (which routes to the CINRAD
    bridge based on filename patterns).
    """

    name = "cine"
    bands = ("C", "S", "X")

    def __init__(self, root=None):
        self.root = root or os.getcwd()

    def _scan(self):
        import glob as _glob

        files = []
        for ext in _CINE_EXTS:
            files.extend(
                _glob.glob(os.path.join(self.root, "**", "*" + ext), recursive=True)
            )
        # Deduplicate: on case-insensitive filesystems '.cine' and '.CINE'
        # match the same files.
        return sorted(set(files))

    @staticmethod
    def _site_of(path):
        return os.path.splitext(os.path.basename(path))[0].split("_")[0]

    def list_sites(self) -> dict:
        out = {}
        for path in self._scan():
            site = self._site_of(path)
            out.setdefault(site, RadarSite(site, 0.0, 0.0, 0.0, "C", "CN"))
        return out

    def list_files(self, site, start, end, step) -> list:
        if isinstance(end, str) and end.lower() == "now":
            end = _utcnow()
        out = []
        for path in self._scan():
            if self._site_of(path) != site:
                continue
            age = os.path.getmtime(path)
            ts = datetime.fromtimestamp(age)
            if start <= ts < end:
                out.append(path)
        return sorted(out)

    def fetch(self, key, dest=None) -> str:
        if not os.path.exists(key):
            raise RemoteDataError(f"local file not found: {key}")
        return key

    def read(self, key, dest=None, **kwargs):
        from .auto_read import read as _read

        local = self.fetch(key, dest=dest)
        return _read(local, **kwargs)

    def scan_cache(self, site=None, start=None, end=None, **kwargs) -> list:
        """Batch-convert cached local files into :class:`Radar` objects.

        Loops over every cached file beneath ``root`` (optionally restrained
        to ``site`` and the ``[start, end)`` mtime window) and reads each with
        :meth:`read`; failed reads are skipped. Unlike ``read_time_span`` this
        does **not** touch the network -- it is the fully offline path for
        turning downloaded CINRAD/CINE files into Radar objects.

        Returns
        -------
        radars : list of Radar
        """
        keys = self._scan()
        if site is not None:
            keys = [k for k in keys if self._site_of(k) == site]
        if start is not None or end is not None:
            start_ts = start.timestamp() if start is not None else None
            end_ts = end.timestamp() if end is not None else None
            out = []
            for k in keys:
                m = os.path.getmtime(k)
                if start_ts is not None and m < start_ts:
                    continue
                if end_ts is not None and m >= end_ts:
                    continue
                out.append(k)
            keys = out
        radars = []
        for key in keys:
            try:
                radars.append(self.read(key, **kwargs))
            except Exception:  # noqa: BLE001
                continue
        return radars
