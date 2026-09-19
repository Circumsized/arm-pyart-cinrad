"""
Security tests for the RXM-25 NetCDF reader
(FU-21, 434d5a, CWE-789; FU-22, 17bab9, CWE-129).

The ``Gate`` and ``Radial`` dimensions drive every allocation in
:func:`read_rxm25` (via ``make_empty_ppi_radar``) and the 12 moment
variables must be stored with the ``(Radial, Gate)`` shape the reader
assigns straight into ``radar.fields``. Before FU-21 a crafted
``Gate=1e10``/``Radial=1e10`` file requested a multi-exabyte allocation
before any data was inspected, and before FU-22 a moment variable with a
mismatched shape produced a Radar object that the Cython kernels then
indexed out of bounds.

The NetCDF files are synthesized in-memory, so no real data is required.
"""

import hashlib

import netCDF4
import numpy as np
import pytest

from pyart.aux_io.rxm25 import read_rxm25
from pyart.exceptions import PyARTDataError

# 6 rays, 8 gates, single sweep.
NRAYS = 6
NGATES = 8
FILL = -9999.0

RXM25_FIELD_VARIABLES = (
    "Reflectivity",
    "NormalizedCoherentPower",
    "SpectralWidth",
    "Velocity",
    "CorrectedReflectivity",
    "DifferentialReflectivity",
    "DifferentialPhase",
    "SpecificPhase",
    "CorrectedDifferentialReflectivity",
    "SignalToNoiseRatio",
    "RainfallRate",
    "CrossPolCorrelation",
)


def _write_rxm25(
    path,
    gate_size=None,
    radial_size=None,
    velocity_shape=None,
    drop_variables=(),
    time_len=None,
    azimuth_len=None,
):
    """Write a minimal RXM-25 style NetCDF file.

    ``gate_size`` / ``radial_size`` override the declared Gate / Radial
    dimension sizes (FU-21) and ``velocity_shape`` stores Velocity with a
    shape that disagrees with them (FU-22). In those attack modes the
    crafted dimensions are left unused by every variable, so the file
    itself stays tiny: the reader is what turns the declarations into
    allocations.
    """
    attack = (
        gate_size is not None
        or radial_size is not None
        or velocity_shape is not None
    )
    dataset = netCDF4.Dataset(str(path), "w", format="NETCDF4")
    try:
        dataset.createDimension("Gate", gate_size if gate_size is not None else NGATES)
        dataset.createDimension(
            "Radial", radial_size if radial_size is not None else NRAYS
        )
        if attack:
            ray_dim, gate_dim = "Ray", "Gatesmall"
            dataset.createDimension(ray_dim, NRAYS)
            dataset.createDimension(gate_dim, NGATES)
            if velocity_shape is not None:
                dataset.createDimension("VelRay", velocity_shape[0])
                dataset.createDimension("VelGate", velocity_shape[1])
        else:
            ray_dim, gate_dim = "Radial", "Gate"

        time_len = NRAYS if time_len is None else time_len
        azimuth_len = NRAYS if azimuth_len is None else azimuth_len

        time = dataset.createVariable("Time", "f8", (ray_dim,))
        time[:time_len] = 1.6e9 + np.arange(time_len)

        azimuth = dataset.createVariable("Azimuth", "f4", (ray_dim,))
        azimuth[:azimuth_len] = np.linspace(0.0, 360.0, azimuth_len, endpoint=False)

        elevation = dataset.createVariable("Elevation", "f4", (ray_dim,))
        elevation[:] = np.full(NRAYS, 1.5)

        # Longitude / Latitude / Height are read through attribute access,
        # so the format carries them as global attributes.
        dataset.Longitude = -100.0
        dataset.Latitude = 40.0
        dataset.Height = 300.0

        start_range = dataset.createVariable("StartRange", "f8", (ray_dim,))
        start_range[:] = np.full(NRAYS, 500.0)
        gate_width = dataset.createVariable("GateWidth", "f8", (ray_dim,))
        gate_width[:] = np.full(NRAYS, 100.0)

        for i, name in enumerate(RXM25_FIELD_VARIABLES):
            if name in drop_variables:
                continue
            if name == "Velocity" and velocity_shape is not None:
                dims, shape = ("VelRay", "VelGate"), velocity_shape
            else:
                dims, shape = (ray_dim, gate_dim), (NRAYS, NGATES)
            var = dataset.createVariable(name, "f4", dims, fill_value=FILL)
            var[:] = (np.arange(int(np.prod(shape)), dtype="f4") + i).reshape(shape)
    finally:
        dataset.close()


