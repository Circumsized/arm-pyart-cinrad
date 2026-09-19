"""
Security tests for the GAMIC HDF5 reader (FU-23, 57a9dd, CWE-789).

The ``what/sets`` attribute drives the scan-name list, every how/what
attribute loop and the ``(total_rays, max_num_gates)`` masked volume
allocation in :class:`GAMICFile`. Before FU-23 a crafted ``sets=1e9`` file
built a billion-entry scan list and then looped over groups the file does
not carry, and crafted ``ray_count`` / ``bin_count`` attributes sized an
unbounded allocation. All of these must be rejected with
``PyARTDataError`` while well-formed files read unchanged.

The HDF5 files are synthesized in-memory, so no real data is required.
"""

import hashlib

import h5py
import numpy as np
import pytest

from pyart.aux_io.gamicfile import GAMICFile
from pyart.exceptions import PyARTDataError

# 2 scans, 4 rays each, 8 gates.
NSCANS = 2
NRAYS = 4
NBINS = 8

RAY_HEADER_DTYPE = np.dtype(
    [
        ("azimuth_start", "f4"),
        ("azimuth_stop", "f4"),
        ("elevation_start", "f4"),
        ("elevation_stop", "f4"),
        ("timestamp", "i8"),
    ]
)


def _write_gamic(
    path,
    sets=NSCANS,
    scan_groups=None,
    ray_count=NRAYS,
    bin_count=NBINS,
    drop_sets_attribute=False,
):
    """Write a minimal GAMIC style HDF5 file.

    ``sets`` overrides the declared set count, ``scan_groups`` the number of
    scan groups actually present (default: the well-formed count), and
    ``ray_count`` / ``bin_count`` the per-scan counts. The groups stored on
    disk stay small regardless of the declared ``sets``: the crafted count
    is what the reader turns into allocations and loops.
    """
    scan_groups = NSCANS if scan_groups is None else scan_groups
    with h5py.File(str(path), "w") as hfile:
        what = hfile.create_group("what")
        if not drop_sets_attribute:
            what.attrs["sets"] = sets

        where = hfile.create_group("where")
        where.attrs["lat"] = 40.0
        where.attrs["lon"] = -100.0
        where.attrs["height"] = 300.0

        hfile.create_group("how")

        for scan in range(scan_groups):
            group = hfile.create_group(f"scan{scan}")

            scan_what = group.create_group("what")
            scan_what.attrs["scan_type"] = b"ppi"
            scan_what.attrs["set_idx"] = scan

            scan_how = group.create_group("how")
            scan_how.attrs["ray_count"] = ray_count
            scan_how.attrs["bin_count"] = bin_count
            scan_how.attrs["range_samples"] = 1
            scan_how.attrs["range_step"] = 100.0
            scan_how.attrs["range_start"] = 500.0
            scan_how.attrs["elevation"] = 1.0
            scan_how.attrs["azimuth"] = 0.0

            # The stored ray_header stays small; the declared ray_count is
            # what the reader turns into an allocation.
            rays = min(ray_count, NRAYS)
            ray_header = np.zeros(rays, dtype=RAY_HEADER_DTYPE)
            ray_header["azimuth_start"] = np.arange(rays, dtype="f4") * 90.0
            ray_header["azimuth_stop"] = np.arange(rays, dtype="f4") * 90.0 + 89.0
            ray_header["elevation_start"] = 1.0
            ray_header["elevation_stop"] = 1.0
            ray_header["timestamp"] = 1_600_000_000_000_000
            group.create_dataset("ray_header", data=ray_header)

            # moment_0 is the dataset itself; its scaling attributes sit on
            # the dataset, matching _get_gamic_sweep_data's expectations.
            moment = group.create_dataset(
                "moment_0",
                data=(
                    np.arange(NRAYS * NBINS, dtype="uint16").reshape(NRAYS, NBINS) + 1
                ),
            )
            moment.attrs["moment"] = b"dBZ"
            moment.attrs["dyn_range_min"] = 0.0
            moment.attrs["dyn_range_max"] = 100.0
            moment.attrs["format"] = b"UV16"


def _read(path, **kwargs):
    _write_gamic(path, **kwargs)
    return GAMICFile(str(path))


class TestWellFormedFileUnchanged:
    def test_scan_geometry(self, tmp_path):
        gfile = _read(tmp_path / "wellformed.h5")
        try:
            assert gfile.nsweeps == NSCANS
            assert list(gfile.rays_per_sweep) == [NRAYS, NRAYS]
            assert gfile.total_rays == 2 * NRAYS
            assert gfile.max_num_gates == NBINS
            assert list(gfile.start_ray) == [0, NRAYS]
            assert list(gfile.end_ray) == [NRAYS - 1, 2 * NRAYS - 1]
            assert gfile.is_file_complete()
            assert gfile.is_file_single_scan_type()
        finally:
            gfile.close()

    def test_moment_data_unchanged(self, tmp_path):
        gfile = _read(tmp_path / "wellformed.h5")
        try:
            data = gfile.moment_data("moment_0", "float32")
            assert data.shape == (2 * NRAYS, NBINS)
            # UV16 -> (value * (100 - 0) / 65535.0), value 1 in the first gate
            assert np.allclose(data[0, 0], 100.0 / 65535.0)
            digest = hashlib.md5(np.ma.getdata(data).tobytes()).hexdigest()[:16]
            assert digest == "73b6c7fce7bc0c91"
        finally:
            gfile.close()


class TestSetsAttribute:
    def test_sets_over_limit_rejected(self, tmp_path):
        # FU-23 (57a9dd, CWE-789): sets=1e9 built a billion-entry scan list.
        with pytest.raises(PyARTDataError, match="sets"):
            _read(tmp_path / "sets_bomb.h5", sets=10**9)

    def test_sets_zero_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="sets"):
            _read(tmp_path / "sets_zero.h5", sets=0)

    def test_sets_negative_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="sets"):
            _read(tmp_path / "sets_negative.h5", sets=-3)

    def test_sets_not_an_integer_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="sets"):
            _read(tmp_path / "sets_float.h5", sets=2.5)

    def test_sets_exceeding_scan_groups_rejected(self, tmp_path):
        # Declares more sweeps than the file carries.
        with pytest.raises(PyARTDataError, match="scan"):
            _read(tmp_path / "sets_extra.h5", sets=5, scan_groups=2)

    def test_missing_scan_group_rejected(self, tmp_path):
        # The declared count matches the remaining groups only if a gap is
        # allowed; the reader must require scan0..scan[n-1] to exist.
        with pytest.raises(PyARTDataError, match="scan1"):
            _read(tmp_path / "sets_gap.h5", sets=2, scan_groups=1)

    def test_missing_sets_attribute_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="sets"):
            _read(tmp_path / "no_sets.h5", drop_sets_attribute=True)


class TestRayAndGateCounts:
    def test_ray_count_over_limit_rejected(self, tmp_path):
        # FU-23 (57a9dd, CWE-789): ray_count sized the masked volume. The
        # value stays within int32 so the crash is the allocation request,
        # not an int32 overflow in the attribute cast.
        with pytest.raises(PyARTDataError, match="GAMIC volume"):
            _read(tmp_path / "rays_bomb.h5", ray_count=10**9)

    def test_bin_count_over_limit_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="GAMIC volume"):
            _read(tmp_path / "bins_bomb.h5", bin_count=10**9)
