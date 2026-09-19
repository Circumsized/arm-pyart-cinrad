"""
Security tests for the S-band (CINRAD-SA) Level II reader
(FU-25, 9b7666, CWE-789 / CWE-400).

The per-ray gate count, the gate spacing and the range extent are
unbounded unsigned header fields. Before FU-25 a ray declaring more gates
than the range axis holds (or than its own record stores, since
``np.frombuffer`` clamps the slice to the buffer end) aborted the read with
a raw broadcast ``ValueError``, and a crafted ``sur_range_step`` /
``sur_nbins`` pair sized an unbounded ``np.arange`` range axis. The reader
must clamp the per-ray assignment (as ``nexrad_level2.py:535`` does) and
reject absurd declarations, while well-formed files read unchanged.

The file bytes are synthesized from the structure definitions in
:mod:`pyart.io.sband_radar`, so no real data is required.
"""

import io
import re
import struct

import numpy as np
import pytest

from pyart.exceptions import PyARTDataError
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


def _build_record(gates=(86, 88, 90, 92), sur_pointer=100, **msg1_overrides):
    """Build a single MSG1 radial record."""
    record = bytearray(RECORD_SIZE)

    msg_header = _pack_struct(MSG_HEADER, {"style": 1})
    overrides = {
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
        "sur_nbins": len(gates),
        "doppler_nbins": 0,
        "cut_sector_num": 0,
        "calib_const": 0,
        "sur_pointer": sur_pointer,
        "vel_pointer": 0,
        "width_pointer": 0,
        "doppler_resolution": 0,
        "vcp": 0,
        "nyquist_vel": 1000,
    }
    overrides.update(msg1_overrides)
    msg1 = _pack_struct(MSG_1, overrides)

    msg_header_size = len(msg_header)
    record[0:msg_header_size] = msg_header
    record[msg_header_size : msg_header_size + len(msg1)] = msg1
    data_offset = msg_header_size + overrides["sur_pointer"]
    record[data_offset : data_offset + len(gates)] = bytes(gates)

    return bytes(record)


def _build_file(*records):
    return b"".join(records)


def _read(data, **kwargs):
    kwargs.setdefault("station", (33.431, 120.201, 80))
    return read_sband_archive(io.BytesIO(data), **kwargs)


class TestWellFormedFileUnchanged:
    def test_single_ray_reads_unchanged(self):
        radar = _read(_build_file(_build_record()))
        assert radar.fields["reflectivity"]["data"].shape == (1, 4)
        assert np.allclose(
            radar.fields["reflectivity"]["data"], [[10.0, 11.0, 12.0, 13.0]]
        )
        assert np.allclose(radar.range["data"], [0, 250, 500, 750])
        assert radar.nrays == 1
        assert radar.nsweeps == 1

    def test_multiple_rays_read_unchanged(self):
        records = [
            _build_record(gates=(86, 88, 90, 92), azimuth_angle=0),
            _build_record(gates=(87, 89, 91, 93), azimuth_angle=90),
        ]
        radar = _read(_build_file(*records))
        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (2, 4)
        assert np.allclose(data[0], [10.0, 11.0, 12.0, 13.0])
        assert np.allclose(data[1], [10.5, 11.5, 12.5, 13.5])


class TestOversizedDeclarations:
    def test_range_axis_over_limit_rejected(self):
        # FU-25 (9b7666, CWE-789): every gate field is a uint16, but the
        # range extent combines them (first + step * nbins) and the axis is
        # normalized by the *smallest* spacing: a REF sweep at 1 m spacing
        # plus a VEL sweep at 60000 m spacing requests billions of gates.
        record = _build_record(
            gates=(86, 88, 90, 92),
            sur_range_step=1,
            sur_nbins=60000,
            doppler_range_step=60000,
            doppler_nbins=60000,
            vel_pointer=100,
        )
        with pytest.raises(PyARTDataError, match="range axis"):
            _read(_build_file(record))

    def test_non_positive_gate_spacing_rejected(self):
        with pytest.raises(PyARTDataError, match="gate spacing"):
            _read(_build_file(_build_record(sur_range_step=0)))


class TestClampedAssignment:
    def test_ray_wider_than_range_axis_clamped(self):
        # The second ray declares 1000 gates while the range axis (built
        # from the first ray) holds 4: used to raise a broadcast ValueError.
        records = [
            _build_record(gates=(86, 88, 90, 92)),
            _build_record(gates=(166, 168, 170, 172) * 25, sur_nbins=100),
        ]
        radar = _read(_build_file(*records))
        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (2, 4)
        assert np.allclose(data[0], [10.0, 11.0, 12.0, 13.0])
        # The oversized ray is clamped to the range axis width.
        assert np.allclose(data[1], [50.0, 51.0, 52.0, 53.0])

    def test_later_ray_max_uint16_declaration_clamped(self):
        # A later ray declares the largest gate count the format can hold
        # (65535) against a 4-gate range axis: clamped, not crashed.
        records = [
            _build_record(gates=(86, 88, 90, 92)),
            _build_record(gates=(166, 168, 170, 172) * 100, sur_nbins=65535),
        ]
        radar = _read(_build_file(*records))
        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (2, 4)
        assert np.allclose(data[1], [50.0, 51.0, 52.0, 53.0])

    def test_ray_declaring_beyond_stored_bytes_clamped(self):
        # sur_nbins claims 1000 gates but the record only stores bytes after
        # the pointer; np.frombuffer silently clamps the slice.
        record = _build_record(
            gates=tuple(range(100)), sur_nbins=1000, sur_pointer=2200
        )
        radar = _read(_build_file(record))
        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (1, 1000)
        # The stored tail is placed (values 0 and 1 are masked by the <= 1
        # below-threshold rule), the rest stays masked.
        assert data.mask[0, 100:].all()
        assert np.allclose(data.data[0, :4], [-33.0, -32.5, -32.0, -31.5])
        assert data.mask[0, :2].all()