def _read(path, heading=None, **kwargs):
    _write_rxm25(path, **kwargs)
    return read_rxm25(str(path), heading=heading)


class TestWellFormedFileUnchanged:
    def test_fields_and_values(self, tmp_path):
        radar = _read(tmp_path / "wellformed.nc")
        assert radar.ngates == NGATES
        assert radar.nrays == NRAYS
        assert radar.nsweeps == 1
        assert len(radar.fields) == len(RXM25_FIELD_VARIABLES)

        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (NRAYS, NGATES)
        assert list(data[0]) == [float(v) for v in range(NGATES)]
        digest = hashlib.md5(np.asarray(data).tobytes()).hexdigest()[:16]
        assert digest == "4a227dfd1cf0aab2"

    def test_range_and_angles(self, tmp_path):
        radar = _read(tmp_path / "wellformed.nc")
        assert np.allclose(radar.range["data"][0], 0.5)
        assert np.allclose(radar.range["data"][-1], 1.2)
        assert np.allclose(radar.latitude["data"], 40.0)
        assert np.allclose(radar.longitude["data"], -100.0)
        assert radar.fixed_angle["data"][0] == 1.5

    def test_heading_offset(self, tmp_path):
        radar = _read(tmp_path / "wellformed.nc", heading=35.0)
        assert radar.heading == 35.0
        expected = np.mod(np.linspace(0.0, 360.0, NRAYS, endpoint=False) - 35.0, 360.0)
        assert np.allclose(radar.azimuth["data"], expected)


class TestDimensionBounds:
    def test_gate_dimension_over_limit_rejected(self, tmp_path):
        # FU-21 (434d5a, CWE-789): a 1e10 Gate dimension requests a
        # multi-exabyte (Radial, Gate) allocation before any data is read.
        with pytest.raises(PyARTDataError, match="Gate"):
            _read(tmp_path / "gate_bomb.nc", gate_size=10**10)

    def test_radial_dimension_over_limit_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Radial"):
            _read(tmp_path / "radial_bomb.nc", radial_size=10**10)

    def test_missing_dimensions_rejected(self, tmp_path):
        path = tmp_path / "no_dims.nc"
        dataset = netCDF4.Dataset(str(path), "w", format="NETCDF4")
        dataset.createDimension("Other", 4)
        dataset.close()
        with pytest.raises(PyARTDataError, match="Gate"):
            read_rxm25(str(path))


class TestFieldShapeConsistency:
    def test_velocity_radial_mismatch_rejected(self, tmp_path):
        # FU-22 (17bab9, CWE-129): the stored shape disagreed with the
        # Radial dimension; the reader used to assign it straight into
        # radar.fields, leaving a Radar object the kernels mis-index.
        with pytest.raises(PyARTDataError, match="shape"):
            _read(tmp_path / "vel_rays.nc", velocity_shape=(3, 5))

    def test_velocity_gate_mismatch_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="shape"):
            _read(tmp_path / "vel_gates.nc", velocity_shape=(NRAYS, 4))

    def test_missing_field_variable_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Velocity"):
            _read(tmp_path / "no_vel.nc", drop_variables=("Velocity",))

    def test_short_time_variable_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Time"):
            _read(tmp_path / "short_time.nc", time_len=3)

    def test_azimuth_length_mismatch_rejected(self, tmp_path):
        with pytest.raises(PyARTDataError, match="Azimuth"):
            _read(tmp_path / "short_az.nc", azimuth_len=4)
