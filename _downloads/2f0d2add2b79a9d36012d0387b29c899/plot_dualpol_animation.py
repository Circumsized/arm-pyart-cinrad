"""
CINRAD X/S/C-band dual-polarization animation example
=====================================================

Build a GIF animation from a series of radar files (any band supported by
the readers) or Radar objects, and save it as ``dualpol_animation.gif``.

When run as a script **with** file arguments, those files are animated::

    python plot_dualpol_animation.py frame1.bin frame2.bin ...

When executed **without** arguments -- which is how Sphinx-Gallery renders
this example -- a pair of synthetic PPI radars is generated so the example
is fully self-contained and does not need any external data.

"""

import sys

import matplotlib
import numpy as np
from matplotlib import font_manager

from pyart.graph.animation import animate_ppi
from pyart.testing import make_empty_ppi_radar


def configure_chinese_font():
    """Select the first available CJK font, or leave the default in place."""
    preferred = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "Noto Sans CJK SC",
        "PingFang SC",
    ]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    matplotlib.rcParams["axes.unicode_minus"] = False
    for name in preferred:
        if name in installed:
            matplotlib.rcParams["font.sans-serif"] = [name]
            return name
    return None


def make_synthetic_frames(n=3):
    """Return ``n`` synthetic PPI radars carrying a reflectivity field."""
    frames = []
    for i in range(n):
        radar = make_empty_ppi_radar(ngates=60, rays_per_sweep=90, nsweeps=1)
        # A simple range-dependent, frame-varying echo so the GIF differs
        # from one frame to the next.
        gates = radar.range["data"]
        rays = radar.azimuth["data"]
        data = (
            40.0
            * np.exp(-((gates[None, :] - (20.0 + 5.0 * i)) ** 2) / (2 * 12.0**2))
            * np.cos(np.deg2rad(rays[:, None]))
        )
        radar.add_field(
            "reflectivity",
            {
                "data": np.ma.masked_invalid(data),
                "units": "dBZ",
                "long_name": "Synthetic reflectivity",
            },
        )
        frames.append(radar)
    return frames


def main(files):
    """Animate ``files`` if given, otherwise fall back to synthetic frames."""
    target = files or make_synthetic_frames()

    animate_ppi(
        target,
        "reflectivity",
        sweep=0,
        out="ppi_animation.gif",
        vmin=0,
        vmax=70,
        fps=3,
        title_fmt="PPI Frame {i}",
    )
    print("saved ppi_animation.gif")

    # --- RHI animation (single radar, several tilts shown as frames) -----
    # animate_rhi(target, 'reflectivity', out='rhi_animation.gif')

    # --- Map-projected animation (requires cartopy) ----------------------
    # animate_map_ppi(target, 'reflectivity', out='map_animation.gif',
    #                 extent=(116., 123., 30., 35.))


# Sphinx-Gallery imports this module and executes the body, so run the
# example unconditionally. When invoked from a shell the ``if __name__``
# guard simply re-uses the same entry point with the user's file paths.
configure_chinese_font()
main(sys.argv[1:])
