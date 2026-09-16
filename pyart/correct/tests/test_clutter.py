"""
Tests for the MeteoSwiss-inspired clutter mask (pyart.correct.clutter).
"""

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.correct.clutter import apply_clutter_mask, clutter_mask


@pytest.fixture(scope="module")
def radar():
    from pyart.testing import make_empty_ppi_radar

    r = make_empty_ppi_radar(10, 5, 1)
    ngates, nrays = 10, 5
    # constant reflectivity region (gates 0-6) with a sharp drop further
    # along the ray so that gates 0, 1, 4 have near-zero texture
    refl = np.full((nrays, ngates), 40.0, dtype="float32")
    refl[:, 7:] = 5.0
    rho = np.full((nrays, ngates), 0.90, dtype="float32")
    rho[:, [0, 1, 4]] = 0.99
    r.add_field("reflectivity", {"data": refl, "units": "dBZ"})
    r.add_field("cross_correlation_ratio", {"data": rho, "units": "unitless"})
    return r


def test_clutter_mask_shape(radar):
    mask = clutter_mask(radar, max_texture=3.0, min_rho=0.95)
    assert mask.shape == (radar.nrays, radar.ngates)
    assert mask.dtype == bool


def test_clutter_mask_flags_constant_high_rho(radar):
    mask = clutter_mask(radar, max_texture=3.0, min_rho=0.95)
    # gates 0, 1, 4 have rho=0.99 and constant reflectivity -> clutter
    assert mask[0, 0] and mask[0, 1] and mask[0, 4]
    # gates 2, 3, 5 have rho=0.90 -> not clutter
    assert not mask[0, 2] and not mask[0, 3] and not mask[0, 5]


def test_clutter_mask_requires_reflectivity(radar):
    saved = radar.fields.pop("reflectivity")
    try:
        with pytest.raises(ValueError, match="reflectivity"):
            clutter_mask(radar)
    finally:
        radar.fields["reflectivity"] = saved


def test_apply_clutter_mask(radar):
    mask = clutter_mask(radar, max_texture=3.0, min_rho=0.95)
    apply_clutter_mask(radar, mask)
    data = radar.fields["reflectivity"]["data"]
    assert np.ma.is_masked(data[0, 0])
    assert not np.ma.is_masked(data[0, 2])
