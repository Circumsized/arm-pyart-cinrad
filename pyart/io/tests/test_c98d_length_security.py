"""
Security tests for the C98D reader (FU-19, e756f4, CWE-789 / CWE-400).

The moment ``length`` and radial / cut counts are *signed* 32-bit fields
taken straight from the file. Before FU-19 a negative moment length moved
the parse cursor backwards (silently re-parsing already consumed bytes and
mis-decoding the rest of the volume), and an oversized length aborted the
read with a raw ``ValueError`` from ``numpy.frombuffer``. Bogus cut /
moment counts and truncated structures surfaced as ``struct.error`` /
``KeyError``. All of these must be rejected uniformly with
``PyARTDataError`` while well-formed files decode unchanged.

The C98D bytes are synthesized from the structure definitions in
:mod:`pyart.io.C98DRadFile`, so no real data is required.
"""

import io
import re
import struct

import pytest

from pyart.exceptions import PyARTDataError
from pyart.io.c98d_archive import c98dfile_archive
from pyart.io.C98DRadFile import (
    CUT_CONFIG,
    GENERIC_HEADER,
    MOMENT_HEADER,
    RADIAL_HEADER,
    SITE_CONFIG,
    TASK_CONFIG,
    C98DRadFile,
)

# Helpers mirror test_c98d_archive.py so this file stays self-contained.


def _pack_struct(structure, overrides):
    """Pack a structure defined as a list of (name, format_code) tuples."""
    fmt = "".join(code for _, code in structure)
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


def _encode_16bit(values):
    """Encode integer gate values as little-endian byte pairs."""
    out = bytearray()
    for value in values:
        value = int(value)
        out.append(value & 0xFF)
        out.append((value >> 8) & 0xFF)
    return bytes(out)


DBZ_GATES = [86, 88, 90, 92]
DBZ_MOMENT = {
    "data_type": 2,
    "scale": 2,
    "offset": 66,
    "bin_length": 2,
    "length": 2 * len(DBZ_GATES),
}
RAY0 = {
    "radial_state": 0,
    "azimuth": 0.0,
    "elevation": 0.5,
    "seconds": 1000000000,
}
RAY_END = {
    "radial_state": 4,
    "azimuth": 1.0,
    "elevation": 0.5,
    "seconds": 1000000001,
}


def _build_file(radials, cut_number=1):
    """Build a minimal C98D file from (radial, moments) records.

    Each radial is ``(radial_overrides, moments)`` where moments is a list
    of ``(moment_overrides, payload_bytes)``.
    """
    buf = bytearray()
    buf += _pack_struct(
        GENERIC_HEADER,
        {
            "magic_word": 0x12345678,
            "major_version": 1,
            "minor_version": 0,
            "generic_type": 1,
            "product_type": 1,
        },
    )
    buf += _pack_struct(
        SITE_CONFIG, {"latitude": 32.2, "longitude": 118.7, "height": 45}
    )
    buf += _pack_struct(
        TASK_CONFIG,
        {
            "polarization_type": 3,
            "scan_type": 0,
            "volume_start_time": 1000000000,
            "cut_number": cut_number,
        },
    )
    buf += _pack_struct(
        CUT_CONFIG,
        {
            "log_reso": 250,
            "doppler_reso": 250,
            "nyquist_speed": 10.0,
            "elevation": 0.5,
        },
    )
    for radial_over, moments in radials:
        overrides = dict(radial_over)
        overrides.setdefault("moment_number", len(moments))
        buf += _pack_struct(RADIAL_HEADER, overrides)
        for moment_over, payload in moments:
            buf += _pack_struct(MOMENT_HEADER, moment_over)
            buf += payload
    return bytes(buf)


def _wellformed_radials():
    """A single-ray PPI with one dBZ moment, closed by a state-4 radial."""
    return [(RAY0, [(DBZ_MOMENT, _encode_16bit(DBZ_GATES))]), (RAY_END, [])]


def _bad_moment_file(moment_overrides):
    """A file whose only moment carries ``moment_overrides``."""
    moment = dict(DBZ_MOMENT, **moment_overrides)
    return _build_file([(RAY0, [(moment, _encode_16bit(DBZ_GATES))]), (RAY_END, [])])


