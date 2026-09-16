"""
pyart.io.xband_native
=====================

Experimental pure-Python readers for non-standard Chinese X-band radar
base-data formats (724XSP and SCRXD-01).

Both formats are vendor-specific with only publicly documented layout
sketches; this implementation provides a minimal structural parser and is
**EXPERIMENTAL** -- it must be validated against real sample files before
production use. Register explicit entry points only; the automatic reader
(``pyart.io.read``) deliberately does not dispatch to these formats.

"""

import datetime
import struct

import numpy as np

from pyart.config import get_fillvalue, get_metadata
from pyart.core.radar import Radar
from pyart.io.common import make_time_unit_str

# 724XSP layout (little-endian)
XSP724_HEADER = [
    ("magic", "4s"),
    ("major_version", "B"),
    ("minor_version", "B"),
    ("site_lat", "f"),
    ("site_lon", "f"),
    ("site_alt", "f"),
    ("ngates", "H"),
    ("gate_spacing", "f"),
    ("nyquist_speed", "f"),
    ("nrays", "H"),
    ("fixed_angle", "f"),
]
XSP724_RAY = [("azimuth", "f"), ("elevation", "f")]
XSP724_MOMENT = [("data_type", "B"), ("scale", "B"), ("offset", "B")]

# SCRXD-01 layout (little-endian)
SCRXD_HEADER = [
    ("magic", "4s"),
    ("major_version", "B"),
    ("minor_version", "B"),
    ("site_lat", "d"),
    ("site_lon", "d"),
    ("site_alt", "f"),
    ("ngates", "H"),
    ("gate_spacing", "f"),
    ("nyquist_speed", "f"),
    ("nrays", "H"),
    ("fixed_angle", "f"),
]
SCRXD_RAY = [("azimuth", "f"), ("elevation", "f")]
SCRXD_MOMENT = [("data_type", "B"), ("scale", "B"), ("offset", "B")]

# data_type codes shared by both formats
MOMENT_CODES = {
    1: "reflectivity",  # dBZ, uint8 raw
    2: "velocity",  # m/s, uint8 raw signed around 128
    3: "spectrum_width",  # m/s, uint8 raw
    4: "differential_reflectivity",  # dB
    5: "differential_phase",  # degrees
    6: "cross_correlation_ratio",  # phi_hv
}


def _fmt(layout):
    return "=" + "".join(code for _, code in layout)


def _pack_structure(values, layout):
    return struct.pack(_fmt(layout), *values)


