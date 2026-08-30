"""Tests for the MeteoSwiss-style phase processing functions."""

import numpy as np

import pyart
from pyart.correct import (
    correct_sys_phase,
    det_sys_phase_ray,
    smooth_phidp_double_window,
    smooth_phidp_single_window,
)


def make_dualpol_radar():
    radar = pyart.testing.make_target_radar()
    radar.fields["differential_phase"] = {
        "data": np.full((360, 50), 180.0, dtype="float32"),
        "units": "degrees",
    }
    radar.fields["cross_correlation_ratio"] = {
        "data": np.full((360, 50), 0.99, dtype="float32"),
        "units": "",
    }
    # reflectivity already present from make_target_radar
    return radar


def test_correct_sys_phase_adds_field():
    radar = make_dualpol_radar()
    out = correct_sys_phase(radar, 60.0)
    assert "corrected_differential_phase" in radar.fields
    assert out is radar.fields["corrected_differential_phase"]
    # data offset by -60
    assert np.allclose(out["data"], 120.0)


def test_correct_sys_phase_preserves_mask():
    # CR-007: masked gates must stay masked after the offset correction
    radar = make_dualpol_radar()
    raw = radar.fields["differential_phase"]["data"]
    masked = np.ma.array(raw, mask=np.zeros(raw.shape, dtype=bool))
    masked.mask[5, 5] = True  # one masked gate
    radar.fields["differential_phase"]["data"] = masked
    out = correct_sys_phase(radar, 60.0)
    assert np.ma.isMaskedArray(out["data"])
    assert bool(out["data"].mask[5, 5]) is True


def test_det_sys_phase_ray_returns_scalar():
    radar = make_dualpol_radar()
    val = det_sys_phase_ray(radar, ind_rmin=0, ind_rmax=50,
                            min_rhoHV=0.9, min_ref=-10, smooth_len=21)
    assert val is not None
    assert np.isfinite(val)


def test_det_sys_phase_ray_no_meteo_returns_none():
    radar = make_dualpol_radar()
    radar.fields["cross_correlation_ratio"]["data"] = np.zeros((360, 50))
    val = det_sys_phase_ray(radar, ind_rmin=0, ind_rmax=50, min_rhoHV=0.5)
    assert val is None


def test_smooth_phidp_single_window_shape():
    radar = make_dualpol_radar()
    out = smooth_phidp_single_window(
        radar, 0.0, min_rhoHV=0.9, min_ref=-10, smooth_len=21)
    assert out["data"].shape == (360, 50)


def test_smooth_phidp_double_window_shape():
    radar = make_dualpol_radar()
    out = smooth_phidp_double_window(
        radar, 0.0, min_rhoHV=0.9, min_ref=-10, smooth_len=21, smooth_len2=31)
    assert out["data"].shape == (360, 50)


def test_smooth_phidp_masks_non_meteo():
    radar = make_dualpol_radar()
    # force all reflectivity below threshold so nothing is meteorological
    radar.fields["reflectivity"]["data"] = np.full((360, 50), -50.0)
    out = smooth_phidp_single_window(
        radar, 0.0, min_rhoHV=0.9, min_ref=0.0, smooth_len=21)
    fill = pyart.config.get_fillvalue()
    assert np.all(out["data"] == fill)
