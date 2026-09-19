"""
Security regression tests for the NEXRAD Level 3 symbology readers (FU-16/FU-17).

Covers the unbounded allocations and out-of-bounds reads in
``pyart.io.nexrad_level3.NEXRADLevel3File``:

* ``_read_symbology_block`` (packet 16 / AF1F, 18f97e) sized ``raw_data``
  straight from the packet header's ``nradials``/``nbins`` (both signed
  16-bit header fields) and sliced each radial out of the symbology block
  without checking that the bytes were there. A 23 KB sample patched to
  declare 32767 radials allocated a 7.5 MB array and then died in
  ``struct.unpack``; a patched bin count surfaced as a NumPy broadcast
  ``ValueError``; and a negative ``nbytes`` walked the cursor backwards.

* ``_read_symbology_block_28`` (packet 28, 8227a7) sliced the XDR payload
  with the header's ``num_bytes`` without bounds, indexed ``radials[0]``
  without checking that any radial was parsed, and let ``EOFError`` /
  ``ValueError`` escape from the XDR parser.

Every rejection must be a ``PyARTDataError``; the three sample products
must decode byte-identically.
"""

import bz2
import hashlib
import io
import os
import struct

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io import nexrad_level3 as l3  # noqa: E402
from pyart.io import read_nexrad_level3  # noqa: E402
from pyart.io._validate import MAX_NGATES, MAX_NRAYS  # noqa: E402

DATA_PATH = "pyart/testing/data"
# packet AF1F (run length encoded radials), symbology stored uncompressed
MSG19 = os.path.join(DATA_PATH, "example_nexrad_level3_msg19")
# packet 16 (raw radial data array), symbology stored bz2 compressed
MSG163 = os.path.join(DATA_PATH, "example_nexrad_level3_msg163")
# packet 28 (XDR generic format), symbology stored bz2 compressed
MSG176 = os.path.join(DATA_PATH, "example_nexrad_level3_msg176")

# Offsets inside the (uncompressed) msg19 symbology block, which starts
# right after the 30 byte text header, the 18 byte message header and the
# 102 byte product description (no record padding in this file).
SYM_OFFSET = 150
PACKET_CODE_OFFSET = SYM_OFFSET + 16  # RADIAL_PACKET_HEADER.packet_code
NBINS_OFFSET = SYM_OFFSET + 20  # RADIAL_PACKET_HEADER.nbins
NRADIALS_OFFSET = SYM_OFFSET + 28  # RADIAL_PACKET_HEADER.nradials
RADIAL0_NBYTES_OFFSET = SYM_OFFSET + 30  # RADIAL_HEADER.nbytes

PACKET_16 = 16
INT16_MAX = 2**15 - 1
INT32_MAX = 2**31 - 1

# Pinned digests (first 16 hex digits of sha256) of the sample products.
MSG19_RAW_DIGEST = "386629bf42112476"
MSG163_RAW_DIGEST = "64273aebb88c93b1"
MSG176_RAW_DIGEST = "66bfe5ea5b5a672b"
MSG19_FIELD_DIGEST = "03b3465a48e41544"
MSG163_FIELD_DIGEST = "fbcd9f813cfbbb90"
MSG176_FIELD_DIGEST = "8549705e9bc98e38"


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _digest(arr):
    return hashlib.sha256(np.ascontiguousarray(arr)).hexdigest()[:16]


def _patch_i16(buf, offset, value):
    struct.pack_into(">h", buf, offset, value)


def _patch_i32(buf, offset, value):
    struct.pack_into(">i", buf, offset, value)


def _open(raw):
    return l3.NEXRADLevel3File(io.BytesIO(bytes(raw)))


def _msg176_uncompressed():
    """Rebuild the packet 28 sample around its decompressed symbology block.

    The sample stores the symbology block as a bz2 stream, which cannot be
    patched byte by byte. Reassembling the file with the already expanded
    block yields an equivalent product whose XDR payload can be edited
    directly; ``test_synthetic_msg176_matches_real`` proves the assembly
    decodes identically to the original.
    """
    raw = _read_bytes(MSG176)
    bpos = 30 + raw.find(b"SDUS") + 18 + 102
    assert raw[bpos : bpos + 2] == b"BZ"
    return bytearray(raw[:bpos] + bz2.decompress(raw[bpos:]))


