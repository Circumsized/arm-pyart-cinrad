"""
Security regression tests for the Sigmet reader's dimension validation (FU-14).

Covers the unbounded allocation in ``pyart.io._sigmetfile.SigmetFile``
(d2238d, CWE-789). ``read_data`` and ``_get_sweep`` derived their array
shapes straight from the file headers and allocated immediately: a 12 KB
file declaring ``nsweeps=32767, nrays=65535, nbins=2**31-1`` requested a
multi-exabyte allocation, surfacing as a bare ``MemoryError`` (or an OS OOM
kill) from deep inside the reader.

The fix routes every header-derived dimension through the shared
:py:mod:`pyart.io._validate` limits *before* any allocation, raising
``PyARTDataError`` instead. Well-formed files must decode unchanged.
"""

import subprocess
import sys
import warnings

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io import _sigmetfile  # noqa: E402
from pyart.io._validate import (  # noqa: E402
    MAX_NGATES,
    MAX_NRAYS,
    MAX_NSWEEPS,
    MAX_NVOLUME_ELEMS,
)

DATA_FILE = "pyart/testing/data/example_sigmet_ppi.sigmet"
# Header field offsets within the sample file (see the structure tables in
# _sigmetfile.pyx): product_end.number_bins (SINT4) in record 0,
# ingest_configuration.number_rays_sweep (UINT2) and
# task_scan_info.number_sweeps (SINT2) in record 1, and the first
# ingest_data_header.number_rays_file_expected (SINT2) in record 2.
NUMBER_BINS_OFFSET = 496
NUMBER_RAYS_SWEEP_OFFSET = 6340
NUMBER_SWEEPS_OFFSET = 7574
SWEEP_NRAYS_OFFSET = 2 * 6144 + 12 + 30


def _read_file_bytes():
    with open(DATA_FILE, "rb") as f:
        return f.read()


def _patch(raw, offset, value, dtype):
    buf = bytearray(raw)
    buf[offset : offset + np.dtype(dtype).itemsize] = np.array(
        [value], dtype=dtype
    ).tobytes()
    return bytes(buf)


def _craft(tmp_path, nsweeps=None, nrays=None, nbins=None, sweep_nrays=None):
    raw = _read_file_bytes()
    if nsweeps is not None:
        raw = _patch(raw, NUMBER_SWEEPS_OFFSET, nsweeps, "<i2")
    if nrays is not None:
        raw = _patch(raw, NUMBER_RAYS_SWEEP_OFFSET, nrays, "<u2")
    if nbins is not None:
        raw = _patch(raw, NUMBER_BINS_OFFSET, nbins, "<i4")
    if sweep_nrays is not None:
        raw = _patch(raw, SWEEP_NRAYS_OFFSET, sweep_nrays, "<i2")
    path = tmp_path / "crafted.sigmet"
    path.write_bytes(raw)
    return str(path)


def _read_rejected(path):
    """Read a crafted file, expecting PyARTDataError from the reader."""
    sigmetfile = _sigmetfile.SigmetFile(path)
    try:
        with pytest.raises(PyARTDataError):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sigmetfile.read_data()
    finally:
        sigmetfile.close()


def test_sample_file_dimensions_are_understood():
    raw = _read_file_bytes()
    assert int(np.frombuffer(raw[NUMBER_BINS_OFFSET : NUMBER_BINS_OFFSET + 4], "<i4")[0]) == 25
    assert int(np.frombuffer(raw[NUMBER_RAYS_SWEEP_OFFSET : NUMBER_RAYS_SWEEP_OFFSET + 2], "<u2")[0]) == 20
    assert int(np.frombuffer(raw[NUMBER_SWEEPS_OFFSET : NUMBER_SWEEPS_OFFSET + 2], "<i2")[0]) == 1


def test_unmodified_sample_file_still_reads():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data, metadata = _sigmetfile.SigmetFile(DATA_FILE).read_data()
    assert data["DBZ2"].shape == (1, 20, 25)


def test_huge_volume_dimensions_rejected_before_allocation(tmp_path):
    # The headline d2238d case: every dimension far beyond its limit.
    path = _craft(tmp_path, nsweeps=32767, nrays=65535, nbins=2**31 - 1)
    _read_rejected(path)


def test_huge_dimensions_do_not_allocate(tmp_path):
    """The rejection must happen before any allocation (no RSS spike)."""
    path = _craft(tmp_path, nsweeps=32767, nrays=65535, nbins=2**31 - 1)
    script = (
        "import resource, sys, warnings\n"
        "warnings.simplefilter('ignore')\n"
        "from pyart.exceptions import PyARTDataError\n"
        "from pyart.io import _sigmetfile\n"
        "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "f = _sigmetfile.SigmetFile(sys.argv[1])\n"
        "try:\n"
        "    f.read_data()\n"
        "    print('NOT-REJECTED')\n"
        "    sys.exit(1)\n"
        "except PyARTDataError:\n"
        "    pass\n"
        "after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(before, after)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    # the import banner is printed first; the numbers are on the last line
    last_line = proc.stdout.strip().splitlines()[-1]
    before, after = (int(x) for x in last_line.split())
    # ru_maxrss is in KB on Linux; a successful multi-gigabyte allocation
    # attempt would push this far beyond a few hundred MB.
    assert after - before < 100 * 1024


@pytest.mark.parametrize(
    "nsweeps,nrays,nbins",
    [
        (MAX_NSWEEPS + 1, 20, 25),
        (1, MAX_NRAYS + 1, 25),
        (1, 20, MAX_NGATES + 1),
    ],
)
def test_single_dimension_above_limit_rejected(tmp_path, nsweeps, nrays, nbins):
    path = _craft(tmp_path, nsweeps=nsweeps, nrays=nrays, nbins=nbins)
    _read_rejected(path)


def test_dimension_product_above_limit_rejected(tmp_path):
    # Each axis is individually legal but the product is not.
    assert MAX_NSWEEPS * MAX_NRAYS * MAX_NGATES > MAX_NVOLUME_ELEMS
    path = _craft(
        tmp_path, nsweeps=MAX_NSWEEPS, nrays=MAX_NRAYS, nbins=MAX_NGATES
    )
    _read_rejected(path)


def test_sweep_level_ray_count_above_limit_rejected(tmp_path):
    # The sweep buffer in _get_sweep is sized from the per-data-type
    # ingest headers, which are independent of the volume-level counts.
    path = _craft(tmp_path, sweep_nrays=MAX_NRAYS + 1)
    _read_rejected(path)
