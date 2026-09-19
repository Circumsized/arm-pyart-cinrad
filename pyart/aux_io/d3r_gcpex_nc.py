"""
Routines for reading GCPEX D3R files.

"""

import datetime

import netCDF4
import numpy as np

from ..config import FileMetadata
from ..core.radar import Radar
from ..exceptions import PyARTDataError
from ..io._validate import MAX_NGATES, MAX_NRAYS, validate_dims
from ..io.common import _test_arguments, make_time_unit_str

D3R_FIELD_NAMES = {
    # corrected reflectivity, horizontal
    "Reflectivity": "reflectivity",
    # corrected reflectivity, vertical
    "DBZV": "reflectivity",
    # differential reflectivity
    "DifferentialReflectivity": "differential_reflectivity",
    "CrossPolCorrelation": "cross_correlation_ratio",
    "ClutterPowerH": "clutter_power_h",
    "ClutterPowerV": "clutter_power_v",
    "DifferentialPhase": "differential_phase",
    "KDP": "specific_differential_phase",
    "NormalizedCoherentPower": "normalized_coherent_power",
    "Signal+Clutter_toNoise_H": "signal_to_noise_ratio",
    "Velocity": "velocity",
    "SpectralWidth": "spectrum_width",
    "SignalPower_H": "signal_power_h",
}