def _msg176_xdr_offsets(buf):
    """Return the byte offsets (into the assembled file) of the XDR fields
    patched by the tests.

    The offsets are located by parsing the real payload, so the tests fail
    loudly if the layout of the sample file ever changes.
    """
    sym_start = 30 + buf.find(b"SDUS") + 18 + 102
    hunk_start = sym_start + 24  # symbology header (16) + packet header (8)
    num_bytes = struct.unpack_from(">i", buf, sym_start + 20)[0]
    hunk = buf[hunk_start : hunk_start + num_bytes]

    parser = l3.Level3XDRParser(hunk)
    parser.unpack_string()  # name
    parser.unpack_string()  # description
    parser.unpack_int()  # code
    parser.unpack_int()  # type
    parser.unpack_uint()  # prod_time
    parser.unpack_string()  # radar_name
    for _ in range(3):
        parser.unpack_float()  # latitude, longitude, height
    for _ in range(2):
        parser.unpack_uint()  # vol_time, el_time
    parser.unpack_float()  # el_angle
    for _ in range(6):
        parser.unpack_int()  # vol_num, op_mode, vcp_num, el_num, ...
    parser._unpack_parameters()
    components_num = parser._pos
    parser.unpack_int()  # components count
    parser.unpack_int()  # components pointer
    parser.unpack_int()  # component code
    parser.unpack_string()  # radial description
    parser.unpack_float()  # gate_width
    parser.unpack_float()  # first_gate
    parser._unpack_parameters()
    num_rads = parser._pos
    parser.unpack_int()  # number of radials
    num_bins = parser._pos + 12  # azimuth, elevation, width floats
    return {
        "num_bytes": sym_start + 20,
        "components_num": hunk_start + components_num,
        "num_rads": hunk_start + num_rads,
        "num_bins": hunk_start + num_bins,
    }


class TestRadialPacketDimensions:
    """Packet 16 / AF1F dimensions must be validated before allocating."""

    def test_huge_nradials_rejected(self):
        # nradials=32767 used to allocate a 7.5 MB array and then walk off
        # the end of the symbology block into struct.error. The signed
        # 16-bit header can express a value above MAX_NRAYS, which is what
        # makes this reachable.
        assert INT16_MAX > MAX_NRAYS
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, NRADIALS_OFFSET, INT16_MAX)
        with pytest.raises(PyARTDataError, match="exceeds the limit"):
            _open(raw)

    def test_negative_nradials_rejected(self):
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, NRADIALS_OFFSET, -1)
        with pytest.raises(PyARTDataError, match="positive"):
            _open(raw)

    def test_zero_nradials_rejected(self):
        # nradials=0 used to return an empty (0, 230) raw_data array.
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, NRADIALS_OFFSET, 0)
        with pytest.raises(PyARTDataError, match="positive"):
            _open(raw)

    def test_volume_product_over_limit_rejected(self):
        # the bin count of a packet 16 product comes from the first radial
        # header; 360x32767 elements is within the per-axis limits but the
        # shared element cap must still reject it before np.empty runs
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, PACKET_CODE_OFFSET, PACKET_16)
        _patch_i16(raw, RADIAL0_NBYTES_OFFSET, INT16_MAX)
        _patch_i16(raw, NRADIALS_OFFSET, 9000)
        with pytest.raises(PyARTDataError, match="elements"):
            _open(raw)


class TestRadialPacketBounds:
    """Every radial slice must fit inside the symbology block."""

    def test_packet16_bins_past_buffer_rejected(self):
        # the bin count comes from the first radial header (packet 16 uses
        # it when the two header fields disagree). A signed 16-bit field can
        # never exceed MAX_NGATES, so it is the buffer bounds check, not the
        # per-axis limit, that rejects a bogus bin count.
        assert INT16_MAX < MAX_NGATES
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, PACKET_CODE_OFFSET, PACKET_16)
        _patch_i16(raw, RADIAL0_NBYTES_OFFSET, INT16_MAX)
        with pytest.raises(PyARTDataError, match="remain in the symbology"):
            _open(raw)

    def test_rle_run_past_buffer_rejected(self):
        # rle_size = nbytes * 2 = 65534 bytes, far past the 23 KB block
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, RADIAL0_NBYTES_OFFSET, INT16_MAX)
        with pytest.raises(PyARTDataError, match="remain in the symbology"):
            _open(raw)

    def test_negative_radial_nbytes_rejected(self):
        # a negative nbytes used to move the cursor backwards and silently
        # mis-parse the remaining radials
        raw = bytearray(_read_bytes(MSG19))
        _patch_i16(raw, RADIAL0_NBYTES_OFFSET, -100)
        with pytest.raises(PyARTDataError, match="negative"):
            _open(raw)

    def test_truncated_symbology_rejected(self):
        raw = bytearray(_read_bytes(MSG19))
        del raw[SYM_OFFSET + 2000 :]  # cut inside the radial data
        with pytest.raises(PyARTDataError, match="symbology block"):
            _open(raw)

    def test_symbology_too_short_for_headers_rejected(self):
        raw = bytearray(_read_bytes(MSG19))
        del raw[SYM_OFFSET + 20 :]  # not even a full radial packet header
        with pytest.raises(PyARTDataError, match="too short"):
            _open(raw)


