"""
Security tests for the CF/Radial variable-gate reader
(FU-20, 7a67cb, CWE-789 / CWE-400).

``ray_n_gates`` and ``ray_start_index`` come straight from the netCDF file
and index into the flattened ``n_points`` field data. Before FU-20 the
bounds were never checked: a negative start index read data from the *end*
of the flat array (silently wrong values, no error), an out-of-range
index/gate count aborted with a raw broadcast ``ValueError``, and a
gate-count list longer than the time dimension was silently truncated by
``zip``.

The netCDF files are synthesized in-memory, so no real data is required.
"""

import hashlib

import netCDF4
import numpy as np
import pytest

from pyart.exceptions import PyARTDataError
from pyart.io.cfradial import _unpack_variable_gate_field_dic, read_cfradial

# 2 rays, 4 gates per ray dimension, 6 flattened points total:
# ray 0 reads [0:4], ray 1 reads [4:6].
NRAYS = 2
NGATES = 4
N_POINTS = 6
GOOD_GATES = [4, 2]
GOOD_STARTS = [0, 4]
FLAT_VALUES = np.array([10.0, 11.0, 12.0, 13.0, 20.0, 21.0], dtype="float32")


def _write_variable_gate_file(
    path, ray_n_gates, ray_start_index, n_points=N_POINTS, counts_dim="time"
):
    """Write a minimal variable-gate CF/Radial file.

    ``counts_dim`` selects the dimension carrying ``ray_n_gates`` /
    ``ray_start_index``; a non-time dimension is how a crafted file declares
    more entries than the file has rays.
    """
    dataset = netCDF4.Dataset(path, "w", format="NETCDF4")
    try:
        dataset.createDimension("time", NRAYS)
        dataset.createDimension("range", NGATES)
        dataset.createDimension("sweep", 1)
        dataset.createDimension("n_points", n_points)
        dataset.createDimension("string_length", 32)
        if counts_dim != "time":
            dataset.createDimension(counts_dim, len(ray_n_gates))

        time = dataset.createVariable("time", "f8", ("time",))
        time[:] = np.arange(NRAYS, dtype="f8")
        time.units = "seconds since 2020-01-01 00:00:00"

        gate_range = dataset.createVariable("range", "f4", ("range",))
        gate_range[:] = np.arange(NGATES, dtype="f4") * 250.0

        latitude = dataset.createVariable("latitude", "f8", ())
        latitude[...] = 32.0
        longitude = dataset.createVariable("longitude", "f8", ())
        longitude[...] = 118.0
        altitude = dataset.createVariable("altitude", "f8", ())
        altitude[...] = 45.0

        azimuth = dataset.createVariable("azimuth", "f4", ("time",))
        azimuth[:] = np.array([0.0, 1.0], dtype="f4")
        elevation = dataset.createVariable("elevation", "f4", ("time",))
        elevation[:] = np.array([0.5, 0.5], dtype="f4")

        sweep_mode = dataset.createVariable(
            "sweep_mode", "S1", ("sweep", "string_length")
        )
        mode_name = b"azimuth_surveillance"
        sweep_mode[0] = np.frombuffer(mode_name.ljust(32, b"\x00"), dtype="S1")

        fixed_angle = dataset.createVariable("fixed_angle", "f4", ("sweep",))
        fixed_angle[:] = np.array([0.5], dtype="f4")
        sweep_start = dataset.createVariable("sweep_start_ray_index", "i4", ("sweep",))
        sweep_start[:] = np.array([0], dtype="i4")
        sweep_end = dataset.createVariable("sweep_end_ray_index", "i4", ("sweep",))
        sweep_end[:] = np.array([NRAYS - 1], dtype="i4")
        sweep_number = dataset.createVariable("sweep_number", "i4", ("sweep",))
        sweep_number[:] = np.array([0], dtype="i4")

        ray_n_gates_var = dataset.createVariable("ray_n_gates", "i4", (counts_dim,))
        ray_n_gates_var[:] = np.array(ray_n_gates, dtype="i4")
        ray_start_var = dataset.createVariable("ray_start_index", "i4", (counts_dim,))
        ray_start_var[:] = np.array(ray_start_index, dtype="i4")

        field = dataset.createVariable(
            "reflectivity_horizontal", "f4", ("n_points",), fill_value=-9999.0
        )
        field[:] = np.concatenate([FLAT_VALUES, np.full(n_points - N_POINTS, -9999.0)])
        field.long_name = "reflectivity_horizontal"
        field.units = "dBZ"
    finally:
        dataset.close()


