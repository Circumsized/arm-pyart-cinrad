"""Tests for the map-animation enhancements in pyart.graph.animation."""

import numpy as np
import pytest

cartopy = pytest.importorskip("cartopy")
import matplotlib.pyplot as plt  # noqa: E402

import pyart  # noqa: E402
from pyart.graph.animation import (  # noqa: E402
    TEMPLATES,
    _apply_template,
    _azimuth_to_sweep,
    _draw_basemap_features,
    _free_radar,
    _stamp_time,
    _with_colorbar_units,
    animate_map_ppi,
)


def test_apply_template_returns_tuple():
    kwargs, figsize, title_fmt = _apply_template({}, "timespan")
    assert kwargs == {}
    assert figsize == (10, 8)
    assert title_fmt == "{site} {time}"


def test_apply_template_none():
    kwargs, figsize, title_fmt = _apply_template({"a": 1}, None)
    assert kwargs == {"a": 1}
    assert figsize is None and title_fmt is None


def test_apply_template_never_leaks_keys_into_kwargs():
    # CR-001: template figsize/title_fmt must not end up in plotting kwargs
    kwargs, figsize, title_fmt = _apply_template({}, "dualpol")
    assert "figsize" not in kwargs
    assert "title_fmt" not in kwargs
    assert figsize == TEMPLATES["dualpol"]["figsize"]


def test_azimuth_negative_clamped():
    import pyart as _p

    radar = _p.testing.make_target_radar()
    assert _azimuth_to_sweep(radar, -1) == 0


def test_format_title_never_raises():
    # CR-001 follow-up: template titles with missing placeholders render as
    # empty strings instead of raising KeyError.
    from pyart.graph.animation import _format_title

    # {field} provided
    assert _format_title("{field} - {i}", i=3, field="refl") == "refl - 3"
    # {time} missing -> safe empty
    assert _format_title("{site} {time}", site="KTLX") == "KTLX "
    # missing both -> safe empty
    assert (
        _format_title(
            "{time}",
        )
        == ""
    )
    # None -> None
    assert _format_title(None) is None


def test_plot_range_ring_range_annulus(_fake_display=None):
    # CR-003: the annulus fill must produce a real polar polygon, not the
    # old fill_between(theta, ...) stripe.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    import pyart as _p
    from pyart.graph import RadarDisplay

    radar = _p.testing.make_target_radar()
    display = RadarDisplay(radar)
    # Draw a PPI first so the display owns a radar-coordinate axes.
    fig, ax = plt.subplots()
    display.plot("reflectivity", 0, ax=ax)
    display.plot_range_ring_range((20, 50), ax=ax)

    # one filled annulus polygon present
    annulus = [p for p in ax.patches if p.get_alpha()][0]
    verts = np.concatenate([p.get_verts() for p in [annulus]])
    xs, ys = verts[:, 0], verts[:, 1]
    # outer radius 50, inner radius 20 in both axes -> a true ring, never a
    # stripe with x confined to [0, 2*pi]
    assert np.max(np.abs(xs)) > 40
    assert np.max(np.abs(ys)) > 40
    assert np.min(np.abs(xs)) < 25
    assert np.min(np.abs(ys)) < 25
    plt.close(fig)


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
    _stamp_time(ax, radar, "%Y-%m-%d")
    assert len(ax.texts) == 1


def test_with_colorbar_units_no_colorbar():
    radar = pyart.testing.make_target_radar()
    fig = plt.figure()
    _with_colorbar_units(fig, "reflectivity", radar)
    plt.close(fig)


def test_animate_map_ppi_empty_raises(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        animate_map_ppi([], "reflectivity", out=str(tmp_path / "o.gif"))


class _FakeMapDisplay:
    calls = []

    def __init__(self, radar):
        self.radar = radar

    def plot_ppi_map(self, field, sweep, ax=None, **kwargs):
        type(self).calls.append(
            {"field": field, "sweep": sweep, "ax": ax, "kwargs": kwargs}
        )


def test_animate_map_ppi_uses_plot_ppi_map(monkeypatch, tmp_path):
    _FakeMapDisplay.calls = []
    radar = pyart.testing.make_target_radar()

    captured = {}

    def spy_collect(plot_frame, radars, out, fps):
        captured["plot_frame"] = plot_frame
        plot_frame(0, radars[0])
        return out

    import pyart.graph.animation as anim_mod

    monkeypatch.setattr(anim_mod, "_collect_frames", spy_collect)
    monkeypatch.setattr(pyart.graph, "RadarMapDisplay", _FakeMapDisplay)

    out = str(tmp_path / "map.gif")
    result = animate_map_ppi(
        [radar],
        "reflectivity",
        out=out,
        resolution="50m",
        mask_outside=True,
        lat_lines=[10, 20],
        lon_lines=[100, 110],
        draw_coastline=False,
        draw_borders=False,
    )

    assert result == out
    assert len(_FakeMapDisplay.calls) == 1
    call = _FakeMapDisplay.calls[0]
    assert call["field"] == "reflectivity"
    assert call["kwargs"]["resolution"] == "50m"
    assert call["kwargs"]["mask_outside"] is True
    assert call["kwargs"]["lat_lines"] == [10, 20]
