"""
Security regression tests for the NEXRAD bz2 decompression bomb (FU-18).

Covers the unbounded ``bz2.decompress`` calls in the NEXRAD readers
(f56fc4, CWE-409/400). bzip2 compresses runs of zeros by a factor of
~10^6, so a few hundred bytes of payload expand to hundreds of megabytes:

* ``nexrad_level2._decompress_records`` expanded every record stream of an
  Archive 2 file with a single ``decompressor.decompress()`` call and no
  ceiling on the result.
* ``nexrad_level3.NEXRADLevel3File`` expanded the whole symbology block
  with ``bz2.decompress``.
* ``mdv_common.MdvFile.read_a_field`` expanded each level with
  ``bz2.decompress``.

The fix expands the streams in chunks with a hard ceiling on the output
(:py:data:`pyart.io._validate.MAX_DECOMPRESSED`, plus a per-stream ceiling
for the multi-stream Archive 2 case) and wraps every bz2 failure in
``PyARTDataError``.
"""

import bz2
import hashlib
import io
import struct

import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io import mdv_common, nexrad_level2, nexrad_level3  # noqa: E402
from pyart.io._validate import MAX_DECOMPRESSED, MAX_STREAM_DECOMPRESSED  # noqa: E402

L2_FILE = "pyart/testing/data/example_nexrad_archive_msg31_compressed.ar2v"
# Level 3 products with a bz2 compressed symbology block
L3_FILE = "pyart/testing/data/example_nexrad_level3_msg163"
MDV_FILE = "pyart/testing/data/example_mdv_ppi.mdv"

# The Archive 2 layout: 24 byte volume header, then a 4 byte control word,
# then the first bz2 stream (the 12 byte "compression record" overlaps the
# stream, its bytes 4-6 are the "BZ" magic).
L2_STREAM_OFFSET = (
    nexrad_level2._structure_size(nexrad_level2.VOLUME_HEADER)
    + nexrad_level2.CONTROL_WORD_SIZE
)

# Pinned digests of the sample files' decompressed data.
L2_BUFFER_DIGEST = "73239c50912dbae6"
L3_SYMBOLOGY_DIGEST = "523eaf778bf97423"


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _digest(data):
    return hashlib.sha256(data).hexdigest()[:16]


def _bomb_payload(mib):
    """Return a bz2 stream expanding to ``mib`` MiB of zeros.

    Fed to the compressor in 1 MiB chunks so the test itself never holds
    more than a few megabytes; the payload is a few hundred bytes.
    """
    compressor = bz2.BZ2Compressor()
    parts = []
    for _ in range(mib):
        parts.append(compressor.compress(b"\x00" * (1 << 20)))
    parts.append(compressor.flush())
    return b"".join(parts)


def _multi_stream_bomb(stream_mib, count):
    """Return an Archive 2 payload of ``count`` concatenated bomb streams.

    Every stream stays below the per-stream ceiling while their sum
    exceeds the cumulative one; the 4 byte control word that separates
    consecutive streams is included.
    """
    parts = []
    for _ in range(count):
        parts.append(_bomb_payload(stream_mib))
        parts.append(b"\x00\x00\x00\x00")  # control word
    return b"".join(parts)


def _l2_with_payload(payload):
    """Assemble an Archive 2 file whose compressed section is ``payload``."""
    return _read_bytes(L2_FILE)[:L2_STREAM_OFFSET] + payload


def _l3_with_payload(payload):
    """Assemble a Level 3 file whose bz2 symbology block is ``payload``."""
    raw = bytearray(_read_bytes(L3_FILE))
    bpos = 30 + raw.find(b"SDUS") + 18 + 102
    assert raw[bpos : bpos + 2] == b"BZ"
    return bytes(raw[:bpos]) + payload


def _mdv_with_bzip2_level(payload):
    """Assemble an MDV file whose first level is a bz2 payload.

    The compression info block of the first level lives right after the
    vlevel info block at the field data offset. It is overwritten in place
    together with the head of the level data, so the file length and every
    later offset (the chunk data lives past the field data) are preserved.
    """
    raw = bytearray(_read_bytes(MDV_FILE))
    field_data_offset = struct.unpack_from(">i", raw, 1024 + 60)[0]
    nz = struct.unpack_from(">i", raw, 1024 + 44)[0]
    compr_offset = field_data_offset + 2 * 4 * nz
    compr = struct.pack(
        mdv_common.MdvFile.compression_info_fmt,
        mdv_common.BZIP_COMPRESSED,
        len(payload) * 1000,  # nbytes_uncompressed (unused for bzip2)
        len(payload),  # nbytes_compressed
        len(payload),  # nbytes_coded, the number of bytes to read
        0,
        0,
    )
    end = compr_offset + len(compr) + len(payload)
    return bytes(raw[:compr_offset]) + compr + payload + bytes(raw[end:])


