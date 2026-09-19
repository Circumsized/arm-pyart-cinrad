"""
Security regression tests for the shared io input-validation layer (5.1).

The layer exists so that every reader enforces the same contract on
dimensions, cursors and decompression sizes derived from file content
*before* any allocation happens (the d2238d lesson: a 12 KB file must not
be able to request a 1e19-element array).
"""

import bz2

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io._validate import (  # noqa: E402
    MAX_DECOMPRESSED,
    MAX_DIM_PRODUCT,
    MAX_FILE_BYTES,
    MAX_MDV_NX,
    MAX_MDV_NY,
    MAX_MDV_NZ,
    MAX_NGATES,
    MAX_NRAYS,
    MAX_NSWEEPS,
    MAX_NVOLUME_ELEMS,
    MAX_STREAM_DECOMPRESSED,
    check_decompressed_size,
    decompress_bzip2_bounded,
    decompress_bzip2_records_bounded,
    validate_cursor,
    validate_dims,
    validate_mdv_dims,
    validate_volume_dims,
)


def test_validate_dims_accepts_legal_volume():
    # A realistic CINRAD volume is far below every limit.
    validate_dims(100, 360, 1000, max_elements=MAX_NVOLUME_ELEMS)


def test_validate_dims_rejects_zero_and_negative():
    with pytest.raises(PyARTDataError, match="positive"):
        validate_dims(0, 360)
    with pytest.raises(PyARTDataError, match="positive"):
        validate_dims(100, -1)


@pytest.mark.parametrize(
    "dims",
    [
        (MAX_NSWEEPS + 1, 10, 10),
        (10, MAX_NRAYS + 1, 10),
        (10, 10, MAX_NGATES + 1),
    ],
)
def test_validate_dims_enforces_per_dimension_limits(dims):
    with pytest.raises(PyARTDataError, match="exceeds the limit"):
        validate_dims(*dims, limits=(MAX_NSWEEPS, MAX_NRAYS, MAX_NGATES))


def test_validate_volume_dims_accepts_and_rejects():
    validate_volume_dims(10, 360, 1000)
    with pytest.raises(PyARTDataError, match="exceeds the limit"):
        validate_volume_dims(MAX_NSWEEPS + 1, 360, 1000)
    with pytest.raises(PyARTDataError, match="positive"):
        validate_volume_dims(10, 0, 1000)


def test_validate_mdv_dims_accepts_and_rejects():
    # A realistic MDV grid field is far below every limit.
    validate_mdv_dims(3661, 1837, 1)
    validate_mdv_dims(10, 10, MAX_MDV_NZ)
    with pytest.raises(PyARTDataError, match="exceeds the limit"):
        validate_mdv_dims(MAX_MDV_NX + 1, 10, 1)
    with pytest.raises(PyARTDataError, match="exceeds the limit"):
        validate_mdv_dims(10, MAX_MDV_NY + 1, 1)
    with pytest.raises(PyARTDataError, match="exceeds the limit"):
        # the format caps the number of vertical levels at 122
        validate_mdv_dims(10, 10, MAX_MDV_NZ + 1)
    with pytest.raises(PyARTDataError, match="positive"):
        validate_mdv_dims(0, 10, 1)
    with pytest.raises(PyARTDataError, match="positive"):
        validate_mdv_dims(10, 10, -1)
    # a field whose product exceeds the shared element cap is rejected even
    # though each axis is individually legal
    with pytest.raises(PyARTDataError, match="elements"):
        validate_mdv_dims(MAX_MDV_NX, MAX_MDV_NY, MAX_MDV_NZ)


def test_validate_dims_rejects_mismatched_limits():
    with pytest.raises(PyARTDataError, match="limits given"):
        validate_dims(10, 10, 10, limits=(100, 100))


def test_validate_dims_enforces_element_product_limit():
    # Each dimension is individually legal but the product is not.
    dims = (MAX_NRAYS, MAX_NGATES, 100)
    assert int(np.prod(dims)) > MAX_DIM_PRODUCT
    with pytest.raises(PyARTDataError, match="elements"):
        validate_dims(*dims)


def test_validate_dims_product_guard_before_multiplication():
    # The product guard must be evaluated without computing a product that
    # overflows a C integer (2**31) or a float.
    big = 2**31
    with pytest.raises(PyARTDataError):
        validate_dims(big, big, 1, max_elements=MAX_NVOLUME_ELEMS)


