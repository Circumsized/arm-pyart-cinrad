"""
Unit tests for reading Chinese S-band (CINRAD-SA) Level II archive files.

The file bytes are synthesized from the structure definitions in
:mod:`pyart.io.sband_radar`, so these tests do not require any real data.
"""

import io
import re
import struct

import numpy as np

from pyart.io.sband_archive import read_sband_archive
from pyart.io.sband_radar import MSG_1, MSG_HEADER, RECORD_SIZE


def _pack_struct(structure, overrides):
    """Pack a structure using the standard-size ('=') byte order."""
    fmt = "=" + "".join(code for _, code in structure)
    values = []
    for name, code in structure:
        match = re.match(r"(\d*)([a-zA-Z])", code)
        count = int(match.group(1) or 1)
        typ = match.group(2)
        if typ == "s":
            values.append(overrides.get(name, b""))
        elif typ == "B":
            val = overrides.get(name, 0)
            if isinstance(val, (bytes, bytearray, list, tuple)):
                values.extend(val[:count])
            else:
                values.extend([val] * count)
        else:
            values.append(overrides.get(name, 0))
    return struct.pack(fmt, *values)


def _build_sband_file():
    record = bytearray(RECORD_SIZE)

    msg_header = _pack_struct(MSG_HEADER, {"style": 1})
    msg1 = _pack_struct(
        MSG_1,
        {
            "collect_ms": 0,
            "collect_date": 1,
            "unambig_range": 4600,
            "azimuth_angle": 0,
            "azimuth_number": 0,
            "radial_status": 0,
            "elevation_angle": 0,
            "elevation_number": 1,
            "sur_range_first": 0,
            "doppler_range_first": 0,
            "sur_range_step": 250,
            "doppler_range_step": 250,
            "sur_nbins": 4,
            "doppler_nbins": 0,
            "cut_sector_num": 0,
            "calib_const": 0,
            "sur_pointer": 100,
            "vel_pointer": 0,
            "width_pointer": 0,
            "doppler_resolution": 0,
            "vcp": 0,
            "nyquist_vel": 1000,
        },
    )

    msg_header_size = len(msg_header)
    record[0:msg_header_size] = msg_header
    record[msg_header_size : msg_header_size + len(msg1)] = msg1
    record[128:132] = bytes([86, 88, 90, 92])

    return bytes(record)


def test_read_sband_archive_reads_reflectivity():
    radar = read_sband_archive(
        io.BytesIO(_build_sband_file()), station=(33.431, 120.201, 80)
    )

    assert "reflectivity" in radar.fields
    assert radar.fields["reflectivity"]["units"] == "dBZ"

    refl = radar.fields["reflectivity"]["data"]
    assert refl.shape == (1, 4)
    assert np.allclose(refl.data, [[10.0, 11.0, 12.0, 13.0]])


def test_read_sband_archive_metadata():
    radar = read_sband_archive(
        io.BytesIO(_build_sband_file()), station=(33.431, 120.201, 80)
    )

    assert radar.latitude["data"][0] == 33.431
    assert radar.longitude["data"][0] == 120.201
    assert radar.altitude["data"][0] == 80

    assert np.allclose(radar.instrument_parameters["nyquist_velocity"]["data"][0], 10.0)
    assert np.allclose(
        radar.instrument_parameters["unambiguous_range"]["data"][0], 460.0
    )

    assert len(radar.range["data"]) == 4
    assert np.allclose(radar.range["data"], [0, 250, 500, 750])
    assert radar.nrays == 1
    assert radar.nsweeps == 1