class TestPacket28:
    """Packet 28 (product 176) XDR parsing must be bounded."""

    def test_num_bytes_past_buffer_rejected(self):
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        _patch_i32(raw, offsets["num_bytes"], INT32_MAX)
        with pytest.raises(PyARTDataError, match="remain in the symbology"):
            _open(raw)

    def test_negative_num_bytes_rejected(self):
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        _patch_i32(raw, offsets["num_bytes"], -1)
        with pytest.raises(PyARTDataError, match="remain in the symbology"):
            _open(raw)

    def test_empty_components_rejected(self):
        # a 176 product with zero components used to blow up on
        # ``components.radials`` (AttributeError); the hunk is trimmed so
        # the XDR parse itself succeeds and the empty component list is
        # what gets rejected
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        assert struct.unpack_from(">i", raw, offsets["components_num"])[0] == 1
        _patch_i32(raw, offsets["components_num"], 0)
        # trim the hunk so it ends right after the (now zero) component
        # count and its pointer field: the parse itself must succeed for
        # the empty check to fire
        _patch_i32(
            raw,
            offsets["num_bytes"],
            offsets["components_num"] - offsets["num_bytes"] + 4,
        )
        with pytest.raises(PyARTDataError, match="no radial"):
            _open(raw)

    def test_zero_radials_rejected(self):
        # an empty radial list used to raise IndexError on radials[0]
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        assert struct.unpack_from(">i", raw, offsets["num_rads"])[0] == 360
        _patch_i32(raw, offsets["num_rads"], 0)
        # trim the hunk so it ends right after the (now zero) radial count
        _patch_i32(
            raw, offsets["num_bytes"], offsets["num_rads"] - offsets["num_bytes"]
        )
        with pytest.raises(PyARTDataError, match="no radial"):
            _open(raw)

    def test_huge_num_bins_rejected(self):
        # nbins comes from the XDR stream; 2**31-1 used to request a
        # 1.5 TB uint16 allocation
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        assert struct.unpack_from(">i", raw, offsets["num_bins"])[0] == 920
        _patch_i32(raw, offsets["num_bins"], INT32_MAX)
        with pytest.raises(PyARTDataError, match="exceeds the limit"):
            _open(raw)

    def test_truncated_xdr_hunk_rejected(self):
        # a hunk that ends mid-stream used to leak EOFError from the parser
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        num_bytes = struct.unpack_from(">i", raw, offsets["num_bytes"])[0]
        _patch_i32(raw, offsets["num_bytes"], num_bytes - 1000)
        with pytest.raises(PyARTDataError, match="malformed"):
            _open(raw)

    def test_radial_length_mismatch_rejected(self):
        # the first radial declares the bin count; a shorter later radial
        # used to surface as a NumPy broadcast ValueError
        raw = _msg176_uncompressed()
        offsets = _msg176_xdr_offsets(raw)
        # shrink the data array of the first radial by one integer
        num_bins = struct.unpack_from(">i", raw, offsets["num_bins"])[0]
        _patch_i32(raw, offsets["num_bins"], num_bins - 1)
        with pytest.raises(PyARTDataError, match="gates"):
            _open(raw)


class TestXDRParserContract:
    """The XDR unpacker must reject bogus array lengths immediately."""

    def test_unpack_array_rejects_bogus_length(self):
        buf = struct.pack(">I", 2**32 - 1) + b"\x00" * 64
        parser = l3.Level3XDRParser(buf)
        with pytest.raises(EOFError):
            parser.unpack_array(parser.unpack_int)

    def test_unpack_array_accepts_legal_length(self):
        buf = struct.pack(">I", 2) + struct.pack(">ii", 7, 9)
        parser = l3.Level3XDRParser(buf)
        assert parser.unpack_array(parser.unpack_int) == [7, 9]


class TestRealProductsDecodeUnchanged:
    """The fix must not change decoding of well-formed products."""

    def test_msg19_raw_data(self):
        nfile = _open(_read_bytes(MSG19))
        assert nfile.raw_data.shape == (360, 230)
        assert _digest(nfile.raw_data) == MSG19_RAW_DIGEST

    def test_msg163_raw_data(self):
        nfile = _open(_read_bytes(MSG163))
        assert nfile.raw_data.shape == (360, 1200)
        assert _digest(nfile.raw_data) == MSG163_RAW_DIGEST

    def test_msg176_raw_data(self):
        nfile = _open(_read_bytes(MSG176))
        assert nfile.raw_data.shape == (360, 920)
        assert _digest(nfile.raw_data) == MSG176_RAW_DIGEST

    def test_synthetic_msg176_matches_real(self):
        # proves the uncompressed reassembly used by the patch tests is
        # equivalent to the original bz2 file
        nfile = _open(_msg176_uncompressed())
        assert nfile.raw_data.shape == (360, 920)
        assert _digest(nfile.raw_data) == MSG176_RAW_DIGEST

    @pytest.mark.parametrize(
        "path, field, digest",
        [
            (MSG19, "reflectivity", MSG19_FIELD_DIGEST),
            (MSG163, "specific_differential_phase", MSG163_FIELD_DIGEST),
            (MSG176, "radar_estimated_rain_rate", MSG176_FIELD_DIGEST),
        ],
    )
    def test_read_nexrad_level3_end_to_end(self, path, field, digest):
        radar = read_nexrad_level3(path)
        data = radar.fields[field]["data"]
        assert _digest(data.filled(np.nan).astype("float32")) == digest