def test_validate_dims_promotes_numpy_scalars_before_multiplying():
    # FU-23: netCDF/HDF5 attributes arrive as numpy integer scalars. The
    # product must be accumulated in Python integers, because int32(8) *
    # int32(1e9) silently wraps and would let a bomb volume through.
    with pytest.raises(PyARTDataError, match="elements"):
        validate_dims(np.int32(8), np.int32(10**9))


def test_validate_dims_honors_explicit_limits():
    # Callers may pass tighter limits for a specific field/ray count.
    validate_dims(4, 10, max_elements=100)
    with pytest.raises(PyARTDataError):
        validate_dims(4, 100, max_elements=100)


def test_validate_cursor_accepts_in_range_values():
    buf = b"\x00" * 32
    validate_cursor(0, buf)
    validate_cursor(32, buf)
    validate_cursor(16, buf, consumed=16)


def test_validate_cursor_rejects_negative_and_overflow():
    buf = b"\x00" * 32
    with pytest.raises(PyARTDataError):
        validate_cursor(-1, buf)
    with pytest.raises(PyARTDataError):
        validate_cursor(33, buf)
    with pytest.raises(PyARTDataError):
        validate_cursor(17, buf, consumed=16)


def test_check_decompressed_size_within_limit():
    assert check_decompressed_size(0) == 0
    assert check_decompressed_size(MAX_DECOMPRESSED) == MAX_DECOMPRESSED


def test_check_decompressed_size_rejects_bomb():
    with pytest.raises(PyARTDataError, match="decompressed"):
        check_decompressed_size(MAX_DECOMPRESSED + 1)


def test_streaming_bzip2_is_bounded():
    # A single bz2 stream of 384 MiB of zeros compresses to a few hundred
    # bytes (bzip2 reaches ~23000:1 on zeros). Feeding the compressor in
    # 1 MiB chunks keeps the test's own build memory small; the bounded
    # decompressor must abort at MAX_DECOMPRESSED instead of materializing
    # the full expansion.
    compressor = bz2.BZ2Compressor()
    parts = []
    for _ in range(384):
        parts.append(compressor.compress(b"\x00" * (1 << 20)))
    parts.append(compressor.flush())
    payload = b"".join(parts)
    assert len(payload) < (1 << 16)
    with pytest.raises(PyARTDataError, match="decompressed"):
        decompress_bzip2_bounded(payload)


def test_streaming_bzip2_normal_file_roundtrip():
    raw = b"reflectivity" * 4096
    assert decompress_bzip2_bounded(bz2.compress(raw)) == raw


def test_streaming_bzip2_records_roundtrip():
    # a run of concatenated streams separated by 4 byte control words, as
    # found in Archive 2 files, must reassemble into one buffer
    streams = [bz2.compress(b"A" * 5000), bz2.compress(b"B" * 7000)]
    payload = streams[0] + b"\x00\x00\x00\x00" + streams[1]
    out = decompress_bzip2_records_bounded(payload, 0, 4)
    assert out == b"A" * 5000 + b"B" * 7000


def test_streaming_bzip2_records_cumulative_limit():
    streams = [bz2.compress(b"\x00" * (1 << 20)) for _ in range(3)]
    payload = b"".join(s + b"\x00\x00\x00\x00" for s in streams)
    with pytest.raises(PyARTDataError, match="decompressed"):
        decompress_bzip2_records_bounded(
            payload, 0, 4, limit=2 << 20, stream_limit=1 << 20
        )


def test_streaming_bzip2_records_stream_limit():
    payload = bz2.compress(b"\x00" * (2 << 20))
    with pytest.raises(PyARTDataError, match="stream"):
        decompress_bzip2_records_bounded(payload, 0, 4, stream_limit=1 << 20)


def test_streaming_bzip2_records_truncated_stream():
    payload = bz2.compress(b"reflectivity" * 4096)
    with pytest.raises(PyARTDataError, match="end-of-stream"):
        decompress_bzip2_records_bounded(payload[:20], 0, 4)


def test_streaming_bzip2_records_invalid_data():
    with pytest.raises(PyARTDataError, match="bz2 decompression failed"):
        decompress_bzip2_records_bounded(b"BZh9not-a-stream", 0, 4)


def test_limits_are_sane_constants():
    assert MAX_NGATES > 0 and MAX_NRAYS > 0 and MAX_NSWEEPS > 0
    assert MAX_NVOLUME_ELEMS <= MAX_DIM_PRODUCT
    assert MAX_DECOMPRESSED <= MAX_FILE_BYTES * 2
    assert 0 < MAX_STREAM_DECOMPRESSED < MAX_DECOMPRESSED
