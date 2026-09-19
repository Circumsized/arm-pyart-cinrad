"""
Security regression tests for the MDV field-header dimension validation (FU-15).

Covers the unbounded allocations driven by MDV headers
(b94672, CWE-789). ``MdvFile.read_a_field`` sized its output array straight
from the untrusted field header with ``np.zeros([nz, ny, nx])`` and
``_calc_geometry`` sized its geometry arrays straight from the master
header's ``max_nx/max_ny/max_nz``. A 69 KB sample file patched to declare
``nx=ny=nz=2**31-1`` reached NumPy's allocator and surfaced as a bare
``ValueError: array is too big``; ``nx=0`` was accepted silently and returned
a zero-gate field; and a truncated vlevel block made ``struct.unpack`` raise
an opaque ``struct.error``.

The fix routes every header-derived dimension through the shared
:py:mod:`pyart.io._validate` limits *before* any allocation, cross-checks the
field dimensions against the master header, and rejects uncompressed volumes
that cannot fit in the bytes left in the file. Every rejection is a
``PyARTDataError``. Well-formed files must decode byte-identically.
"""

import hashlib
import io
import os
import struct

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io import mdv_common, read_mdv  # noqa: E402
from pyart.io._validate import (  # noqa: E402
    MAX_MDV_NX,
    MAX_MDV_NY,
    MAX_MDV_NZ,
)
from pyart.io.mdv_grid import read_grid_mdv  # noqa: E402

DATA_PATH = "pyart/testing/data"
PPI_FILE = os.path.join(DATA_PATH, "example_mdv_ppi.mdv")
RHI_FILE = os.path.join(DATA_PATH, "example_mdv_rhi.mdv")
GRID_FILE = os.path.join(DATA_PATH, "example_mdv_grid.mdv")

# Byte offsets of the big-endian int32 header fields. The master header
# starts at 0; the first field header starts at field_hdr_offset, which is
# 1024 for every sample file (master header tuple indices 20/21/22 and field
# header tuple indices 9/10/11/13/27, four bytes per int32).
MASTER_MAX_NX_OFFSET = 80
MASTER_MAX_NY_OFFSET = 84
MASTER_MAX_NZ_OFFSET = 88
FIELD_HDR_OFFSET = 1024
FIELD_HEADER_SIZE = 416
VLEVEL_HEADER_SIZE = 1024
# master header + (field header + vlevel header) per field, no chunks;
# matches MdvFile._calc_file_offsets for nfields=1, nchunks=0.
FIELD_DATA_OFFSET = FIELD_HDR_OFFSET + FIELD_HEADER_SIZE + VLEVEL_HEADER_SIZE
NX_OFFSET = FIELD_HDR_OFFSET + 36
NY_OFFSET = FIELD_HDR_OFFSET + 40
NZ_OFFSET = FIELD_HDR_OFFSET + 44
ENCODING_OFFSET = FIELD_HDR_OFFSET + 52
COMPRESSION_OFFSET = FIELD_HDR_OFFSET + 108

# Pinned digests (first 16 hex digits of sha256 over the C-contiguous
# float32 field array) of the sample files, see
# test_real_samples_decode_unchanged.
PPI_DIGEST = "a9cd2ebf40f7e18e"
RHI_DIGEST = "179298d71435aacb"

INT32_MAX = 2**31 - 1


def _read_bytes(path):
    with open(path, "rb") as f:
        return bytearray(f.read())


def _patch_i32(buf, offset, value):
    struct.pack_into(">i", buf, offset, value)


def _digest(arr):
    return hashlib.sha256(np.ascontiguousarray(arr, dtype="float32")).hexdigest()[:16]


def _open(patched):
    return mdv_common.MdvFile(io.BytesIO(bytes(patched)))


