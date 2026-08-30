"""Tests for the map-animation enhancements in pyart.graph.animation."""

import pytest

cartopy = pytest.importorskip("cartopy")
import matplotlib.pyplot as plt  # noqa: E402

import pyart  # noqa: E402
from pyart.graph.animation import (  # noqa: E402
    _draw_basemap_features,
    _free_radar,
    _stamp_time,
    _with_colorbar_units,
    animate_map_ppi,
)


class _FakeAx:
    transAxes = None

    def __init__(self):
        self.features = []
        self.texts = []

    def add_feature(self, feature):
        self.features.append(feature)

    def text(self, *args, **kwargs):
        self.texts.append((args, kwargs))


def test_draw_basemap_features():
    ax = _FakeAx()
    _draw_basemap_features(ax, True, True)
    assert len(ax.features) == 2
    _draw_basemap_features(ax, False, False)
    assert len(ax.features) == 2  # nothing added


def test_free_radar_context_manager():
    radar = object()
    with _free_radar(radar) as r:
        assert r is radar


def test_stamp_time(tmp_axes=None):
    radar = pyart.testing.make_target_radar()
    ax = _FakeAx()
    _stamp_time(ax, radar, '%Y-%m-%d')
    assert len(ax.texts) == 1


def test_with_colorbar_units_no_colorbar():
    radar = pyart.testing.make_target_radar()
    fig = plt.figure()
    _with_colorbar_units(fig, 'reflectivity', radar)
    plt.close(fig)


def test_animate_map_ppi_empty_raises(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        animate_map_ppi([], 'reflectivity', out=str(tmp_path / "o.gif"))


class _FakeMapDisplay:
    calls = []

    def __init__(self, radar):
        self.radar = radar

    def plot_ppi_map(self, field, sweep, ax=None, **kwargs):
        type(self).calls.append(
            {'field': field, 'sweep': sweep, 'ax': ax, 'kwargs': kwargs})


def test_animate_map_ppi_uses_plot_ppi_map(monkeypatch, tmp_path):
    _FakeMapDisplay.calls = []
    radar = pyart.testing.make_target_radar()

    captured = {}

    def spy_collect(plot_frame, radars, out, fps):
        captured['plot_frame'] = plot_frame
        plot_frame(0, radars[0])
        return out

    import pyart.graph.animation as anim_mod
    monkeypatch.setattr(anim_mod, "_collect_frames", spy_collect)
    monkeypatch.setattr(pyart.graph, "RadarMapDisplay", _FakeMapDisplay)

    out = str(tmp_path / "map.gif")
    result = animate_map_ppi(
        [radar], 'reflectivity', out=out,
        resolution='50m', mask_outside=True, lat_lines=[10, 20],
        lon_lines=[100, 110], draw_coastline=False, draw_borders=False)

    assert result == out
    assert len(_FakeMapDisplay.calls) == 1
    call = _FakeMapDisplay.calls[0]
    assert call['field'] == 'reflectivity'
    assert call['kwargs']['resolution'] == '50m'
    assert call['kwargs']['mask_outside'] is True
    assert call['kwargs']['lat_lines'] == [10, 20]