"""
pyart.graph.dualpol_diagnostic
==============================

A MeteoSwiss-style four-panel dual-polarization diagnostic plot combining
reflectivity, differential reflectivity, specific differential phase and
cross-correlation ratio.
"""

import numpy as np

from . import RadarDisplay


def plot_dualpol_diagnostic(
    radar,
    sweep=0,
    fields=None,
    vmin_max=None,
    cmap=None,
    ax=None,
    title=None,
    show_zenith_lines=True,
):
    """
    Plot a four-panel dual-polarization diagnostic.

    By default the panels show ``reflectivity``,
    ``differential_reflectivity``, ``specific_differential_phase`` and
    ``cross_correlation_ratio`` in a 2x2 grid, following the MeteoSwiss/pyart
    diagnostic layout.

    Parameters
    ----------
    radar : Radar
        Input radar.
    sweep : int, optional
        Sweep number to display.
    fields : tuple of str, optional
        Four field names to display. None uses the default four.
    vmin_max : dict, optional
        Optional mapping of field name to ``(vmin, vmax)`` color limits.
    cmap : str or None, optional
        Colormap name forwarded to each plot.
    ax : array of Axes or None, optional
        Existing axes to reuse. None creates a new 2x2 figure.
    title : str or None, optional
        Figure super-title.
    show_zenith_lines : bool, optional
        Accepted for MeteoSwiss/pyart API compatibility; no-op for PPI.

    Returns
    -------
    axes : ndarray of Axes
        The axes used, in field order.

    """
    if fields is None:
        fields = (
            "reflectivity",
            "differential_reflectivity",
            "specific_differential_phase",
            "cross_correlation_ratio",
        )
    vmin_max = vmin_max or {}

    import matplotlib.pyplot as plt

    if ax is not None:
        axes = np.asarray(ax).ravel()
    else:
        fig, axes = plt.subplots(2, 2, figsize=(12, 12))
        axes = np.asarray(axes).ravel()

    display = RadarDisplay(radar)
    for axi, field in zip(axes, fields):
        if field not in radar.fields:
            axi.set_visible(False)
            continue
        limits = vmin_max.get(field)
        vmin = limits[0] if limits else None
        vmax = limits[1] if limits else None
        display.plot(field, sweep, ax=axi, vmin=vmin, vmax=vmax, cmap=cmap)

    if title is not None and len(axes):
        axes[0].figure.suptitle(title)

    return axes