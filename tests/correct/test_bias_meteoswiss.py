"""Tests for the MeteoSwiss-style bias / self-consistency estimators."""

import numpy as np

import pyart
from pyart.correct import (
    est_rhohv_rain,
    est_zdr_precip,
    est_zdr_snow,
    selfconsistency_bias,
    selfconsistency_bias2,
    selfconsistency_kdp_phidp,
    selfconsistency_zdr_zh,
)


def make_dualpol_radar():
    radar = pyart.testing.make_target_radar()
    # reflectivity present; add ZDR / RhoHV / KDP
    radar.fields["differential_reflectivity"] = {
        "data": np.full((360, 50), 0.0, dtype="float32"),
    }
    radar.fields["cross_correlation_ratio"] = {
        "data": np.full((360, 50), 0.99, dtype="float32"),
    }
    radar.fields["specific_differential_phase"] = {
        "data": np.full((360, 50), 10.0, dtype="float32"),
    }
    return radar


def test_est_rhohv_rain_returns_scalar():
    radar = make_dualpol_radar()
    val = est_rhohv_rain(
        radar,
        ind_rmin=0,
        ind_rmax=50,
        min_ref=-10,
        zdr_min=-2.0,
        zdr_max=2.0,
        kdp_min=0.0,
        kdp_max=100.0,
    )
    assert val is not None
    assert 0.0 <= val <= 1.0


def test_est_rhohv_rain_no_meteo():
    radar = make_dualpol_radar()
    radar.fields["reflectivity"]["data"] = np.full((360, 50), -50.0)
    val = est_rhohv_rain(radar, ind_rmin=0, ind_rmax=50, min_ref=30)
    assert val is None


def test_est_zdr_precip():
    radar = make_dualpol_radar()
    val = est_zdr_precip(radar, min_ref=-10, max_ref=60, min_rhohv=0.5)
    assert val is not None


def test_est_zdr_snow():
    radar = make_dualpol_radar()
    val = est_zdr_snow(radar, min_ref=-10, max_ref=60, min_rhohv=0.5)
    assert val is not None


def test_selfconsistency_returns_scalar():
    radar = make_dualpol_radar()
    params = {}
    for fn in (
        selfconsistency_bias,
        selfconsistency_bias2,
        selfconsistency_kdp_phidp,
        selfconsistency_zdr_zh,
    ):
        val = fn(radar, params)
        assert val is None or np.isfinite(val)
