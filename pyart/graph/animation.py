"""
pyart.graph.animation
=====================

Create GIF animations from Py-ART Radar objects, supporting PPI sweeps,
RHI cross-sections and projected map displays.

Requires the optional ``imageio`` dependency::

    pip install arm_pyart[animation]

Map animations additionally require cartopy.

"""

import contextlib
import os
import warnings

import matplotlib
import numpy as np


MAX_FRAMES = 200


def _import_imageio():
    try:
        import imageio
    except ImportError as exc:
        raise ImportError(
            'imageio is required for animations; install it with '
            '"pip install arm_pyart[animation]"') from exc
    return imageio


def _to_radars(radars_or_files):
    """ Normalize a list of Radar objects or file paths to Radar objects. """
    import pyart

    radars = []
    for item in radars_or_files:
        if hasattr(item, 'fields'):
            radars.append(item)
        else:
            radars.append(pyart.io.read(item))
    return radars


def _check_frames(radars, out):
    if len(radars) == 0:
        raise ValueError('No radar frames provided')
    if len(radars) > MAX_FRAMES:
        warnings.warn(
            'Truncating animation from {0} to {1} frames'.format(
                len(radars), MAX_FRAMES))
        radars = radars[:MAX_FRAMES]
    directory = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(directory):
        raise ValueError('Output directory does not exist: {0}'.format(
            directory))
    return radars


def _collect_frames(plot_frame, radars, out, fps):
    imageio = _import_imageio()
    frames = []
    with matplotlib.rc_context({'backend': 'Agg'}):
        import matplotlib.pyplot as plt
        for i, radar in enumerate(radars):
            fig = plot_frame(i, radar)
            fig.canvas.draw()
            buf = np.asarray(fig.canvas.buffer_rgba())
            frames.append(buf.copy())
            plt.close(fig)
    imageio.mimwrite(out, frames, fps=fps, loop=0)
    return out


def animate_ppi(radars_or_files, field, sweep=0, out='ppi.gif', vmin=None,
                vmax=None, fps=4, title_fmt=None, gatefilter=None,
                display_kwargs=None):
    """
    Create a GIF animation of PPI sweeps.

    Parameters
    ----------
    radars_or_files : list
        Radar objects or file paths (any format supported by
        :func:`pyart.io.read`), rendered one frame per entry.
    field : str
        Field to plot, e.g. ``'reflectivity'``.
    sweep : int, optional
        Sweep number to display.
    out : str, optional
        Output GIF path.
    vmin, vmax : float, optional
        Color limits. Defaults come from the Py-ART configuration.
    fps : int, optional
        Frames per second.
    title_fmt : str or None, optional
        Title template with a ``{i}`` placeholder for the frame index,
        e.g. ``'Frame {i}'``.
    gatefilter : GateFilter or None, optional
        Optional gate filter applied to every frame.
    display_kwargs : dict, optional
        Extra keyword arguments forwarded to ``RadarDisplay.plot``.

    Returns
    -------
    out : str
        The output GIF path.

    """
    import pyart

    radars = _to_radars(radars_or_files)
    radars = _check_frames(radars, out)
    display_kwargs = dict(display_kwargs or {})

    def plot_frame(i, radar):
        import matplotlib.pyplot as plt
        display = pyart.graph.RadarDisplay(radar)
        fig = plt.figure(figsize=(8, 8))
        display.plot(field, sweep, vmin=vmin, vmax=vmax,
                     gatefilter=gatefilter,
                     colorbar_label='', ax=fig.add_subplot(111),
                     **display_kwargs)
        if title_fmt is not None:
            plt.title(title_fmt.format(i=i))
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def animate_rhi(radars_or_files, field, azimuth=None, out='rhi.gif', vmin=None,
                vmax=None, fps=4, title_fmt=None, display_kwargs=None):
    """
    Create a GIF animation of RHI cross-sections.

    Parameters are as in :func:`animate_ppi`; ``azimuth`` selects the RHI
    cross-section (defaults to the first available azimuth when None).

    """
    import pyart

    radars = _to_radars(radars_or_files)
    radars = _check_frames(radars, out)
    display_kwargs = dict(display_kwargs or {})

    def plot_frame(i, radar):
        display = pyart.graph.RadarDisplay(radar)
        az = azimuth
        if az is None:
            az = float(np.round(np.median(radar.azimuth['data']), 1))
        fig = plt.figure(figsize=(10, 6))
        display.plot_rhi(field, az, vmin=vmin, vmax=vmax,
                         colorbar_label='', ax=fig.add_subplot(111),
                         **display_kwargs)
        if title_fmt is not None:
            plt.title(title_fmt.format(i=i))
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def animate_map_ppi(radars_or_files, field, sweep=0, out='map.gif', vmin=None,
                    vmax=None, fps=4, title_fmt=None, projection=None,
                    extent=None, display_kwargs=None):
    """
    Create a GIF animation of PPI sweeps on a projected map (cartopy).

    Parameters
    ----------
    projection : cartopy projection or None, optional
        Defaults to ``cartopy.crs.PlateCarree()``.
    extent : 4-tuple, optional
        Map extent (lon0, lon1, lat0, lat1).
    Others as in :func:`animate_ppi`.

    """
    import pyart

    try:
        import cartopy  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            'cartopy is required for map animations; install it with '
            '"pip install cartopy"') from exc

    radars = _to_radars(radars_or_files)
    radars = _check_frames(radars, out)
    display_kwargs = dict(display_kwargs or {})

    if projection is None:
        import cartopy.crs as ccrs
        projection = ccrs.PlateCarree()

    def plot_frame(i, radar):
        display = pyart.graph.RadarMapDisplay(radar)
        fig = plt.figure(figsize=(10, 8))
        ax = fig.add_subplot(111, projection=projection)
        display.plot(field, sweep, vmin=vmin, vmax=vmax,
                     colorbar_label='', ax=ax, **display_kwargs)
        if extent is not None:
            ax.set_extent(extent)
        if title_fmt is not None:
            plt.title(title_fmt.format(i=i))
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


__all__ = ['animate_ppi', 'animate_rhi', 'animate_map_ppi']