class TestLevel2Decompression:
    """Archive 2 record streams must be expanded with a hard ceiling."""

    def test_decompression_bomb_rejected(self):
        # five streams of 60 MiB each stay below the per-stream ceiling but
        # their sum exceeds the cumulative one; a few hundred bytes of
        # payload must abort at MAX_DECOMPRESSED
        crafted = _l2_with_payload(_multi_stream_bomb(60, 5))
        assert len(crafted) < (1 << 16)
        with pytest.raises(PyARTDataError, match="decompressed"):
            nexrad_level2._decompress_records(io.BytesIO(crafted))

    def test_decompression_bomb_rejected_end_to_end(self):
        crafted = _l2_with_payload(_multi_stream_bomb(60, 5))
        with pytest.raises(PyARTDataError, match="decompressed"):
            nexrad_level2.NEXRADLevel2File(io.BytesIO(crafted))

    def test_single_stream_over_stream_limit_rejected(self):
        # 80 MiB is below the cumulative cap but above the per-stream cap
        crafted = _l2_with_payload(_bomb_payload(80))
        with pytest.raises(PyARTDataError, match="stream"):
            nexrad_level2._decompress_records(io.BytesIO(crafted))

    def test_truncated_stream_rejected(self):
        payload = bz2.compress(b"\x00" * 100000)
        crafted = _l2_with_payload(payload[: len(payload) // 2])
        with pytest.raises(PyARTDataError):
            nexrad_level2._decompress_records(io.BytesIO(crafted))

    def test_malformed_stream_rejected(self):
        crafted = _l2_with_payload(b"BZh9" + b"\x00" * 64)
        with pytest.raises(PyARTDataError, match="bz2 decompression failed"):
            nexrad_level2._decompress_records(io.BytesIO(crafted))

    def test_real_file_decodes_unchanged(self):
        with open(L2_FILE, "rb") as fh:
            buf = nexrad_level2._decompress_records(fh)
        assert len(buf) == 1151956
        assert _digest(buf) == L2_BUFFER_DIGEST

    def test_real_file_end_to_end(self):
        nfile = nexrad_level2.NEXRADLevel2File(L2_FILE)
        assert len(nfile.radial_records) == 120
        assert nfile.nscans == 1


class TestLevel3Decompression:
    """The Level 3 symbology block must be expanded with a ceiling."""

    def test_decompression_bomb_rejected(self):
        crafted = _l3_with_payload(_bomb_payload(384))
        assert len(crafted) < (1 << 20)
        with pytest.raises(PyARTDataError, match="decompressed"):
            nexrad_level3.NEXRADLevel3File(io.BytesIO(crafted))

    def test_real_file_decodes_unchanged(self):
        raw = _read_bytes(L3_FILE)
        bpos = 30 + raw.find(b"SDUS") + 18 + 102
        buf2 = bz2.decompress(raw[bpos:])
        assert _digest(buf2) == L3_SYMBOLOGY_DIGEST


class TestMdvDecompression:
    """MDV bzip2 levels must be expanded with a ceiling."""

    def test_decompression_bomb_rejected(self):
        crafted = bytearray(_mdv_with_bzip2_level(_bomb_payload(384)))
        assert len(crafted) < (1 << 20)
        mdv = mdv_common.MdvFile(io.BytesIO(bytes(crafted)))
        with pytest.raises(PyARTDataError, match="decompressed"):
            mdv.read_a_field(0)

    def test_truncated_level_rejected(self):
        payload = bz2.compress(b"\x00" * 100000)
        crafted = _mdv_with_bzip2_level(payload[: len(payload) // 2])
        mdv = mdv_common.MdvFile(io.BytesIO(crafted))
        with pytest.raises(PyARTDataError):
            mdv.read_a_field(0)


class TestLimits:
    """The ceilings must be documented constants."""

    def test_stream_limit_below_cumulative_limit(self):
        assert 0 < MAX_STREAM_DECOMPRESSED < MAX_DECOMPRESSED
