"""
CINRAD X/S/C-band dual-polarization animation example
=====================================================

Build a GIF animation from a series of radar files (any band supported by
the readers) or Radar objects, and save it as ``dualpol_animation.gif``.

Run with::

    python plot_dualpol_animation.py frame1.bin frame2.bin ...

"""

import sys

import matplotlib
from matplotlib import font_manager

from pyart.graph.animation import animate_ppi


def configure_chinese_font():
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


if __name__ == "__main__":
    configure_chinese_font()

    files = sys.argv[1:]
    if not files:
        raise SystemExit(
            "usage: python plot_dualpol_animation.py <file1> [file2 ...]\n"
            "files may be C-band .AR2, S-band .bin, or X-band AXPT/DXK "
            "base data; mixing bands in one animation is allowed but the "
            "field must exist in all frames"
        )

    # --- PPI animation ---------------------------------------------------
    animate_ppi(
        files,
        "reflectivity",
        sweep=0,
        out="ppi_animation.gif",
        vmin=0,
        vmax=70,
        fps=3,
        title_fmt="PPI Frame {i}",
    )

    # --- RHI animation (single radar, several tilts shown as frames) -----
    # animate_rhi(files, 'reflectivity', out='rhi_animation.gif')

    # --- Map-projected animation (requires cartopy) ----------------------
    # animate_map_ppi(files, 'reflectivity', out='map_animation.gif',
    #                 extent=(116., 123., 30., 35.))

    print("saved ppi_animation.gif")
