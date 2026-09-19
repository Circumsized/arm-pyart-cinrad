"""
Security regression tests for the Sigmet ray run-length decoder (FU-09).

Covers the out-of-bounds access in ``pyart.io._sigmetfile.SigmetFile._get_ray``
(7b4e3f, CWE-125/787). A negative 16-bit compression word means "the next
``words = code + 32768`` words are copied verbatim", and ``words`` can be as
large as 32767 while the destination ray row only holds ``nbins + 6`` words
and a record only offers 3072 words (3066 after its 6-word header):

* a run longer than the remaining room in the ray row overran the row -- the
  zero-fill branch checked this bound, the compressed branch never did, so a
  crafted file raised ``IndexError`` out of the reader;
* in the split-record branch the remainder is read through the raw
  ``_rbuf_p`` pointer, which Cython does not bounds-check, so a remainder
  larger than a record reads past the 6144-byte record allocation (visible
  under ASan; TC-09a);
* a failed ``_load_record`` in the split branch was ignored, silently
  re-reading the stale previous record.

The fix rejects each case through the reader's existing "truncated or
corrupt" failure path. Legitimate split runs (remainder within one record)
must keep decoding, and the bundled sample files must decode byte-for-byte
identically.
"""

import hashlib
import warnings

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.io import _sigmetfile  # noqa: E402

DATA_FILES = [
    "pyart/testing/data/example_sigmet_ppi.sigmet",
    "pyart/testing/data/example_sigmet_rhi.sigmet",
]
RECORD_SIZE = 6144
WORDS_PER_RECORD = 3072
# The sample files declare a single data type (DBZ2), so the first ray's
# compression word sits right after the 6-word raw product header and the
# single 76-byte ingest data header of the first data record.
FIRST_RAY_CODE_OFFSET = 2 * RECORD_SIZE + 12 + 76
# product_hdr = 12 + 320 + 308 bytes; number_bins is the last SINT4 of the
# 308-byte product_end substructure.
NUMBER_BINS_OFFSET = 12 + 320 + 164
EXPECTED_NUMBER_BINS = 25
# SHA-256 (first 16 hex chars) of the decoded DBZ2 array of each sample
# file. Pinned so any change in the decoder shows up as a digest mismatch.
DATA_DIGESTS = {
    "pyart/testing/data/example_sigmet_ppi.sigmet": "6fca0a51b0fd8dfe",
    "pyart/testing/data/example_sigmet_rhi.sigmet": "2da42fb1d7bd8524",
}


def _read_file_bytes(path=DATA_FILES[0]):
    with open(path, "rb") as f:
        return f.read()


def _probe_layout():
    """Sanity-check the hardcoded offsets against the sample file."""
    raw = _read_file_bytes()
    nbins = int(
        np.frombuffer(raw[NUMBER_BINS_OFFSET : NUMBER_BINS_OFFSET + 4], "<i4")[0]
    )
    assert nbins == EXPECTED_NUMBER_BINS
    assert len(raw) % RECORD_SIZE == 0
    first_code = int(
        np.frombuffer(
            raw[FIRST_RAY_CODE_OFFSET : FIRST_RAY_CODE_OFFSET + 2], "<i2"
        )[0]
    )
    # the sample's first ray is a run that exactly fills its row
    assert first_code < 0
    assert first_code + 32768 == EXPECTED_NUMBER_BINS + 6


def _patch(raw, offset, value, dtype="<i2"):
    buf = bytearray(raw)
    buf[offset : offset + np.dtype(dtype).itemsize] = np.array(
        [value], dtype=dtype
    ).tobytes()
    return bytes(buf)


def _write_tmp(tmp_path, raw):
    path = tmp_path / "crafted.sigmet"
    path.write_bytes(raw)
    return str(path)


def _read_data(path):
    """Read a crafted file, returning (data, metadata, warnings)."""
    sigmetfile = _sigmetfile.SigmetFile(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        data, metadata = sigmetfile.read_data()
    sigmetfile.close()
    return data, metadata, [str(w.message) for w in caught]


def test_sample_file_layout_is_understood():
    _probe_layout()


@pytest.mark.parametrize("path", DATA_FILES)
def test_unmodified_sample_files_read_unchanged(path):
    """Regression: the real samples must decode to the same values."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        data, metadata, msgs = _read_data(path)
    name = "DBZ2"
    assert data[name].shape[0] > 0
    # every decoded ray must report a bin count within the declared
    # maximum (25) or the missing-ray sentinel, never a garbage value
    nbins = metadata[name]["nbins"]
    reported = set(np.unique(nbins[nbins != -1]).tolist())
    assert reported and max(reported) <= EXPECTED_NUMBER_BINS
    digest = hashlib.sha256(data[name].tobytes()).hexdigest()[:16]
    assert digest == DATA_DIGESTS[path]


def test_run_longer_than_declared_bins_is_rejected(tmp_path):
    # The sample's first ray is a 31-word run filling its row exactly
    # (nbins=25 + 6 header words). Stretch it to 40 words: the old decoder
    # wrote 9 words past the row and raised IndexError out of the reader.
    crafted = _patch(_read_file_bytes(), FIRST_RAY_CODE_OFFSET, 40 - 32768)
    path = _write_tmp(tmp_path, crafted)
    data, metadata, msgs = _read_data(path)
    assert any("truncated or corrupt" in m for m in msgs)
    assert data["DBZ2"].shape[0] == 0


def test_split_run_larger_than_a_record_is_rejected(tmp_path):
    # Declare 40000 bins so the destination row is large enough to hide the
    # overflow, then request a 32767-word run spanning records. The
    # remainder (29740 words) far exceeds what one record can supply, so
    # the old decoder read ~50 KB past the record allocation through the
    # unchecked raw pointer.
    raw = _read_file_bytes()
    crafted = _patch(raw, NUMBER_BINS_OFFSET, 40000, dtype="<i4")
    crafted = _patch(crafted, FIRST_RAY_CODE_OFFSET, -1)  # words = 32767
    path = _write_tmp(tmp_path, crafted)
    data, metadata, msgs = _read_data(path)
    assert any("truncated or corrupt" in m for m in msgs)
    assert data["DBZ2"].shape[0] == 0


def test_legitimate_split_run_still_decodes(tmp_path):
    # A run that spans records but whose remainder fits inside the next
    # record is the documented split case and must keep working: 3100
    # words leave a 73-word remainder. The file is extended with a copy of
    # its only data record so the second half of the run has a real record
    # to read from.
    raw = _read_file_bytes()
    extended = raw + raw[2 * RECORD_SIZE :]
    crafted = _patch(extended, NUMBER_BINS_OFFSET, 40000, dtype="<i4")
    crafted = _patch(crafted, FIRST_RAY_CODE_OFFSET, 3100 - 32768)
    path = _write_tmp(tmp_path, crafted)
    data, metadata, msgs = _read_data(path)
    assert not any("truncated or corrupt" in m for m in msgs)
    assert data["DBZ2"].shape[0] > 0

    # the crafted run starts with the same 31 words as the sample's first
    # ray, so the first 25 decoded gates must match the unmodified read
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref, _, _ = _read_data(DATA_FILES[0])
    np.testing.assert_array_equal(
        data["DBZ2"][0, 0, :EXPECTED_NUMBER_BINS],
        ref["DBZ2"][0, 0, :EXPECTED_NUMBER_BINS],
    )
