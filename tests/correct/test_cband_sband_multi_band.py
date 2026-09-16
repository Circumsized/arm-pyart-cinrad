"""Tests for the three-band parametrization in pyart.correct.cband_sband."""

import numpy as np
import pytest

import pyart
from pyart.correct import BAND_PARAMS, calibrate_dualpol
from pyart.correct.cband_sband import _resolve_band


def make_radar(band=None):
    radar = pyart.testing.make_target_radar()
    radar.fields["differential_reflectivity"] = {
        "data": np.full((360, 50), 0.0, dtype="float32"),
    }
    radar.fields["differential_phase"] = {
        "data": np.full((360, 50), 180.0, dtype="float32"),
    }
    radar.fields["linear_depolarization_ratio"] = {
        "data": np.full((360, 50), -30.0, dtype="float32"),
    }
    if band is not None:
        radar.metadata["radar_band"] = band
    return radar


def test_band_params_has_all_bands():
    assert set(BAND_PARAMS) == {"X", "C", "S"}


def test_resolve_band_present():
    for band in ("X", "C", "S", "x", "s"):
        radar = make_radar(band=band)
        assert _resolve_band(radar) == band.upper()


def test_resolve_band_missing_defaults_to_c():
    radar = make_radar()  # no radar_band
    with pytest.warns(UserWarning):
        assert _resolve_band(radar) == "C"


def test_calibrate_dualpol_uses_calibration_priority():
    radar = make_radar(band="X")
    radar.radar_calibration = {
        "zdr_calibration": {"data": np.array([0.5], dtype="float32")},
    }
    corr = calibrate_dualpol(radar)
    assert "differential_reflectivity" in corr
    assert np.allclose(corr["differential_reflectivity"]["data"], -0.5)


def test_calibrate_dualpol_no_calibration_no_op():
    radar = make_radar(band="S")
    corr = calibrate_dualpol(radar)
    # no nonzero offsets, so data unchanged
    assert "differential_reflectivity" in corr
    assert np.allclose(corr["differential_reflectivity"]["data"], 0.0)