class TestMalformedMomentLength:
    """The signed moment length must be bounded before use."""

    @pytest.mark.parametrize("length", [0, -1, -4, -(2**31)])
    def test_non_positive_moment_length_rejected(self, length):
        # FU-19 (e756f4, CWE-789/400): a negative length used to slice
        # buf[pos : pos + length] to nothing and then move self.pos
        # *backwards*, re-parsing the buffer at a shifted offset until a
        # struct.error surfaced the misalignment.
        with pytest.raises(PyARTDataError):
            C98DRadFile(io.BytesIO(_bad_moment_file({"length": length})))

    @pytest.mark.parametrize("length", [100, 2**31 - 1])
    def test_moment_length_past_end_rejected(self, length):
        # FU-19 (e756f4, CWE-400): np.frombuffer(count=leng) used to raise a
        # raw ValueError because the clamped slice was shorter than count.
        with pytest.raises(PyARTDataError, match="remain"):
            C98DRadFile(io.BytesIO(_bad_moment_file({"length": length})))

    def test_negative_length_rejected_end_to_end(self):
        with pytest.raises(PyARTDataError):
            c98dfile_archive(io.BytesIO(_bad_moment_file({"length": -1})))


class TestUnknownMomentType:
    def test_unknown_data_type_rejected(self):
        # FU-19 (e756f4): MOMENTS_TYPE[data_type] used to raise a bare
        # KeyError from inside _get_moment_data.
        with pytest.raises(PyARTDataError, match="data type"):
            C98DRadFile(io.BytesIO(_bad_moment_file({"data_type": 99})))


class TestBoundedParseLoops:
    """Bogus counts must not walk the cursor past the buffer."""

    def test_bogus_cut_number_rejected(self):
        # FU-19 (e756f4, CWE-400): a 2**20 cut count used to loop until the
        # structure read ran off the end with a struct.error.
        with pytest.raises(PyARTDataError, match="cut"):
            C98DRadFile(
                io.BytesIO(_build_file(_wellformed_radials(), cut_number=2**20))
            )

    def test_excessive_moment_count_rejected(self):
        with pytest.raises(PyARTDataError):
            C98DRadFile(
                io.BytesIO(_build_file([(dict(RAY_END, moment_number=10), [])]))
            )

    def test_unterminated_radial_sequence_rejected(self):
        # FU-19 (e756f4, CWE-400): radial_state values outside the documented
        # set used to keep the while-1 loop alive until a struct.error.
        radials = [
            ({"radial_state": 1, "azimuth": 0.5 * i, "elevation": 0.5}, [])
            for i in range(5)
        ]
        with pytest.raises(PyARTDataError):
            C98DRadFile(io.BytesIO(_build_file(radials)))

    def test_truncated_radial_header_rejected(self):
        raw = _build_file(_wellformed_radials())
        with pytest.raises(PyARTDataError, match="truncated"):
            C98DRadFile(io.BytesIO(raw[:-30]))


class TestWellFormedFileUnchanged:
    """The bounds must not disturb a well-formed file."""

    def test_moment_bytes_decode_unchanged(self):
        rad = C98DRadFile(io.BytesIO(_build_file(_wellformed_radials())))
        assert rad.moment_data["dBZ"][0].tobytes() == _encode_16bit(DBZ_GATES)

    def test_get_data_values_unchanged(self):
        rad = C98DRadFile(io.BytesIO(_build_file(_wellformed_radials())))
        data = rad.get_data("dBZ")
        assert data.shape == (1, 4)
        assert (data == [[10.0, 11.0, 12.0, 13.0]]).all()

    def test_archive_read_unchanged(self):
        radar = c98dfile_archive(io.BytesIO(_build_file(_wellformed_radials())))
        assert radar.nrays == 1
        assert radar.ngates == 4
        assert radar.nsweeps == 1
        refl = radar.fields["reflectivity"]["data"]
        assert (refl == [[10.0, 11.0, 12.0, 13.0]]).all()
