"""
Security regression tests for the Sigmet ray-header nbins sink (FU-03).

Covers the out-of-bounds heap write (6ff3be, CWE-787) in
``pyart.io._sigmetfile._mask_gates_not_collected``.

The function is compiled with ``@cython.boundscheck(False)`` and uses the
per-ray ``nbins`` int16 word -- straight from an untrusted Sigmet/IRIS ray
header -- as the lower bound of ``range(nbin, full_nbins)``. Cython only
rebases a negative index once (``idx += shape``), so any ``nbin`` below
``-full_nbins`` (e.g. -32768) stays negative and ``mask[i, j] = 1`` writes
before the NumPy allocation. A legitimate missing ray already injects -1
into the same column, so the negative path is not hypothetical.

These tests drive the public ``convert_sigmet_data`` converter, which is
the documented entry point of the masking routine.
"""

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.io import _sigmetfile  # noqa: E402

DBZ2 = 9  # 2-byte reflectivity; the 1-byte path needs a subarray view that
# NumPy 2 rejects, so the 2-byte format exercises the same masking sink.
NRAYS = 2
FULL_NBINS = 100


def _convert(nbins_value):
    nbins = np.full(NRAYS, nbins_value, dtype="int16")
    # non-zero raw codes so the per-format "no data" masking does not fire
    data = np.full((NRAYS, FULL_NBINS), 2000, dtype="uint16")
    return _sigmetfile.convert_sigmet_data(DBZ2, data, nbins)


def test_convert_sigmet_data_negative_nbins_does_not_write_out_of_bounds():
    """A crafted nbins below -full_nbins must not write outside the mask."""
    result = _convert(-32768)
    # whole ray treated as not collected (clamped semantics); crucially the
    # process survives and the mask stays consistent with its shape.
    assert result.mask.shape == (NRAYS, FULL_NBINS)


def test_convert_sigmet_data_small_negative_nbins_does_not_write_out_of_bounds():
    result = _convert(-(FULL_NBINS + 1))
    assert result.mask.shape == (NRAYS, FULL_NBINS)


def test_convert_sigmet_data_missing_ray_semantics_preserved():
    """The legitimate -1 missing-ray sentinel must keep its behaviour."""
    result = _convert(-1)
    assert result.mask.all()


def test_convert_sigmet_data_normal_nbins_masks_only_beyond_nbin():
    result = _convert(50)
    assert not result.mask[:, :50].any()
    assert result.mask[:, 50:].all()


def test_convert_sigmet_data_oversized_nbins_is_clamped():
    """nbins beyond full_nbins must not mask anything (or crash)."""
    result = _convert(30000)
    assert not result.mask.any()
