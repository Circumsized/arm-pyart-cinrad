"""Tests for animate_map_timespan wiring (offline mocks)."""

from datetime import datetime, timedelta

import pyart
import pyart.graph.animation as anim_mod


def test_animate_map_timespan_wiring(monkeypatch):
    fake_radars = [pyart.testing.make_target_radar()]
    calls = {}

    monkeypatch.setattr(
        pyart.io, "read_time_span", lambda source, site, start, end, step: fake_radars
    )

    def fake_animate(radars, field, **kw):
        calls["animate"] = kw
        return "timespan.gif"

    monkeypatch.setattr(anim_mod, "animate_map_ppi", fake_animate)

    start = datetime(2020, 6, 1, 0, 0)
    end = datetime(2020, 6, 1, 1, 0)
    step = timedelta(minutes=5)

    out = anim_mod.animate_map_timespan(
        "nexrad",
        "KTLX",
        start,
        end,
        step,
        field="reflectivity",
        resolution="50m",
        draw_coastline=False,
    )

    assert out == "timespan.gif"
    assert calls["animate"]["resolution"] == "50m"
    assert calls["animate"]["title_fmt"] == "{site} {time}"


def test_animate_map_timespan_template_resolves_title(monkeypatch):
    fake_radars = [pyart.testing.make_target_radar()]
    calls = {}

    monkeypatch.setattr(pyart.io, "read_time_span", lambda *a, **k: fake_radars)
    monkeypatch.setattr(
        anim_mod,
        "animate_map_ppi",
        lambda radars, field, **kw: calls.setdefault("kw", kw),
    )

    anim_mod.animate_map_timespan(
        "nexrad",
        "KTLX",
        datetime(2020, 6, 1),
        datetime(2020, 6, 1, 1),
        timedelta(minutes=5),
        template="timespan",
    )
    assert calls["kw"]["title_fmt"] == "{site} {time}"
