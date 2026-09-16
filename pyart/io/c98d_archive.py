# coding : utf-8
"""
pyart.io.c98d_archive
=====================

Functions for reading C-band (C98D / NUIST) dual-polarization Doppler radar
archive files.

"""

import numpy as np

from pyart.config import FileMetadata, get_fillvalue
from pyart.core.radar import Radar
from pyart.io.common import _test_arguments, make_time_unit_str, prepare_for_read

from .C98DRadFile import C98DRadFile


def c98dfile_archive(
    filename,
    field_names=None,
    additional_metadata=None,
    file_field_names=False,
    exclude_fields=None,
    cutnum=None,
    delay_field_loading=False,
    **kwargs,
):
    """
    Read a C98D (C-band dual-polarization) radar archive file.

    Parameters
    ----------
    filename : str or file-like
        Filename or file-like object of the C98D archive file.
    field_names : dict, optional
        Dictionary mapping C98D moment names to radar field names. A value of
        None uses the mapping defined in the metadata configuration file and a
        moment not present in the dictionary is not read.
    additional_metadata : dict of dicts, optional
        Dictionary of dictionaries to retrieve metadata from during this read.
    file_field_names : bool, optional
        True to keep the file (raw moment) field names instead of mapping them
        to Py-ART standard names.
    exclude_fields : list or None, optional
        List of fields to exclude from the radar object.
    cutnum : list of int or None, optional
        Cuts (sweeps, 0 based) to read. None (the default) reads all cuts.

    Returns
    -------
    radar : Radar
        Radar object containing the moments and sweeps in the volume.

    Note
    ----
    The dual-polarization moment decoding follows the C98D file structure and
    should be validated against real NUIST C-band data before production use.

    """
    _test_arguments(kwargs)

    filemetadata = FileMetadata(
        "c98d_archive",
        field_names,
        additional_metadata,
        file_field_names,
        exclude_fields,
    )

    nfile = C98DRadFile(prepare_for_read(filename))

    if cutnum is None:
        cutnum = list(nfile.scans)
    else:
        cutnum = list(cutnum)

    cut_start = np.array(nfile.cut_start, dtype="int64")
    cut_end = np.array(nfile.cut_end, dtype="int64")

    # time
    time = filemetadata("time")
    time_start_seconds = float(nfile.task_config["volume_start_time"])
    seconds = np.array(nfile.radial_info["seconds"], dtype="float64")
    time_parts = [seconds[cut_start[c] : cut_end[c]] for c in cutnum]
    time["data"] = np.concatenate(time_parts) - time_start_seconds
    time["units"] = make_time_unit_str(nfile.get_volume_start_time)

    # fields (assemble all moments, padding to a common number of gates)
    fields = {}
    max_ngates = 0
    for raw_moment in nfile.get_moment_type:
        field_name = filemetadata.get_field_name(raw_moment)
        if field_name is None:
            continue

        dic = filemetadata(field_name)
        dic["_FillValue"] = get_fillvalue()

        moment_data = []
        first = nfile.get_data(raw_moment, cutnum[0])
        ngates = first.shape[1]
        max_ngates = max(max_ngates, ngates)
        moment_data.append(first)
        for cn in cutnum[1:]:
            fndata = nfile.get_data(raw_moment, cn)
            if fndata.shape[1] < ngates:
                pad = np.full((fndata.shape[0], ngates - fndata.shape[1]), np.nan)
                fndata = np.c_[fndata, pad]
            elif fndata.shape[1] > ngates:
                raise ValueError(f"Inconsistent gate count for moment {raw_moment}")
            moment_data.append(fndata)
        dic["data"] = np.concatenate(moment_data, axis=0)
        fields[field_name] = dic

    # range (from the reference reflectivity resolution, length set to the
    # maximum number of gates so all fields share a common gate axis)
    _range = filemetadata("range")
    gate_spacing = float(nfile.cut_info["log_reso"][cutnum[0]])
    first_gate = float(nfile.cut_info["start_range"][cutnum[0]])
    _range["data"] = first_gate + np.arange(max_ngates, dtype="float32") * gate_spacing
    _range["meters_to_center_of_first_gate"] = first_gate
    _range["meters_between_gates"] = gate_spacing

    # pad every field to the common maximum number of gates and mask the
    # NaN fill so plotting and gridding see masked gates, not NaN values
    for field_name in fields:
        data = fields[field_name]["data"]
        if data.shape[1] < max_ngates:
            pad = np.full((data.shape[0], max_ngates - data.shape[1]), np.nan)
            fields[field_name]["data"] = np.ma.masked_invalid(np.c_[data, pad])

    # metadata
    metadata = filemetadata("metadata")
    metadata["original_container"] = "C98D"

    # scan_type
    scan_type = nfile.scan_type
    if scan_type == "Single PPI":
        scan_type = "ppi"
    elif scan_type == "Single Sector":
        scan_type = "sector"

    # latitude, longitude, altitude
    latitude = filemetadata("latitude")
    longitude = filemetadata("longitude")
    altitude = filemetadata("altitude")
    lat, lon, height = nfile.get_location
    latitude["data"] = np.array([lat], dtype="float64")
    longitude["data"] = np.array([lon], dtype="float64")
    altitude["data"] = np.array([height], dtype="float64")

    # sweep metadata
    nsweeps = len(cutnum)
    nrays_per_sweep = [cut_end[c] - cut_start[c] for c in cutnum]

    sweep_number = filemetadata("sweep_number")
    sweep_mode = filemetadata("sweep_mode")
    sweep_start_ray_index = filemetadata("sweep_start_ray_index")
    sweep_end_ray_index = filemetadata("sweep_end_ray_index")
    fixed_angle = filemetadata("fixed_angle")

    sweep_number["data"] = np.arange(nsweeps, dtype="int32")
    sweep_mode["data"] = np.array(
        nsweeps * ["rhi"] if scan_type == "rhi" else nsweeps * ["azimuth_surveillance"],
        dtype="S",
    )

    sweep_end_ray_index["data"] = np.cumsum(nrays_per_sweep, dtype="int32") - 1
    sweep_start_ray_index["data"] = np.concatenate(
        [[0], np.cumsum(nrays_per_sweep)[:-1]]
    ).astype("int32")

    # azimuth, elevation
    azimuth = filemetadata("azimuth")
    elevation = filemetadata("elevation")
    azimuth_full = np.array(nfile.get_azimuth, dtype="float64")
    elevation_full = np.array(nfile.get_elevation, dtype="float64")
    azimuth["data"] = np.concatenate(
        [azimuth_full[cut_start[c] : cut_end[c]] for c in cutnum]
    )
    elevation["data"] = np.concatenate(
        [elevation_full[cut_start[c] : cut_end[c]] for c in cutnum]
    )

    cutnum_idx = np.array(cutnum, dtype="int64")
    fixed_angle["data"] = elevation_full[cut_start[cutnum_idx]].astype("float32")

    # instrument_parameters
    nyquist_velocity = filemetadata("nyquist_velocity")
    unambiguous_range = filemetadata("unambiguous_range")

    nyq = np.array(nfile.get_nyquist_vel, dtype="float32")[cutnum_idx]
    nyquist_velocity["data"] = np.repeat(nyq, nrays_per_sweep)

    unamb_rng = np.array(nfile.get_unambiguous_range, dtype="float32")[cutnum_idx]
    unambiguous_range["data"] = np.repeat(unamb_rng, nrays_per_sweep)

    instrument_parameters = {
        "unambiguous_range": unambiguous_range,
        "nyquist_velocity": nyquist_velocity,
    }

    # C-band system calibration offsets
    calibration = nfile.get_calibration
    radar_calibration = {
        "zdr_calibration": {
            "data": np.array([calibration["zdr_calibration"]], dtype="float32"),
            "units": "dB",
            "long_name": "ZDR system calibration offset",
            "_FillValue": get_fillvalue(),
        },
        "phase_calibration": {
            "data": np.array([calibration["phase_calibration"]], dtype="float32"),
            "units": "degrees",
            "long_name": "PhiDP system calibration offset",
            "_FillValue": get_fillvalue(),
        },
        "ldr_calibration": {
            "data": np.array([calibration["ldr_calibration"]], dtype="float32"),
            "units": "dB",
            "long_name": "LDR system calibration offset",
            "_FillValue": get_fillvalue(),
        },
    }

    if not hasattr(filename, "read"):
        nfile.close()

    return Radar(
        time,
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
        radar_calibration=radar_calibration,
    )


def read_c98d(filename, **kwargs):
    """
    Convenience wrapper around :func:`c98dfile_archive`.

    Reads a C98D (C-band dual-polarization, NUIST) radar archive file and
    returns a :class:`pyart.core.Radar`. This is the band-named entry point
    matching ``read_xband`` / ``read_sband_radar``; it delegates to
    :func:`c98dfile_archive` without adding behaviour.

    Parameters
    ----------
    filename : str or file-like
        Filename or file-like object of the C98D archive file.
    **kwargs
        Forwarded to :func:`c98dfile_archive` (e.g. ``cutnum``,
        ``field_names``).

    Returns
    -------
    radar : Radar

    Notes
    -----
    The returned radar is tagged with ``metadata['radar_band'] = 'C'`` so the
    band-aware dual-polarization helpers select the C-band parameters.

    """
    radar = c98dfile_archive(filename, **kwargs)
    radar.metadata["radar_band"] = "C"
    return radar
