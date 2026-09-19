"""
Security regression tests for the NEXRAD moment interpolation contract (FU-10).

Covers the gate-count mismatch between the interpolation kernels and the
data array they write into (3b55ab+89582b, CWE-787/125):

* ``_find_range_params`` derived every moment's ``last_gate`` from the
  *first* moment's gate count, so a moment with more gates than moment 0
  produced a range (and therefore a data width) too narrow for it;
* ``_interpolate_scan`` handed ``moment_ngates`` straight to the Cython
  kernels, which write ``multiplier * moment_ngates`` gates per ray without
  any contract check -- a mismatch overran the ray row (surfacing as an
  ``IndexError`` from the bounds-checked memoryviews, or as memory
  corruption wherever the check is compiled out);
* the kernels accepted a negative ``start`` (silently wrapping to the last
  ray) and an ``end`` past the last ray.

The fix rejects each case explicitly: ``PyARTDataError`` from the reader
layer, ``ValueError`` contract assertions at the kernel entry, and a range
widened so that legitimate mixed gate-spacing volumes (250 m reflectivity
next to 1000 m velocity) still interpolate instead of being rejected.
"""

import hashlib
import warnings

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.exceptions import PyARTDataError  # noqa: E402
from pyart.io.nexrad_archive import (  # noqa: E402
    _find_range_params,
    _interp_headroom_last_gate,
    _interpolate_scan,
)
from pyart.io.nexrad_interpolate import (  # noqa: E402
    _fast_interpolate_scan_2,
    _fast_interpolate_scan_4,
)

DATA_FILE = "pyart/testing/data/example_nexrad_archive_msg31_compressed.ar2v"
# SHA-256 (first 16 hex chars) of each field's filled data array. Pinned so
# any change in range/interpolation shows up as a digest mismatch.
FIELD_DIGESTS = {
    "reflectivity": "8eb438aac88d869e",
    "cross_correlation_ratio": "54bddb65d6c06fe3",
    "differential_reflectivity": "2e052613fcce2dd0",
    "differential_phase": "4057f0dff020a54b",
}


class _StubFileMetadata:
    """Minimal filemetadata stand-in: every moment maps to a field."""

    def get_field_name(self, moment):
        return moment


def _data_and_scratch(nrays, width):
    data = np.zeros((nrays, width), dtype="float32")
    scratch = np.zeros((width,), dtype="float32")
    return data, scratch


# ------------------------- kernel contract -------------------------


def test_fast_interpolate_scan_4_rejects_ngates_wider_than_data():
    # 4 * 20 = 80 interpolated gates do not fit a 40-gate ray row.
    data, scratch = _data_and_scratch(3, 40)
    with pytest.raises(ValueError, match="gates"):
        _fast_interpolate_scan_4(data, scratch, -9999.0, 0, 2, 20, 0)


def test_fast_interpolate_scan_2_rejects_ngates_wider_than_data():
    # 2 * 30 - 1 = 59 interpolated gates do not fit a 40-gate ray row.
    data, scratch = _data_and_scratch(3, 40)
    with pytest.raises(ValueError, match="gates"):
        _fast_interpolate_scan_2(data, scratch, -9999.0, 0, 2, 30, 0)


def test_fast_interpolate_scan_4_rejects_scratch_ray_too_small():
    data, scratch = _data_and_scratch(3, 100)
    scratch = np.zeros((40,), dtype="float32")
    with pytest.raises(ValueError, match="scratch_ray"):
        _fast_interpolate_scan_4(data, scratch, -9999.0, 0, 2, 20, 0)


@pytest.mark.parametrize("start,end", [(-1, 0), (0, 3)])
def test_fast_interpolate_scan_4_rejects_invalid_ray_range(start, end):
    data, scratch = _data_and_scratch(3, 40)
    with pytest.raises(ValueError, match="ray range"):
        _fast_interpolate_scan_4(data, scratch, -9999.0, start, end, 5, 0)


def test_fast_interpolate_scan_4_allows_empty_sweep_range():
    # A sweep with no collected rays yields start == end + 1; the kernel
    # must treat that as a no-op rather than an error.
    data, scratch = _data_and_scratch(3, 40)
    _fast_interpolate_scan_4(data, scratch, -9999.0, 2, 1, 5, 0)
    assert not data.any()


def test_fast_interpolate_scan_4_rejects_zero_moment_ngates():
    data, scratch = _data_and_scratch(3, 40)
    with pytest.raises(ValueError, match="moment_ngates"):
        _fast_interpolate_scan_4(data, scratch, -9999.0, 0, 2, 0, 0)


def test_fast_interpolate_scan_4_nearest_neighbor_values_unchanged():
    data, scratch = _data_and_scratch(2, 12)
    data[0, :3] = [0.0, 40.0, 80.0]
    _fast_interpolate_scan_4(data, scratch, -9999.0, 0, 0, 3, 0)
    np.testing.assert_allclose(
        data[0], [0, 0, 0, 0, 40, 40, 40, 40, 80, 80, 80, 80], atol=1e-4
    )


