"""Shared pytest fixtures: provide a stub netCDF4 when it is unavailable.

The pure-Python readers (C98D, S-band) do not need netCDF4 at runtime, but
``import pyart`` pulls in modules that import netCDF4 at module scope. This
conftest installs a minimal stub so offline unit tests can run without the
dependency; real netCDF4 is used when installed (stub only loaded on
ImportError).
"""

import sys
import types
from datetime import datetime as _dt

try:
    import netCDF4  # noqa: F401
except ImportError:
    fake = types.ModuleType('netCDF4')
    fake.num2date = lambda *a, **k: None
    fake.date2num = lambda *a, **k: None
    fake.datetime = _dt
    fake.Dataset = object
    sys.modules['netCDF4'] = fake

    cftime = types.ModuleType('cftime')
    cftime.num2date = fake.num2date
    cftime.date2num = fake.date2num
    cftime.utime = lambda *a, **k: None
    sys.modules['cftime'] = cftime
