#!/usr/bin/env python
"""Verify the availability and configuration of pyart.io.remote sources.

This script is intentionally *offline-friendly*: it never makes a blocking
network call and never fails the process. For each registered source it prints
an actionable checklist (required environment variables, optional client
packages, endpoints, and how to inject configuration), then runs lightweight
offline smoke assertions.

Run ``python scripts/verify_remote_sources.py`` to check every source, or pass
a source name (e.g. ``cma_music``) to focus on one source.

Real-endpoint validation (network + credentials) is out of scope here; run this
script on an authorized machine with credentials set to confirm the wiring.
"""

import os
import sys

# Make sure we import the local Py-ART source tree (this repo), not an
# installed copy. Running a script puts ``scripts/`` on sys.path[0]; the
# repo root (parent of scripts/) holds the ``pyart`` package.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pyart  # noqa: E402


def _check_nexrad():
    print("== nexrad ==")
    print("  Requires: s3fs (optional), anonymous AWS access")
    print("  Endpoint: s3://noaa-nexrad-level2/<Y>/<m>/<d>/<SITE>/")
    print("  Usage:    pyart.io.get_source('nexrad').list_files('KTLX', ...)")
    try:
        import s3fs  # noqa: F401
        print("  s3fs: installed")
    except ImportError:
        print("  s3fs: NOT installed (pip install s3fs or arm_pyart[remote])")


def _check_nmc_cn():
    print("== nmc_cn ==")
    print("  Endpoint: http://www.nmc.cn/publish/radar/... (public)")
    print("  Note: serves RASTER composite PNGs, not polar Radar objects;")
    print("        read() returns a local path, not a pyart Radar.")
    try:
        import requests  # noqa: F401
        print("  requests: installed")
    except ImportError:
        print("  requests: NOT installed (pip install arm_pyart[remote])")


def _check_cma_music():
    print("== cma_music ==")
    print("  Requires: cma_music_api package + credentials")
    env = {
        "CMA_MUSIC_USER_ID": os.environ.get("CMA_MUSIC_USER_ID"),
        "CMA_MUSIC_API_KEY": os.environ.get("CMA_MUSIC_API_KEY"),
        "CMA_MUSIC_SERVER_ID": os.environ.get(
            "CMA_MUSIC_SERVER_ID", "(default NMIC_MUSIC_CMADAAS)"),
    }
    for k, v in env.items():
        print(f"  {k}: {'SET' if v else 'MISSING'}")
    try:
        import cma_music_api  # noqa: F401
        print("  cma_music_api: installed")
    except ImportError:
        print("  cma_music_api: NOT installed (pip install cma-music-api)")
    print("  To validate the real endpoint, set the env vars above and run:")
    print("    python scripts/verify_remote_sources.py cma_music")


def _check_cma_mos():
    print("== cma_mos ==")
    print("  No default endpoint shipped (CMA has no documented anonymous")
    print("  mirror layout). Inject station_config, e.g.:")
    print("    CmaMosSource(station_config={'code': {'template': 'http://...'}})")
    print("  list_sites probes reachability and drops unreachable entries.")


def _check_cine():
    print("== cine ==")
    print("  Fully offline: indexes *.cine files under root (default cwd).")
    print("  Usage: CineSource(root='/path/to/radar/files').scan_cache()")


def _main():
    targets = sys.argv[1:] or [
        "nexrad", "nmc_cn", "cma_music", "cma_mos", "cine",
    ]
    print("Registered sources:", pyart.io.list_sources())
    for name in targets:
        if name == "nexrad":
            _check_nexrad()
        elif name == "nmc_cn":
            _check_nmc_cn()
        elif name == "cma_music":
            _check_cma_music()
        elif name == "cma_mos":
            _check_cma_mos()
        elif name == "cine":
            _check_cine()
        else:
            print(f"== {name} ==")
            print("  (no dedicated checklist; see pyart.io.remote)")
    # Offline smoke: get_source for each registered source succeeds.
    for name in pyart.io.list_sources():
        try:
            pyart.io.get_source(name)
            print(f"  get_source('{name}'): OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  get_source('{name}'): {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
