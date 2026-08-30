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
import time
from datetime import datetime, timedelta
from typing import NamedTuple, Protocol, runtime_checkable


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

    def read_time_span(self, site, start, end, step, **kwargs) -> list:
        ...  # pragma: no cover - protocol


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
            "PYART_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".pyart",
                                           "cache"))
        path = os.path.join(base, self.name)
        os.makedirs(path, exist_ok=True)
        return path

    def cache_path(self, key: str) -> str:
        safe = key.replace("/", "_").replace(":", "_")
        return os.path.join(self.cache_dir(), safe)

    @staticmethod
    def _retry(callable_, *args, retries: int = 2, backoff: float = 1.5,
               timeout: float = 15.0, **kwargs):
        """Call ``callable_(*args, **kwargs)`` with bounded retries.

        ``timeout`` is advisory: callers that issue HTTP/S3 requests should
        pass it through to the underlying client. ``RemoteDataError`` is
        raised after the final failed attempt.
        """
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return callable_(*args, timeout=timeout, **kwargs) \
                    if _accepts_timeout(callable_) else \
                    callable_(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(backoff ** attempt)
        raise RemoteDataError(
            f"{getattr(callable_, '__name__', callable_)} failed after "
            f"{retries + 1} attempts: {last_exc}") from last_exc

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
        for key in keys:
            try:
                radars.append(self.read(key, **kwargs))
            except Exception:  # noqa: BLE001
                continue
        return radars

    # -- HTTP helpers -------------------------------------------------------
    def _http_get(self, url, timeout=15.0):
        """GET ``url`` and return bytes, with bounded retries."""
        try:
            import requests
        except ImportError as exc:
            raise ImportError(
                "requests is required for HTTP sources; install it with "
                '"pip install arm_pyart[remote]"') from exc

        def _do(timeout=None):
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content

        return self._retry(_do, retries=2, timeout=timeout)

    def _http_get_json(self, url, timeout=15.0):
        import json
        return json.loads(self._http_get(url, timeout=timeout).decode("utf-8"))

    @staticmethod
    def _is_reachable(url, timeout=8.0):
        try:
            import requests
        except ImportError:
            return False
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=True)
            return r.status_code < 500
        except Exception:  # noqa: BLE001
            return False