def _build_uncompressed_mdv(nx, ny, nz, values, encoding=mdv_common.ENCODING_INT16):
    """Build a minimal single-field uncompressed MDV file.

    ``values`` is a list of ``nz`` (ny, nx) arrays of unsigned integers in the
    file's byte order. The layout follows the reader in
    ``MdvFile.read_a_field``: master header, field header, vlevel header, then
    per level the vlevel info block followed by the compression info and the
    raw plane.
    """
    itemsize = {
        mdv_common.ENCODING_INT8: 1,
        mdv_common.ENCODING_INT16: 2,
        mdv_common.ENCODING_FLOAT32: 4,
    }[encoding]
    dtype = {
        mdv_common.ENCODING_INT8: ">u1",
        mdv_common.ENCODING_INT16: ">u2",
        mdv_common.ENCODING_FLOAT32: ">f4",
    }[encoding]
    field_data_offset = FIELD_DATA_OFFSET

    master = [0] * 67
    master[0] = 1016
    master[1] = 14142
    master[19] = 1  # nfields
    master[20], master[21], master[22] = nx, ny, nz  # max_nx, max_ny, max_nz
    master[23] = 0  # nchunks
    master[24] = FIELD_HDR_OFFSET  # field_hdr_offset
    master[25] = FIELD_HDR_OFFSET + FIELD_HEADER_SIZE  # vlevel_hdr_offset
    master[26] = field_data_offset  # chunk_hdr_offset
    master[63] = b""
    master[64] = b""
    master[65] = b""
    master[66] = 1016
    master_bytes = struct.pack(mdv_common.MdvFile.master_header_fmt, *master)

    field = [0] * 77
    field[0] = 408
    field[1] = 14143
    field[9], field[10], field[11] = nx, ny, nz
    field[12] = mdv_common.PROJ_FLAT
    field[13] = encoding
    field[14] = itemsize  # data_element_nbytes
    field[15] = field_data_offset
    field[16] = 2 * 4 * nz + nz * (20 + nx * ny * itemsize)  # volume_size
    field[27] = mdv_common.COMPRESSION_NONE
    field[57] = 1.0  # scale
    field[58] = 0.0  # bias
    field[59] = 65535  # bad_data_value, absent from the test data
    field[71] = b"TESTFIELD"  # field_name_long
    field[72] = b"TEST"  # field_name
    field[73] = b""
    field[74] = b""
    field[75] = b""
    field[76] = 408
    field_bytes = struct.pack(mdv_common.MdvFile.field_header_fmt, *field)

    vlevel = [0] * 256
    vlevel[0] = 1016
    vlevel[1] = 14144
    vlevel[255] = 1016
    vlevel_bytes = struct.pack(mdv_common.MdvFile.vlevel_header_fmt, *vlevel)

    level_nbytes = 20 + nx * ny * itemsize
    info = struct.pack(f">{nz}I {nz}I", *([0] * nz), *([level_nbytes] * nz))
    body = b""
    for sw in range(nz):
        compr = struct.pack(
            mdv_common.MdvFile.compression_info_fmt,
            mdv_common.TA_NOT_COMPRESSED,
            nx * ny * itemsize,
            nx * ny * itemsize,
            nx * ny * itemsize,
            0,
            0,
        )
        body += compr + np.asarray(values[sw], dtype=dtype).tobytes()
    return master_bytes + field_bytes + vlevel_bytes + info + body


def _sample_values(nx, ny, nz):
    """Return nz deterministic (ny, nx) planes avoiding the bad data value."""
    return [
        (np.arange(1, nx * ny + 1, dtype=">u2") % 60000).reshape(ny, nx)
        for _ in range(nz)
    ]