def _radar_from_arrays(
    metadata,
    time_seconds,
    ranges,
    fields,
    az_data,
    el_data,
    sweep_number,
    fixed_angle,
    nyquist_speed,
):
    """Assemble the common :class:`pyart.core.Radar` from parsed arrays."""
    ngates = len(ranges)
    nrays = len(az_data)
    for name, dic in fields.items():
        dic = dict(dic)
        dic["_FillValue"] = get_fillvalue()
        if dic["data"].shape != (nrays, ngates):
            raise ValueError(
                "Field {} has shape {}, expected ({}, {})".format(
                    name, dic["data"].shape, nrays, ngates
                )
            )
        fields[name] = dic

    time = get_metadata("time")
    time["data"] = np.asarray(time_seconds, dtype="float64")
    time["units"] = make_time_unit_str(datetime.datetime(1970, 1, 1))

    _range = get_metadata("range")
    _range["data"] = np.asarray(ranges, dtype="float32")
    _range["meters_to_center_of_first_gate"] = float(ranges[0])
    _range["meters_between_gates"] = float(ranges[1] - ranges[0])

    latitude = get_metadata("latitude")
    longitude = get_metadata("longitude")
    altitude = get_metadata("altitude")
    lat, lon, alt = metadata["lat"], metadata["lon"], metadata["alt"]
    latitude["data"] = np.array([lat], dtype="float64")
    longitude["data"] = np.array([lon], dtype="float64")
    altitude["data"] = np.array([alt], dtype="float64")

    nrays_per_sweep = np.diff(
        np.concatenate(
            [[0], np.cumsum([len(az_data) // len(fixed_angle)] * len(fixed_angle))]
        )
    )
    sweep_end_ray_index = np.cumsum(nrays_per_sweep, dtype="int32") - 1
    sweep_start_ray_index = np.concatenate(
        [[0], np.cumsum(nrays_per_sweep)[:-1]]
    ).astype("int32")

    instrument_parameters = {
        "nyquist_velocity": {
            "data": np.full(nrays, nyquist_speed, dtype="float32"),
            "units": "meters_per_second",
            "_FillValue": get_fillvalue(),
        }
    }

    return Radar(
        time,
        _range,
        fields,
        {"original_container": metadata["container"], "radar_band": "X"},
        "ppi",
        latitude,
        longitude,
        altitude,
        {"data": np.arange(len(fixed_angle), dtype="int32")},
        {"data": np.array(len(fixed_angle) * ["azimuth_surveillance"], dtype="S")},
        {"data": np.asarray(fixed_angle, dtype="float32")},
        {"data": sweep_start_ray_index.astype("int32")},
        {"data": sweep_end_ray_index.astype("int32")},
        {"data": np.asarray(az_data, dtype="float32")},
        {"data": np.asarray(el_data, dtype="float32")},
        instrument_parameters=instrument_parameters,
    )


def _decode_uint8_moment(raw, scale, offset, data_type):
    """Decode uint8 raw values to physical units.

    Values are scaled as ``physical = (raw - offset) / scale`` for
    velocity-like fields and ``physical = raw / scale + offset``
    otherwise. The choice follows the public layout sketches of the
    724XSP/SCRXD-01 dialects.
    """
    data = raw.astype("float32")
    if scale == 0:
        scale = 1
    if data_type == MOMENT_T_VEL:
        data = (data - 128.0 - offset) / scale
    else:
        data = data / scale + offset
    # 0 and 255 are fill sentinels in both dialects
    data[raw == 0] = np.nan
    data[raw == 255] = np.nan
    return np.ma.masked_invalid(data)


MOMENT_T_VEL = 2


def _read_volume(
    raw, header, ray_layout, moment_layout, moment_codes, container, expected_magic
):
    """Shared parser: validate magic, unpack rays and moment blocks."""
    size_h = struct.calcsize(_fmt(header))
    if len(raw) < size_h:
        raise ValueError(f"truncated {container} header")
    hdr_vals = struct.unpack(_fmt(header), raw[:size_h])
    if hdr_vals[0] != expected_magic:
        raise ValueError(f"bad magic {hdr_vals[0]!r} for {container}")
    keys = [name for name, _ in header]
    info = dict(zip(keys, hdr_vals))
    pos = size_h

    ngates = int(info["ngates"])
    nrays = int(info["nrays"])
    ray_size = struct.calcsize(_fmt(ray_layout))
    mom_size = struct.calcsize(_fmt(moment_layout))

    azimuth = np.empty(nrays, dtype="float32")
    elevation = np.empty(nrays, dtype="float32")
    fields_raw = {code: [] for code in moment_codes}
    for ray in range(nrays):
        ray_vals = struct.unpack_from(_fmt(ray_layout), raw, pos)
        pos += ray_size
        azimuth[ray] = ray_vals[0]
        elevation[ray] = ray_vals[1]
        for code in moment_codes:
            mom_vals = struct.unpack_from(_fmt(moment_layout), raw, pos)
            pos += mom_size
            dtype, scale, offset = mom_vals
            if pos + ngates > len(raw):
                raise ValueError(f"truncated moment data for ray {ray}")
            fields_raw[code].append(
                (
                    dtype,
                    scale,
                    offset,
                    np.frombuffer(raw, dtype="uint8", count=ngates, offset=pos).copy(),
                )
            )
            pos += ngates

    ranges = (np.arange(ngates, dtype="float32") + 1) * info["gate_spacing"]

    fields = {}
    for code, name in moment_codes.items():
        frames = [item for item in fields_raw[code] if item[0] == code]
        if not frames:
            continue
        data = np.empty((nrays, ngates), dtype="float32")
        for ray, (_, scale, offset, raw_gates) in enumerate(frames):
            data[ray] = _decode_uint8_moment(raw_gates, scale, offset, code)
        fields[name] = {"data": data, "units": _MOMENT_UNITS.get(name)}

    metadata = {
        "lat": info["site_lat"],
        "lon": info["site_lon"],
        "alt": info["site_alt"],
        "container": container,
    }
    return metadata, ranges, fields, azimuth, elevation, info


_MOMENT_UNITS = {
    "reflectivity": "dBZ",
    "velocity": "meters_per_second",
    "spectrum_width": "meters_per_second",
    "differential_reflectivity": "dB",
    "differential_phase": "degrees",
    "cross_correlation_ratio": "ratio",
}


def read_xband_724xsp(filename):
    """
    Read a non-standard 724XSP X-band base-data file (experimental).

    Parameters
    ----------
    filename : str
        Path to the 724XSP file.

    Returns
    -------
    radar : pyart.core.Radar

    Notes
    -----
    Layout assumptions are based on public sketches; validate against real
    samples before production use.
    """
    with open(filename, "rb") as f:
        raw = f.read()
    metadata, ranges, fields, az, el, info = _read_volume(
        raw, XSP724_HEADER, XSP724_RAY, XSP724_MOMENT, MOMENT_CODES, "724XSP", b"724X"
    )
    return _radar_from_arrays(
        metadata,
        np.arange(len(az), dtype="float64"),
        ranges,
        fields,
        az,
        el,
        0,
        [info["fixed_angle"]],
        info["nyquist_speed"],
    )


def read_xband_scrxd01(filename):
    """
    Read a non-standard SCRXD-01 X-band base-data file (experimental).

    Parameters
    ----------
    filename : str
        Path to the SCRXD-01 file.

    Returns
    -------
    radar : pyart.core.Radar

    Notes
    -----
    Layout assumptions are based on public sketches; validate against real
    samples before production use.
    """
    with open(filename, "rb") as f:
        raw = f.read()
    metadata, ranges, fields, az, el, info = _read_volume(
        raw, SCRXD_HEADER, SCRXD_RAY, SCRXD_MOMENT, MOMENT_CODES, "SCRXD-01", b"SCRX"
    )
    return _radar_from_arrays(
        metadata,
        np.arange(len(az), dtype="float64"),
        ranges,
        fields,
        az,
        el,
        0,
        [info["fixed_angle"]],
        info["nyquist_speed"],
    )