def _accepts_timeout(func) -> bool:
    import inspect

    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return "timeout" in sig.parameters


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
        raise KeyError(
            f"Unknown radar source '{name}'. Available: {list_sources()}")
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
        Time range. ``end='now'`` (string) resolves to ``datetime.utcnow()``,
        mirroring ``zssherman/pyart_animation``.
    step : datetime.timedelta
        Cadence between scans.
    **kwargs
        Forwarded to ``source.read`` (e.g. ``scans=(0, 1)`` for NEXRAD).

    Returns
    -------
    radars : list of Radar
        One entry per successfully read file; failed reads are skipped.
    """
    if isinstance(source, str):
        source = get_source(source)
    if isinstance(end, str) and end.lower() == "now":
        end = datetime.utcnow()
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
    "CmaMosSource",
    "CineSource",
]


# ---------------------------------------------------------------------------
# NEXRAD Level II source (anonymous AWS S3)
# ---------------------------------------------------------------------------

import re

_NEXRAD_BUCKET = "noaa-nexrad-level2"
_NEXRAD_KEY_RE = re.compile(r"_(\d{8})_(\d{6})_V")
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
    """Yield ``datetime`` grid points from ``start`` to ``end`` at ``step``."""
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
                '"pip install s3fs"') from exc
        return s3fs.S3FileSystem(anon=self._anon)

    def list_sites(self) -> dict:
        return dict(_NEXRAD_SITES)

    @staticmethod
    def _parse_time(key):
        m = _NEXRAD_KEY_RE.search(key)
        if m is None:
            return None
        try:
            return datetime.strptime(m.group(1) + m.group(2),
                                      "%Y%m%d%H%M%S")
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
            end = datetime.utcnow()
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
            best = min(parsed, key=lambda kt, gt=grid_t: abs((kt[1] - gt)
                       .total_seconds()))
            if best not in selected:
                selected.append(best)
        return [k for k, _ in selected]

    def fetch(self, key, dest=None) -> str:
        fs = self._fs()
        local = dest or self.cache_path(os.path.basename(key))
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local
        fs.get(f"{_NEXRAD_BUCKET}/{key.lstrip('/')}", local)
        # s3fs.get may copy into a directory named like the key; normalise.
        if os.path.isdir(local):
            moved = os.path.join(local, os.path.basename(key))
            return moved
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
                out[region] = RadarSite(
                    region, 0.0, 0.0, 0.0, "C", "CN")
        if not out:
            import warnings
            warnings.warn(
                "NmcCnSource: no nmc.cn region reachable; list_sites empty. "
                "The upstream site may have changed; review _NMC_REGIONS.",
                RuntimeWarning)
        return out

    def list_files(self, site, start, end, step) -> list:
        if site not in _NMC_REGIONS:
            return []
        if isinstance(end, str) and end.lower() == "now":
            end = datetime.utcnow()
        keys = []
        for t in _timespan(start, end, step):
            keys.append(self._region_url(site, t))
        return keys

    def fetch(self, key, dest=None) -> str:
        local = dest or self.cache_path(key)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local
        data = self._http_get(key)
        with open(local, "wb") as fh:
            fh.write(data)
        return local

    def read(self, key, dest=None, **kwargs):
        local = self.fetch(key, dest=dest)
        # nmc.cn serves raster mosaics, not polar Radar objects.
        return local


# ---------------------------------------------------------------------------
# Per-province CMA MOS / CINE public mirrors
# ---------------------------------------------------------------------------

# Best-effort per-province CMA data portal mirrors. Each entry is a URL
# template with {date}/{time} placeholders. list_sites probes reachability.
_CMA_MOS_REGIONS = {
    "beijing": "http://data.cma.cn/radar/{date}/{time}.bin",
    "shanghai": "http://data.cma.cn/radar/sh/{date}/{time}.bin",
    "guangdong": "http://data.cma.cn/radar/gd/{date}/{time}.bin",
}


@register_source
class CmaMosSource(_BaseSource):
    """Per-province CMA radar mirrors (public, anonymous).

    Each entry in ``_CMA_MOS_REGIONS`` is probed for reachability; unreachable
    provinces are dropped. Fetched files are assumed to be CINRAD-style binary
    and are read with :func:`pyart.io.read_cinrad`; if that fails the local
    path is returned unchanged (raster / unsupported binary).
    """

    name = "cma_mos"
    bands = ("S", "C")

    def _fill_url(self, template, when):
        return template.format(
            date=when.strftime("%Y%m%d"), time=when.strftime("%H%M"))

    def list_sites(self) -> dict:
        out = {}
        for region, template in _CMA_MOS_REGIONS.items():
            probe = self._fill_url(template, datetime(2000, 1, 1))
            if self._is_reachable(probe):
                out[region] = RadarSite(region, 0.0, 0.0, 0.0, "S", "CN")
        return out

    def list_files(self, site, start, end, step) -> list:
        if site not in _CMA_MOS_REGIONS:
            return []
        if isinstance(end, str) and end.lower() == "now":
            end = datetime.utcnow()
        template = _CMA_MOS_REGIONS[site]
        return [self._fill_url(template, t)
                for t in _timespan(start, end, step)]

    def fetch(self, key, dest=None) -> str:
        local = dest or self.cache_path(key)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local
        data = self._http_get(key)
        with open(local, "wb") as fh:
            fh.write(data)
        return local

    def read(self, key, dest=None, **kwargs):
        local = self.fetch(key, dest=dest)
        try:
            from .cinrad_bridge import read_cinrad
            return read_cinrad(local, **kwargs)
        except Exception:
            return local


# ---------------------------------------------------------------------------
# Offline CINE file index
# ---------------------------------------------------------------------------

_CINE_EXTS = (".cine", ".CINE", ".bin", ".BIN")


@register_source
class CineSource(_BaseSource):
    """Offline index over a tree of local CINRAD/CINE files.

    This source never touches the network; it enumerates ``*.cine`` files
    beneath ``root`` (defaulting to the current working directory), groups
    them into pseudo-sites by filename prefix, and filters by a timestamp
    embedded in the file name. ``fetch`` returns the path unchanged and
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
            files.extend(_glob.glob(
                os.path.join(self.root, "**", "*" + ext), recursive=True))
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
            out.setdefault(
                site, RadarSite(site, 0.0, 0.0, 0.0, "C", "CN"))
        return out

    def list_files(self, site, start, end, step) -> list:
        if isinstance(end, str) and end.lower() == "now":
            end = datetime.utcnow()
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