class TestFieldHeaderDimensions:
    """Field header dimensions must be validated before any allocation."""

    def test_overflow_dims_rejected(self):
        # nx=ny=nz=2**31-1 previously reached the allocator and raised a bare
        # ValueError ("array is too big") from NumPy.
        raw = _read_bytes(PPI_FILE)
        for offset in (NX_OFFSET, NY_OFFSET, NZ_OFFSET):
            _patch_i32(raw, offset, INT32_MAX)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="exceeds the limit"):
            mdv.read_a_field(0)

    def test_overflow_dims_with_lying_master_header_rejected(self):
        # The master header is untrusted too: even when it claims to allow
        # 2**31-1 points per axis the absolute limits must reject the field.
        raw = _read_bytes(PPI_FILE)
        for offset in (NX_OFFSET, NY_OFFSET, NZ_OFFSET):
            _patch_i32(raw, offset, INT32_MAX)
        for offset in (
            MASTER_MAX_NX_OFFSET,
            MASTER_MAX_NY_OFFSET,
            MASTER_MAX_NZ_OFFSET,
        ):
            _patch_i32(raw, offset, INT32_MAX)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="exceeds the limit"):
            mdv.read_a_field(0)

    def test_field_larger_than_master_max_rejected(self):
        # nx=200 exceeds the master header's max_nx=110; previously this
        # allocated the array and then died in reshape() with a ValueError.
        raw = _read_bytes(PPI_FILE)
        _patch_i32(raw, NX_OFFSET, 200)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="master header"):
            mdv.read_a_field(0)

    def test_zero_dimension_rejected(self):
        # nx=0 previously returned a silent (1, 360, 0) field.
        raw = _read_bytes(PPI_FILE)
        _patch_i32(raw, NX_OFFSET, 0)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="positive"):
            mdv.read_a_field(0)

    def test_negative_dimension_rejected(self):
        raw = _read_bytes(PPI_FILE)
        _patch_i32(raw, NY_OFFSET, -5)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="positive"):
            mdv.read_a_field(0)

    @pytest.mark.parametrize(
        "offset, master_offset, value",
        [
            (NX_OFFSET, MASTER_MAX_NX_OFFSET, MAX_MDV_NX + 1),
            (NY_OFFSET, MASTER_MAX_NY_OFFSET, MAX_MDV_NY + 1),
            (NZ_OFFSET, MASTER_MAX_NZ_OFFSET, MAX_MDV_NZ + 1),
        ],
    )
    def test_dims_just_over_limit_rejected(self, offset, master_offset, value):
        # the master header is patched to allow the value, so only the
        # absolute per-axis limit can reject the field
        raw = _read_bytes(PPI_FILE)
        _patch_i32(raw, offset, value)
        _patch_i32(raw, master_offset, value)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="exceeds the limit"):
            mdv.read_a_field(0)

    def test_volume_product_over_limit_rejected(self):
        # Each axis is within its own limit but the product exceeds the
        # shared volume element cap. The file itself is tiny; only the
        # headers lie.
        nx, ny, nz = 20000, 20000, 10
        raw = bytearray(_build_uncompressed_mdv(2, 2, 1, _sample_values(2, 2, 1)))
        _patch_i32(raw, NX_OFFSET, nx)
        _patch_i32(raw, NY_OFFSET, ny)
        _patch_i32(raw, NZ_OFFSET, nz)
        _patch_i32(raw, MASTER_MAX_NX_OFFSET, nx)
        _patch_i32(raw, MASTER_MAX_NY_OFFSET, ny)
        _patch_i32(raw, MASTER_MAX_NZ_OFFSET, nz)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="elements"):
            mdv.read_a_field(0)


class TestUncompressedVolumeBytes:
    """Uncompressed field data must fit in the bytes left in the file."""

    def test_volume_larger_than_file_rejected(self):
        nx, ny, nz = 64, 48, 2
        raw = bytearray(_build_uncompressed_mdv(nx, ny, nz, _sample_values(nx, ny, nz)))
        file_size = len(raw)
        # claim ten levels while only two are present in the file
        _patch_i32(raw, NZ_OFFSET, 10)
        _patch_i32(raw, MASTER_MAX_NZ_OFFSET, 10)
        needed = nx * ny * 10 * 2
        assert needed > file_size - FIELD_DATA_OFFSET
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="remain in the file"):
            mdv.read_a_field(0)

    def test_data_offset_past_end_rejected(self):
        nx, ny, nz = 8, 6, 1
        raw = bytearray(_build_uncompressed_mdv(nx, ny, nz, _sample_values(nx, ny, nz)))
        _patch_i32(raw, FIELD_HDR_OFFSET + 60, len(raw) + 4096)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="remain in the file"):
            mdv.read_a_field(0)

    def test_fitting_uncompressed_volume_accepted(self):
        # The byte check must not reject a well-formed uncompressed field.
        nx, ny, nz = 7, 5, 2
        values = _sample_values(nx, ny, nz)
        raw = _build_uncompressed_mdv(nx, ny, nz, values)
        mdv = _open(raw)
        data = mdv.read_a_field(0)
        assert data.shape == (nz, ny, nx)
        np.testing.assert_array_equal(data, np.array(values, dtype="float32"))

    def test_compressed_field_not_byte_checked(self):
        # The sample files are gzip compressed: their on-disk size (65 KB) is
        # smaller than the volume they expand to (79 KB), so the byte check
        # must not apply to compressed fields or these files would be
        # rejected before the decompressor ever runs.
        raw = _read_bytes(PPI_FILE)
        uncompressed = 110 * 360 * 1 * 2
        remaining = len(raw) - 4000  # field_data_offset
        assert uncompressed > remaining
        mdv = _open(raw)
        data = mdv.read_a_field(0)
        assert data.shape == (1, 360, 110)


