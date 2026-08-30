"""Tests for the four-panel dual-polarization diagnostic plot."""

import matplotlib
matplotlib.use("Agg")

import numpy as np

import pyart
from pyart.graph import plot_dualpol_diagnostic


def make_dualpol_radar():
    radar = pyart.testing.make_target_radar()
    radar.fields["differential_reflectivity"] = {
        "data": np.full((360, 50), 1.5, dtype="float32"),
    }
    radar.fields["specific_differential_phase"] = {
        "data": np.full((360, 50), 0.5, dtype="float32"),
    }
    radar.fields["cross_correlation_ratio"] = {
        "data": np.full((360, 50), 0.99, dtype="float32"),
    }
    return radar


def test_plot_dualpol_diagnostic_returns_axes():
    radar = make_dualpol_radar()
    axes = plot_dualpol_diagnostic(radar)
    assert axes.shape == (4,)


def test_plot_dualpol_diagnostic_missing_field_skips():
    radar = make_dualpol_radar()
    del radar.fields["differential_reflectivity"]
    axes = plot_dualpol_diagnostic(radar)
    assert axes.shape == (4,)


def test_plot_dualpol_diagnostic_with_limits():
    radar = make_dualpol_radar()
    axes = plot_dualpol_diagnostic(
        radar,
        vmin_max={"reflectivity": (-10, 60)},
        title="diagnostic",
    )
    assert axes.shape == (4,)