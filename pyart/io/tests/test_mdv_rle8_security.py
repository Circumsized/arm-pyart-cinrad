"""
Security regression tests for the MDV RLE8 decoder (FU-13).

Covers the out-of-bounds read/write in ``pyart.io.mdv_common._decode_rle8``
(d7c300, CWE-125/131). The decoder reads ``data[data_ptr + 1]`` and
``data[data_ptr + 2]`` whenever it encounters the escape key, so a stream
whose final byte (or final two bytes) are the key indexes past the end of
the buffer. The pre-allocated output buffer also silently clips runs that
would overflow it. Every malformed stream must now raise
``PyARTDataError`` instead, while well-formed streams decode byte-for-byte
identically.
"""

import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io.mdv_common import _decode_rle8  # noqa: E402

KEY = 0xFE  # arbitrary escape key, distinct from the payload values


def _encode_rle8(raw, key=KEY):
    """Minimal encoder matching the MDV RLE8 format (section 7)."""
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == key:
            j = i
            while j < len(raw) and raw[j] == key:
                j += 1
            run = j - i
            while run > 0:
                n = min(run, 255)
                out += bytes((key, n, key))
                run -= n
            i = j
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def test_decode_rle8_roundtrip_matches_encoder():
    raw = bytes([1, 2, 3, KEY, KEY, KEY, 7, 0, KEY, 9])
    encoded = _encode_rle8(raw)
    assert _decode_rle8(encoded, KEY, len(raw)) == raw


def test_decode_rle8_long_run_of_key_roundtrip():
    raw = bytes([KEY]) * 600 + bytes([4, 5])
    encoded = _encode_rle8(raw)
    assert len(encoded) < len(raw)  # sanity: actually compressed
    assert _decode_rle8(encoded, KEY, len(raw)) == raw


def test_decode_rle8_stream_ending_with_key_raises():
    # The last byte is the escape key: the old decoder read
    # data[data_ptr + 1] and data[data_ptr + 2] past the buffer.
    stream = bytes([1, 2, 3, KEY])
    with pytest.raises(PyARTDataError, match="run header"):
        _decode_rle8(stream, KEY, 3)


def test_decode_rle8_stream_with_truncated_run_header_raises():
    # Escape key followed by a count but no value byte.
    stream = bytes([KEY, 4])
    with pytest.raises(PyARTDataError, match="run header"):
        _decode_rle8(stream, KEY, 4)


def test_decode_rle8_output_shorter_than_header_raises():
    # The stream decodes to 2 bytes but the header declares 16: the old
    # decoder returned uninitialized tail bytes from np.empty.
    stream = bytes([1, 2])
    with pytest.raises(PyARTDataError, match="decoded to"):
        _decode_rle8(stream, KEY, 16)


def test_decode_rle8_output_longer_than_header_raises():
    # The stream decodes to 16 bytes but the header declares 2: the old
    # decoder silently clipped the run into the too-small buffer.
    stream = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16])
    with pytest.raises(PyARTDataError, match="decoded to"):
        _decode_rle8(stream, KEY, 2)


def test_decode_rle8_empty_stream_requires_zero_size():
    # A non-empty declared size with an empty stream is a mismatch.
    with pytest.raises(PyARTDataError, match="decoded to"):
        _decode_rle8(b"", KEY, 5)
    # An empty stream with a declared size of zero is consistent.
    assert _decode_rle8(b"", KEY, 0) == b""