def read_d3r_gcpex_nc(
    filename,
    field_names=None,
    additional_metadata=None,
    file_field_names=False,
    exclude_fields=None,
    include_fields=None,
    read_altitude_from_nc=False,
    **kwargs,
):
    """
    Read a D3R GCPEX netCDF file.

    Parameters
    ----------
    filename : str
        Name of the ODIM_H5 file to read.
    field_names : dict, optional
        Dictionary mapping ODIM_H5 field names to radar field names. If a
        data type found in the file does not appear in this dictionary or has
        a value of None it will not be placed in the radar.fields dictionary.
        A value of None, the default, will use the mapping defined in the
        Py-ART configuration file.
    additional_metadata : dict of dicts, optional
        Dictionary of dictionaries to retrieve metadata from during this read.
        This metadata is not used during any successive file reads unless
        explicitly included.  A value of None, the default, will not
        introduct any addition metadata and the file specific or default
        metadata as specified by the Py-ART configuration file will be used.
    file_field_names : bool, optional
        True to use the MDV data type names for the field names. If this
        case the field_names parameter is ignored. The field dictionary will
        likely only have a 'data' key, unless the fields are defined in
        `additional_metadata`.
    exclude_fields : list or None, optional
        List of fields to exclude from the radar object. This is applied
        after the `file_field_names` and `field_names` parameters. Set
        to None to include all fields specified by include_fields.
    include_fields : list or None, optional
        List of fields to include from the radar object. This is applied
        after the `file_field_names` and `field_names` parameters. Set
        to None to include all fields not specified by exclude_fields.
    read_altitude_from_nc : bool, optional
        True if you want the altitude value to be read from the provider netCDF file.
        False will default to the value np.array([295.], dtype='float64')

    Returns
    -------
    radar : Radar
        Radar object containing data from ODIM_H5 file.

    """
    # TODO before moving to pyart.io
    # * unit test
    # * add default field mapping, etc to default config
    # * auto-detect file type with pyart.io.read function
    # * instrument parameters
    # * add additional checks for HOW attributes
    # * support for other objects (SCAN, XSEC)

    # test for non empty kwargs
    _test_arguments(kwargs)

    # create metadata retrieval object
    if field_names is None:
        field_names = D3R_FIELD_NAMES
    filemetadata = FileMetadata(
        "cfradial",
        field_names,
        additional_metadata,
        file_field_names,
        exclude_fields,
        include_fields,
    )

    # read the data
    ncobj = netCDF4.Dataset(filename)
    ncvars = ncobj.variables

    # One sweep per file
    nsweeps = 1

    # latitude, longitude and altitude
    latitude = filemetadata("latitude")
    longitude = filemetadata("longitude")
    altitude = filemetadata("altitude")

    latitude["data"] = np.array([ncobj.Latitude], dtype="float64")
    longitude["data"] = np.array([ncobj.Longitude], dtype="float64")
    altitude_list = [ncobj.Altitude] if read_altitude_from_nc else [295.0]
    altitude["data"] = np.array(altitude_list, dtype="float64")

    # metadata
    metadata = filemetadata("metadata")
    metadata["source"] = "Colorado State EE - chandrasekar"
    metadata["original_container"] = "D3R_gcpex_nc"
    metadata["nc_conventions"] = ncobj.NetCDFRevision

    metadata["version"] = ncobj.NetCDFRevision
    metadata["source"] = "Chandra"

    metadata["system"] = ncobj.RadarName
    metadata["software"] = ncobj.NetCDFRevision
    metadata["sw_version"] = ncobj.NetCDFRevision

    # sweep_start_ray_index, sweep_end_ray_index
    sweep_start_ray_index = filemetadata("sweep_start_ray_index")
    sweep_end_ray_index = filemetadata("sweep_end_ray_index")

    # FU-24 (db0886, CWE-789): NumGates is a file-controlled global
    # attribute that sizes the range axis (np.arange(nbins)); a crafted
    # NumGates=1e10 used to request a multi-exabyte allocation. Bound it,
    # and cross-check it against the gate dimension the moment variables
    # are actually stored with, so the range axis can never describe more
    # gates than the file carries.
    try:
        nbins = int(ncobj.NumGates)
    except AttributeError as err:
        raise PyARTDataError("D3R file is missing the NumGates attribute") from err
    validate_dims(nbins, limits=(MAX_NGATES,), name="D3R NumGates")
    gate_vars = [v for v in ncvars.values() if v.dimensions == ("Radial", "Gate")]
    stored_gates = {v.shape[1] for v in gate_vars if len(v.shape) == 2}
    if stored_gates and nbins not in stored_gates:
        raise PyARTDataError(
            f"D3R file declares NumGates={nbins} but its moment variables are "
            f"stored with {sorted(stored_gates)} gates"
        )

    # FU-24 (db0886): the ray-length variables must describe exactly one
    # sweep, and every moment must be stored with the (Radial, Gate) shape
    # the Radar object advertises; a mismatch used to flow straight into
    # radar.fields and the Cython kernels index the fields as (ray, gate).
    for name in ("Azimuth", "Elevation", "Time"):
        if name not in ncvars:
            raise PyARTDataError(f"D3R file is missing the {name} variable")

    azimuth_values = ncvars["Azimuth"][:]
    if azimuth_values.ndim == 0:
        azimuth_values = np.array([azimuth_values])
    # A partially written variable keeps its declared length but masks the
    # unwritten tail, so count the real entries.
    nrays = int(np.ma.count(azimuth_values))
    validate_dims(nrays, limits=(MAX_NRAYS,), name="D3R Radial count")

    expected_shape = (nrays, nbins)
    for variable in ncvars.values():
        if (
            variable.dimensions == ("Radial", "Gate")
            and variable.shape != expected_shape
        ):
            raise PyARTDataError(
                f"D3R variable {variable.name} has shape {variable.shape} but "
                f"the NumGates/Radial counts declare {expected_shape}"
            )
    ray_values = {}
    for name in ("Azimuth", "Elevation", "Time"):
        values = ncvars[name][:]
        if values.ndim == 0:
            values = np.array([values])
        n_entries = int(np.ma.count(values))
        if n_entries != nrays:
            raise PyARTDataError(
                f"D3R variable {name} has {n_entries} entries but the Radial "
                f"count is {nrays}"
            )
        ray_values[name] = values

    azimuth_values = ray_values["Azimuth"]
    elevation_values = ray_values["Elevation"]
    time_values = ray_values["Time"]

    rays_per_sweep = np.shape(azimuth_values)
    ssri = np.cumsum(np.append([0], rays_per_sweep[:-1])).astype("int32")
    seri = np.cumsum(rays_per_sweep).astype("int32") - 1
    sweep_start_ray_index["data"] = ssri
    sweep_end_ray_index["data"] = seri

    # sweep_number
    sweep_number = filemetadata("sweep_number")
    sweep_number["data"] = np.arange(nsweeps, dtype="int32")

    # sweep_mode
    sweep_mode = filemetadata("sweep_mode")
    sweep_mode["data"] = np.array(nsweeps * ["azimuth_surveillance"])

    # scan_type
    if ncobj.ScanType == 2:
        scan_type = "ppi"
    else:
        scan_type = "rhi"

    # fixed_angle
    fixed_angle = filemetadata("fixed_angle")
    if ncobj.ScanType == 2:
        sweep_el = elevation_values[0]
    else:
        sweep_el = azimuth_values[0]
    fixed_angle["data"] = np.array([sweep_el], dtype="float32")

    # elevation
    elevation = filemetadata("elevation")
    elevation["data"] = elevation_values

    # range
    _range = filemetadata("range")
    # check that the gate spacing is constant between sweeps
    rstart = ncvars["StartRange"][:]
    if any(rstart != rstart[0]):
        raise ValueError("range start changes between sweeps")
    rscale = ncvars["GateWidth"][:] / 1000.0
    if any(rscale != rscale[0]):
        raise ValueError("range scale changes between sweeps")

    _range["data"] = np.arange(nbins, dtype="float32") * rscale[0] + rstart[0] * 1000.0
    _range["meters_to_center_of_first_gate"] = rstart[0]
    _range["meters_between_gates"] = float(rscale[0])

    # azimuth
    azimuth = filemetadata("azimuth")
    azimuth["data"] = azimuth_values

    # time
    _time = filemetadata("time")
    start_time = datetime.datetime.utcfromtimestamp(ncobj.Time)
    _time["units"] = make_time_unit_str(start_time)
    _time["data"] = (time_values - ncobj.Time).astype("float32")

    # fields
    # all variables with dimensions of 'Radial', 'Gate' are fields
    keys = [k for k, v in ncvars.items() if v.dimensions == ("Radial", "Gate")]

    fields = {}
    for key in keys:
        field_name = filemetadata.get_field_name(key)
        if field_name is None:
            if exclude_fields is not None and key in exclude_fields:
                continue
            if include_fields is not None:
                if key not in include_fields:
                    continue
            field_name = key
        fields[field_name] = _ncvar_to_dict(ncvars[key])

    # instrument_parameters
    instrument_parameters = None

    return Radar(
        _time,
        _range,
        fields,
        metadata,
        scan_type,
        latitude,
        longitude,
        altitude,
        sweep_number,
        sweep_mode,
        fixed_angle,
        sweep_start_ray_index,
        sweep_end_ray_index,
        azimuth,
        elevation,
        instrument_parameters=instrument_parameters,
    )


def _ncvar_to_dict(ncvar):
    """Convert a NetCDF Dataset variable to a dictionary."""
    # copy all attribute except for scaling parameters
    d = {
        k: getattr(ncvar, k)
        for k in ncvar.ncattrs()
        if k not in ["scale_factor", "add_offset"]
    }
    d["data"] = ncvar[:]
    if np.isscalar(d["data"]):
        # netCDF4 1.1.0+ returns a scalar for 0-dim array, we always want
        # 1-dim+ arrays with a valid shape.
        d["data"] = np.array(d["data"])
        d["data"].shape = (1,)
    return d
