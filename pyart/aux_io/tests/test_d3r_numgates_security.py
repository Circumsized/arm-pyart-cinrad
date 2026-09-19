"""
Security tests for the GCPEX D3R netCDF reader (FU-24, db0886, CWE-789).

``NumGates`` is a file-controlled global attribute that sizes the range
axis (``np.arange(nbins)``); before FU-24 a crafted ``NumGates=1e10``
requested a multi-exabyte allocation, a gate count disagreeing with the
stored moment variables flowed into ``radar.fields``, and short
``Azimuth``/``Elevation``/``Time`` variables produced an internally
inconsistent Radar object. All must be rejected with ``PyARTDataError``
while well-formed files read unchanged.

The netCDF files are synthesized in-memory, so no real data is required.
"""

import hashlib
import warnings

import netCDF4
import numpy as np
import pytest

from pyart.aux_io.d3r_gcpex_nc import read_d3r_gcpex_nc
from pyart.exceptions import PyARTDataError

# 4 rays, 8 gates, single sweep.
NRAYS = 4
NGATES = 8

BASE_TIME = 1_600_000_000.0
D3R_FIELD_VARIABLES = ("Reflectivity", "Velocity", "SpectralWidth")


def _write_d3r(
    path,
    num_gates=NGATES,
    gate_dim=None,
    drop_variables=(),
    azimuth_len=None,
    elevation_len=None,
    time_len=None,
    drop_num_gates=False,
):
    """Write a minimal GCPEX D3R style netCDF file.

    ``num_gates`` overrides the declared NumGates attribute and ``gate_dim``
    the stored Gate dimension; the ``*_len`` overrides leave a ray-length
    variable partially written. The declared counts that lie stay cheap to
    store: the reader is what turns them into allocations.
    """
    gate_dim = NGATES if gate_dim is None else gate_dim
    dataset = netCDF4.Dataset(str(path), "w", format="NETCDF4")
    try:
        dataset.createDimension("Radial", NRAYS)
        dataset.createDimension("Gate", gate_dim)

        dataset.Latitude = 40.0
        dataset.Longitude = -100.0
        dataset.Altitude = 295.0
        dataset.NetCDFRevision = "1.0"
        dataset.RadarName = "D3R"
        dataset.ScanType = 2
        dataset.Time = BASE_TIME
        if not drop_num_gates:
            dataset.NumGates = num_gates

        azimuth_len = NRAYS if azimuth_len is None else azimuth_len
        elevation_len = NRAYS if elevation_len is None else elevation_len
        time_len = NRAYS if time_len is None else time_len

        if "Azimuth" not in drop_variables:
            azimuth = dataset.createVariable("Azimuth", "f4", ("Radial",))
            azimuth[:azimuth_len] = np.linspace(0.0, 360.0, azimuth_len, endpoint=False)

        if "Elevation" not in drop_variables:
            elevation = dataset.createVariable("Elevation", "f4", ("Radial",))
            elevation[:elevation_len] = np.full(elevation_len, 1.5)

        if "Time" not in drop_variables:
            time = dataset.createVariable("Time", "f8", ("Radial",))
            time[:time_len] = BASE_TIME + np.arange(time_len)

        start_range = dataset.createVariable("StartRange", "f4", ("Radial",))
        start_range[:] = np.full(NRAYS, 0.5)
        gate_width = dataset.createVariable("GateWidth", "f4", ("Radial",))
        gate_width[:] = np.full(NRAYS, 100.0)

        for name in D3R_FIELD_VARIABLES:
            if name in drop_variables:
                continue
            var = dataset.createVariable(
                name, "f4", ("Radial", "Gate"), fill_value=-9999.0
            )
            var[:] = (np.arange(NRAYS * gate_dim, dtype="f4") + 1.0).reshape(
                NRAYS, gate_dim
            )
    finally:
        dataset.close()


def _read(path, **kwargs):
    _write_d3r(path, **kwargs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return read_d3r_gcpex_nc(str(path))


class TestWellFormedFileUnchanged:
    def test_fields_and_range(self, tmp_path):
        radar = _read(tmp_path / "wellformed.nc")
        assert radar.ngates == NGATES
        assert radar.nrays == NRAYS
        assert radar.nsweeps == 1
        assert radar.scan_type == "ppi"
        assert "reflectivity" in radar.fields
        assert "velocity" in radar.fields

        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (NRAYS, NGATES)
        assert list(data[0]) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        digest = hashlib.md5(np.asarray(data).tobytes()).hexdigest()[:16]
        assert digest == "4d0c294da3af9071"

    def test_angles_and_range_axis(self, tmp_path):
        radar = _read(tmp_path / "wellformed.nc")
        assert np.allclose(radar.azimuth["data"], np.linspace(0.0, 360.0, NRAYS, False))
        assert np.allclose(radar.elevation["data"], 1.5)
        assert np.allclose(radar.range["data"][0], 500.0)
        assert np.allclose(radar.range["data"][-1], 500.7)
        assert np.allclose(radar.latitude["data"], 40.0)
        assert radar.fixed_angle["data"][0] == 1.5

    def test_rhi_scan_type(self, tmp_path):
        path = tmp_path / "rhi.nc"
        _write_d3r(path)
        dataset = netCDF4.Dataset(str(path), "a")
        dataset.ScanType = 1
        dataset.close()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            radar = read_d3r_gcpex_nc(str(path))
        assert radar.scan_type == "rhi"
        assert radar.fixed_angle["data"][0] == 0.0


class TestNumGatesBound:
    def test_num_gates_over_limit_rejected(self, tmp_path):
        # FU-24 (db0886, CWE-789): NumGates sized the range axis with an
        # unbounded np.arange allocation.
        with pytest.raises(PyARTDataError, match="NumGates"):
            _read(tmp_path / "gates_bomb.nc", num_gates=10**10)

    def test_num_gates_inconsistent_with_storage_rejected(self, tmp_path):
        # Declares more gates than any stored moment carries; the range axis
        # used to disagree with every field's second dimension.
        with pytest.raises(PyARTDataError, match="gates"):
            _read(tmp_path / "gates_mismatch.nc", num_gates=64)

    def test_missing_num_gates_attribute_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="NumGates"):
            _read(tmp_path / "no_gates.nc", drop_num_gates=True)


class TestShapeConsistency:
    def test_short_azimuth_rejected(self, tmp_path):
        # A short Azimuth redefines the ray count, so the stored moments no
        # longer match the geometry the Radar object advertises.
        with pytest.raises(PyARTDataError, match="Radial"):
            _read(
                tmp_path / "short_az.nc",
                azimuth_len=3,
                elevation_len=3,
                time_len=3,
            )

    def test_short_elevation_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Elevation"):
            _read(tmp_path / "short_el.nc", elevation_len=3)

    def test_short_time_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Time"):
            _read(tmp_path / "short_time.nc", time_len=3)

    def test_missing_ray_variable_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Elevation"):
            _read(tmp_path / "no_el.nc", drop_variables=("Elevation",))
