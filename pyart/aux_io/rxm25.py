"""
Routines for Ridgeline Instruments RXM-25 formatted NetCDF files.
"""

import datetime

import netCDF4
import numpy as np

import pyart

from ..config import get_metadata
from ..exceptions import PyARTDataError
from ..io._validate import validate_volume_dims
from ..testing import make_empty_ppi_radar

# (Py-ART field name, RXM-25 variable name, metadata configuration key).
RXM25_MOMENT_VARIABLES = (
    ("reflectivity", "Reflectivity", "reflectivity"),
    (
        "normalized_coherent_power",
        "NormalizedCoherentPower",
        "normalized_coherent_power",
    ),
    ("spectral_width", "SpectralWidth", "spectral_width"),
    ("velocity", "Velocity", "velocity"),
    ("corrected_reflectivity", "CorrectedReflectivity", "correct_reflectivity"),
    (
        "differential_reflectivity",
        "DifferentialReflectivity",
        "differential_reflectivity",
    ),
    ("differential_phase", "DifferentialPhase", "differential_phase"),
    (
        "specific_differential_phase",
        "SpecificPhase",
        "specific_differential_phase",
    ),
    (
        "corrected_differential_reflectivity",
        "CorrectedDifferentialReflectivity",
        "corrected_differential_reflectivity",
    ),
    ("signal_to_noise_ratio", "SignalToNoiseRatio", "signal_to_noise_ratio"),
    ("rain_rate", "RainfallRate", "rain_rate"),
    ("cross_correlation_ratio", "CrossPolCorrelation", "cross_correlation_ratio"),
)

# Ray-length variables: they must cover exactly one sweep of rays.
RXM25_RAY_VARIABLES = ("Time", "Azimuth", "Elevation")


def read_rxm25(filename, cfradial_outfile=None, heading=None):
    """
    Read in Ridgeline Instruments RXM-25 formatted NetCDF data.

    Parameters
    ----------
    filename : str
        Name of Ridgeline Instruments (RLI) RXM-25 formatted NetCDF file
        from which to read data.
    cfradial_outfile : str, optional
        If file is to be converted to CF-Radial format, specify the output
        filename here.
    heading : float, optional
        If a heading offset exists, enter it here (in degrees).

    Returns
    -------
    radar : Radar
        Radar object.

    """
    data = netCDF4.Dataset(filename, "r")

    try:
        # FU-21 (434d5a, CWE-789): the Gate/Radial dimension declarations
        # drive every allocation below (make_empty_ppi_radar, the range
        # axis, each moment array). A crafted Gate=1e10 / Radial=1e10 file
        # used to request a multi-exabyte allocation before any data was
        # inspected; the declarations now go through the shared validation
        # layer, which caps each dimension and the (Radial, Gate) product.
        try:
            ngates = data.dimensions["Gate"].size
            rays_per_sweep = data.dimensions["Radial"].size
        except KeyError as err:
            raise PyARTDataError(
                f"RXM-25 file is missing the {err.args[0]} dimension"
            ) from err
        validate_volume_dims(
            1, rays_per_sweep, ngates, name="RXM-25 Gate/Radial dimensions"
        )

        # FU-22 (17bab9, CWE-129): moment variables are assigned straight
        # into radar.fields and the Cython kernels index them as
        # (ray, gate), so the stored shape must match the geometry the
        # Radar object advertises. A crafted velocity stored with a
        # different shape used to yield an internally inconsistent Radar
        # object instead of an error.
        expected_shape = (rays_per_sweep, ngates)
        moment_values = {}
        for field_name, variable, _metadata_key in RXM25_MOMENT_VARIABLES:
            if variable not in data.variables:
                raise PyARTDataError(f"RXM-25 file is missing the {variable} variable")
            values = data[variable][:]
            if values.shape != expected_shape:
                raise PyARTDataError(
                    f"RXM-25 variable {variable} has shape {values.shape} but "
                    f"the Gate/Radial dimensions declare {expected_shape}"
                )
            moment_values[field_name] = values

        # The ray-length variables must cover exactly one sweep; a shorter
        # Time/Azimuth/Elevation used to corrupt the Radar object (or crash
        # in datetime.fromtimestamp on the masked tail).
        ray_values = {}
        for variable in RXM25_RAY_VARIABLES:
            if variable not in data.variables:
                raise PyARTDataError(f"RXM-25 file is missing the {variable} variable")
            values = data[variable][:]
            # A partially written variable keeps its declared length but
            # masks the unwritten tail, so count the real entries.
            n_entries = int(np.ma.count(values))
            if n_entries != rays_per_sweep:
                raise PyARTDataError(
                    f"RXM-25 variable {variable} has {n_entries} entries but "
                    f"the Radial dimension declares {rays_per_sweep}"
                )
            ray_values[variable] = values

        radar = make_empty_ppi_radar(ngates, rays_per_sweep, 1)

        # Time needs to be converted from nss1970 to nss1989 and added to
        # Radar object.
        nineteen89 = datetime.datetime(
            1989, 1, 1, 0, 0, 1, tzinfo=datetime.timezone.utc
        )
        baseTime = np.array(
            [
                datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
                for t in ray_values["Time"]
            ]
        )
        radar.time["data"] = np.array(
            [t.total_seconds() for t in baseTime - nineteen89]
        )

        if heading is not None:
            radar.heading = heading
            radar.azimuth["data"] = np.mod(ray_values["Azimuth"] - radar.heading, 360.0)
        else:
            radar.azimuth["data"] = ray_values["Azimuth"]

        radar.longitude["data"] = np.array([data.Longitude], dtype="float64")
        radar.latitude["data"] = np.array([data.Latitude], dtype="float64")
        radar.elevation["data"] = ray_values["Elevation"]
        radar.altitude["data"] = np.array([data.Height], dtype="float64")

        fixed_agl_data = np.empty((1,), dtype="float32")
        fixed_agl_data[:] = np.mean(radar.elevation["data"][:rays_per_sweep])

        radar.fixed_angle["data"] = fixed_agl_data

        radar.range["data"] = np.linspace(
            data["StartRange"][0] / 1000,
            (ngates - 1) * data["GateWidth"][0] / 1000 + data["StartRange"][0] / 1000,
            ngates,
        )

        fields = {}
        for field_name, _variable, metadata_key in RXM25_MOMENT_VARIABLES:
            fields[field_name] = get_metadata(metadata_key)
        radar.fields = fields
        for field_name in moment_values:
            radar.fields[field_name]["data"] = moment_values[field_name]

        radar.metadata["instrument_name"] = "RXM-25"
    finally:
        data.close()

    if cfradial_outfile is not None:
        pyart.io.write_cfradial(cfradial_outfile, radar, arm_time_variables=True)

    return radar