def test_fast_interpolate_scan_4_linear_values_unchanged():
    data, scratch = _data_and_scratch(2, 12)
    data[0, :3] = [0.0, 40.0, 80.0]
    _fast_interpolate_scan_4(data, scratch, -9999.0, 0, 0, 3, 1)
    np.testing.assert_allclose(
        data[0], [0, 0, 5, 15, 25, 35, 45, 55, 65, 75, 80, 80], atol=1e-4
    )


def test_fast_interpolate_scan_2_values_unchanged():
    data, scratch = _data_and_scratch(2, 6)
    data[0, :3] = [0.0, 40.0, 80.0]
    _fast_interpolate_scan_2(data, scratch, -9999.0, 0, 0, 3, 0)
    np.testing.assert_allclose(data[0], [0, 0, 40, 40, 80, 0], atol=1e-4)


# --------------------- reader-level contract ---------------------


def test_interpolate_scan_rejects_width_mismatch():
    mdata = np.ma.masked_array(np.zeros((3, 40), dtype="float32"))
    with pytest.raises(PyARTDataError, match="gates"):
        _interpolate_scan(mdata, 0, 2, 20, "4")


def test_interpolate_scan_rejects_invalid_ray_range():
    mdata = np.ma.masked_array(np.zeros((3, 40), dtype="float32"))
    with pytest.raises(PyARTDataError, match="ray range"):
        _interpolate_scan(mdata, -1, 0, 5, "4")


def test_interpolate_scan_valid_case_unchanged():
    mdata = np.ma.masked_array(np.zeros((2, 12), dtype="float32"))
    mdata[0, :3] = [0.0, 40.0, 80.0]
    _interpolate_scan(mdata, 0, 0, 3, "4", linear_interp=False)
    np.testing.assert_allclose(
        mdata.data[0], [0, 0, 0, 0, 40, 40, 40, 40, 80, 80, 80, 80], atol=1e-4
    )
    assert not mdata.mask[0].any()


# --------------------- range parameter derivation ---------------------


def test_find_range_params_uses_per_moment_ngates():
    # VEL (moment 1) is the coarse 1000 m moment with 1000 gates; REF
    # (moment 0) only has 460. The old code used moment 0's count for
    # every moment and stopped the range at 459500 m instead of 999500 m.
    scan_info = [
        {
            "moments": ["REF", "VEL"],
            "first_gate": [0.0, 0.0],
            "gate_spacing": [250.0, 1000.0],
            "ngates": [460, 1000],
        }
    ]
    first, spacing, last = _find_range_params(scan_info, _StubFileMetadata())
    assert first == 0.0
    assert spacing == 250.0
    assert last == pytest.approx(1000.0 * (1000 - 0.5))


def test_interp_headroom_widens_range_for_interpolated_moments():
    # A 250 m reflectivity moment with 1832 gates next to a 1000 m velocity
    # moment with 460 gates: the physical last gate center is 459500 m
    # (1838 gates at 250 m) but the interpolated velocity needs
    # 4 * 460 = 1840 gates. The range must be widened or the interpolation
    # would be rejected as a mismatch.
    scan_info = [
        {
            "moments": ["REF", "VEL"],
            "first_gate": [0.0, 0.0],
            "gate_spacing": [250.0, 1000.0],
            "ngates": [1832, 460],
        }
    ]
    interpolate = {"VEL": [0], "multiplier": "4"}
    widened = _interp_headroom_last_gate(
        scan_info, interpolate, 0.0, 250.0, 459500.0
    )
    assert widened == pytest.approx(1840 * 250.0)
    # a range that is already wide enough is left untouched
    assert _interp_headroom_last_gate(
        scan_info, interpolate, 0.0, 250.0, 500000.0
    ) == pytest.approx(500000.0)
    # nothing to interpolate leaves the range untouched
    assert _interp_headroom_last_gate(
        scan_info, {}, 0.0, 250.0, 459500.0
    ) == pytest.approx(459500.0)


def test_interp_headroom_honors_multiplier_2():
    scan_info = [
        {
            "moments": ["REF", "VEL"],
            "first_gate": [0.0, 0.0],
            "gate_spacing": [150.0, 300.0],
            "ngates": [1000, 500],
        }
    ]
    interpolate = {"VEL": [0], "multiplier": "2"}
    widened = _interp_headroom_last_gate(scan_info, interpolate, 0.0, 150.0, 100.0)
    # 2 * 500 - 1 interpolated gates at the 150 m finest spacing
    assert widened == pytest.approx((2 * 500 - 1) * 150.0)


# --------------------- real file regression ---------------------


def test_real_nexrad_archive_read_unchanged():
    from pyart.io import read_nexrad_archive

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        radar = read_nexrad_archive(DATA_FILE)
    for name, digest in FIELD_DIGESTS.items():
        data = radar.fields[name]["data"]
        assert data.shape == (120, 1832)
        assert hashlib.sha256(data.filled(-9999).tobytes()).hexdigest()[:16] == digest