class TestLevelsInfo:
    """The vlevel struct parse must reject truncated buffers."""

    def test_truncated_levels_info_rejected(self):
        raw = _read_bytes(PPI_FILE)
        mdv = _open(raw)
        # park the cursor four bytes before the end of the file and ask for
        # 100 levels: the read comes back short and struct.unpack used to
        # raise a bare struct.error.
        mdv.fileptr.seek(len(raw) - 4)
        with pytest.raises(PyARTDataError, match="truncated"):
            mdv._get_levels_info(100)

    def test_complete_levels_info_accepted(self):
        nx, ny, nz = 4, 3, 2
        raw = _build_uncompressed_mdv(nx, ny, nz, _sample_values(nx, ny, nz))
        mdv = _open(raw)
        mdv.fileptr.seek(FIELD_DATA_OFFSET)
        d = mdv._get_levels_info(nz)
        assert len(d["vlevel_offsets"]) == nz
        assert len(d["vlevel_nbytes"]) == nz


class TestMasterHeaderGeometry:
    """_calc_geometry must validate the master header dimensions too."""

    def test_overflow_master_dims_rejected(self):
        raw = _read_bytes(PPI_FILE)
        for offset in (
            MASTER_MAX_NX_OFFSET,
            MASTER_MAX_NY_OFFSET,
            MASTER_MAX_NZ_OFFSET,
        ):
            _patch_i32(raw, offset, INT32_MAX)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="exceeds the limit"):
            mdv._calc_geometry()

    def test_real_master_dims_accepted(self):
        raw = _read_bytes(PPI_FILE)
        mdv = _open(raw)
        az_deg, range_km, el_deg = mdv._calc_geometry()
        assert len(range_km) == 110  # max_nx of the sample file
        assert len(az_deg) == 360  # max_ny of the sample file
        assert len(el_deg) == 1  # max_nz of the sample file


class TestRealSamplesDecodeUnchanged:
    """The fix must not change decoding of well-formed files."""

    def test_read_a_field_digests(self):
        for path, expected in ((PPI_FILE, PPI_DIGEST), (RHI_FILE, RHI_DIGEST)):
            raw = _read_bytes(path)
            mdv = _open(raw)
            data = mdv.read_a_field(0)
            assert data.shape[0] == 1  # nz
            assert _digest(data) == expected

    def test_read_mdv_end_to_end(self):
        radar = read_mdv(PPI_FILE)
        data = radar.fields["reflectivity"]["data"]
        assert data.shape == (360, 110)
        assert _digest(data.filled(np.nan).astype("float32")) == PPI_DIGEST

    def test_read_grid_mdv_unchanged(self):
        # the grid sample's field name has no default mapping, so no field
        # data is decoded; this pins the pre-existing behaviour.
        grid = read_grid_mdv(GRID_FILE)
        assert list(grid.fields) == []

    def test_grid_sample_field_decode_unchanged(self):
        # reading the grid sample's field directly still fails inside the
        # RLE8 decoder, exactly as it did before the dimension checks.
        raw = _read_bytes(GRID_FILE)
        mdv = _open(raw)
        with pytest.raises(PyARTDataError, match="decoded to"):
            mdv.read_a_field(0)