def _read_file(
    tmp_path, ray_n_gates, ray_start_index, n_points=N_POINTS, counts_dim="time"
):
    path = str(tmp_path / "variable_gates.nc")
    _write_variable_gate_file(path, ray_n_gates, ray_start_index, n_points, counts_dim)
    return read_cfradial(path)


class TestValidVariableGateFile:
    def test_unpack_places_gates_per_ray(self):
        dic = {"data": FLAT_VALUES}
        _unpack_variable_gate_field_dic(
            dic, (NRAYS, NGATES), np.array(GOOD_GATES), np.array(GOOD_STARTS)
        )
        data = dic["data"]
        assert data.shape == (NRAYS, NGATES)
        assert list(data[0]) == [10.0, 11.0, 12.0, 13.0]
        assert list(data[1, :2]) == [20.0, 21.0]
        assert data.mask[1, 2:].all()

    def test_end_to_end_read_unchanged(self, tmp_path):
        radar = _read_file(tmp_path, GOOD_GATES, GOOD_STARTS)
        data = radar.fields["reflectivity_horizontal"]["data"]
        assert data.shape == (NRAYS, NGATES)
        assert list(data[0]) == [10.0, 11.0, 12.0, 13.0]
        assert list(data[1, :2]) == [20.0, 21.0]


class TestGateCountsAndIndices:
    def test_gates_exceeding_range_dim_rejected(self, tmp_path):
        # FU-20 (7a67cb, CWE-789): used to abort with a raw broadcast
        # ValueError when assigning a longer slice into the shorter row.
        with pytest.raises(PyARTDataError, match="gates"):
            _read_file(tmp_path, [6, 2], GOOD_STARTS)

    def test_start_index_past_flat_field_rejected(self, tmp_path):
        # FU-20 (7a67cb, CWE-789): idx + gates ran past n_points.
        with pytest.raises(PyARTDataError, match="flat|flattened|points"):
            _read_file(tmp_path, [4, 4], [0, 5])

    def test_negative_start_index_rejected(self, tmp_path):
        # FU-20 (7a67cb, CWE-125): a negative start index used to slice from
        # the *end* of the flat array, silently returning wrong data.
        with pytest.raises(PyARTDataError):
            _read_file(tmp_path, [4, 2], [-6, 4])

    def test_negative_gate_count_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError):
            _read_file(tmp_path, [-4, 2], GOOD_STARTS)

    def test_zero_gate_ray_yields_masked_row(self, tmp_path):
        # A ray carrying no gates is degenerate but legal: its row stays
        # fully masked instead of raising.
        radar = _read_file(tmp_path, [0, 2], GOOD_STARTS)
        data = radar.fields["reflectivity_horizontal"]["data"]
        assert data.mask[0].all()
        assert list(data[1, :2]) == [20.0, 21.0]

    def test_more_entries_than_rays_rejected(self, tmp_path):
        # FU-20 (7a67cb, CWE-400): zip() silently dropped the third entry.
        with pytest.raises(PyARTDataError, match="ray"):
            _read_file(tmp_path, [4, 2, 4], [0, 4, 6], counts_dim="n_rays")

    def test_gates_sum_exceeding_flat_field_rejected(self, tmp_path):
        # Each ray fits, but together they read past the flattened points.
        with pytest.raises(PyARTDataError):
            _read_file(tmp_path, [4, 4], [2, 4], n_points=6)


class TestRealSamplesUnchanged:
    """The bounds must not disturb files without ray_n_gates."""

    def test_example_cfradial_ppi_unchanged(self):
        radar = read_cfradial("pyart/testing/data/example_cfradial_ppi.nc")
        data = radar.fields["reflectivity_horizontal"]["data"]
        assert data.shape == (40, 42)
        digest = hashlib.md5(np.asarray(data).tobytes()).hexdigest()[:16]
        assert digest == "16db90b0bc6e8994"
