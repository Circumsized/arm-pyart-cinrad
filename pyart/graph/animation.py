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

TEMPLATES = {
    'dualpol': {
        'fields': ['reflectivity', 'differential_reflectivity',
                   'cross_correlation_ratio', 'specific_differential_phase'],
        'figsize': (12, 12),
        'title_fmt': '{field} - {i}',
    },
    'timeseries': {
        'fields': ['reflectivity'],
        'figsize': (10, 8),
        'title_fmt': '{time}',
    },
    'dualpol_map': {
        'fields': ['reflectivity', 'differential_reflectivity',
                   'cross_correlation_ratio', 'specific_differential_phase'],
        'figsize': (12, 12),
        'title_fmt': '{field} - {i}',
    },
    'timespan': {
        'fields': ['reflectivity'],
        'figsize': (10, 8),
        'title_fmt': '{site} {time}',
    },
}


def _apply_template(kwargs, template):
    if template is None or template not in TEMPLATES:
        return kwargs
    preset = TEMPLATES[template]
    out = dict(kwargs)
    if 'figsize' in preset and 'figsize' not in out:
        out['figsize'] = preset['figsize']
    if 'title_fmt' in preset and 'title_fmt' not in out:
        out['title_fmt'] = preset['title_fmt']
    return out


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
            with _free_radar(radar) as radar:
                fig = plot_frame(i, radar)
                fig.canvas.draw()
                buf = np.asarray(fig.canvas.buffer_rgba())
                frames.append(buf.copy())
                plt.close(fig)
    imageio.mimwrite(out, frames, fps=fps, loop=0)
    return out


@contextlib.contextmanager
def _free_radar(radar):
    """Yield ``radar`` and drop the local reference afterwards.

    Best-effort memory hygiene aligned with the ``del radar`` idiom used by
    ``zssherman/pyart_animation`` when animating long time spans. The radar
    object itself is not deallocated while the caller still holds a reference.
    """
    try:
        yield radar
    finally:
        del radar


def _draw_basemap_features(ax, draw_coastline=True, draw_borders=True):
    """Add coastline and country-border features to a cartopy GeoAxes.

    This is the cartopy equivalent of the Basemap-era
    ``display.basemap.drawcounties()`` call from zssherman/pyart_animation.
    """
    import cartopy.feature as cfeature
    if draw_coastline:
        ax.add_feature(cfeature.COASTLINE.with_scale('50m'))
    if draw_borders:
        ax.add_feature(cfeature.BORDERS.with_scale('50m'))


def _stamp_time(ax, radar, timestamp_fmt='%Y-%m-%d %H:%M UTC'):
    """Write the volume start time in the top-right corner of the axes."""
    ts = None
    try:
        from pyart.util.datetime_utils import datetime_from_radar
        ts = datetime_from_radar(radar).strftime(timestamp_fmt)
    except Exception:
        ts = None
    if ts:
        ax.text(0.99, 0.99, ts, transform=ax.transAxes, ha='right',
                va='top', fontsize='small',
                bbox=dict(facecolor='white', alpha=0.6, edgecolor='none'))


def _with_colorbar_units(fig, field, radar):
    """Attach the field ``units`` to the most recent colorbar, if present."""
    units = None
    try:
        units = radar.fields[field].get('units')
    except (AttributeError, KeyError):
        units = None
    if not units:
        return
    for ax in fig.axes:
        cbar = getattr(ax, 'colorbar', None)
        if cbar is not None and hasattr(cbar, 'set_label'):
            cbar.set_label(units)
            break


def animate_ppi(radars_or_files, field, sweep=0, out='ppi.gif', vmin=None,
                vmax=None, fps=4, title_fmt=None, gatefilter=None,
                display_kwargs=None, template=None):
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
    template : str or None, optional
        Animation template name. Available templates: ``'dualpol'``,
        ``'timeseries'``. When provided, ``figsize`` and ``title_fmt``
        are pre-set from the template unless explicitly overridden.

    Returns
    -------
    out : str
        The output GIF path.

    """
    import pyart

    radars = _to_radars(radars_or_files)
    radars = _check_frames(radars, out)
    display_kwargs = dict(display_kwargs or {})
    display_kwargs = _apply_template(display_kwargs, template)
    if title_fmt is None and template in TEMPLATES:
        title_fmt = TEMPLATES[template].get('title_fmt')

    def plot_frame(i, radar):
        import matplotlib.pyplot as plt
        display = pyart.graph.RadarDisplay(radar)
        figsize = display_kwargs.get('figsize', (8, 8))
        fig = plt.figure(figsize=figsize)
        display.plot(field, sweep, vmin=vmin, vmax=vmax,
                     gatefilter=gatefilter,
                     colorbar_label='', ax=fig.add_subplot(111),
                     **display_kwargs)
        if title_fmt is not None:
            plt.title(title_fmt.format(i=i))
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def _azimuth_to_sweep(radar, azimuth):
    """ Return the sweep index for an RHI cross-section.

    An integer is interpreted directly as a sweep index; a float is
    interpreted as an azimuth in degrees and mapped to the sweep with the
    closest mean azimuth.  ``None`` selects the first sweep.
    """
    if azimuth is None:
        return 0
    if not isinstance(azimuth, bool) and isinstance(azimuth, (int, np.integer)):
        return min(int(azimuth), radar.nsweeps - 1)
    target = float(azimuth) % 360.0
    az_values = np.asarray(radar.azimuth['data'], dtype='float64') % 360.0
    best, best_diff = 0, 361.0
    for sweep in range(radar.nsweeps):
        start, end = radar.get_start_end(sweep)
        mean_az = float(np.median(az_values[start:end]))
        diff = min(abs(mean_az - target), 360.0 - abs(mean_az - target))
        if diff < best_diff:
            best_diff = diff
            best = sweep
    return best


def animate_rhi(radars_or_files, field, azimuth=None, out='rhi.gif', vmin=None,
                vmax=None, fps=4, title_fmt=None, display_kwargs=None):
    """
    Create a GIF animation of RHI cross-sections.

    Parameters are as in :func:`animate_ppi`; ``azimuth`` selects the RHI
    cross-section, either as a sweep index (integer) or an azimuth value in
    degrees (defaults to the first sweep when None).

    """
    import pyart

    radars = _to_radars(radars_or_files)
    radars = _check_frames(radars, out)
    display_kwargs = dict(display_kwargs or {})

    def plot_frame(i, radar):
        import matplotlib.pyplot as plt
        display = pyart.graph.RadarDisplay(radar)
        sweep = _azimuth_to_sweep(radar, azimuth)
        fig = plt.figure(figsize=(10, 6))
        display.plot_rhi(field, sweep, vmin=vmin, vmax=vmax,
                         colorbar_label='', ax=fig.add_subplot(111),
                         **display_kwargs)
        if title_fmt is not None:
            plt.title(title_fmt.format(i=i))
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def animate_map_ppi(radars_or_files, field, sweep=0, out='map.gif', vmin=None,
                    vmax=None, fps=4, title_fmt=None, projection=None,
                    extent=None, display_kwargs=None, cmap=None,
                    resolution='110m', mask_outside=False, lat_lines=None,
                    lon_lines=None, min_lon=None, max_lon=None, min_lat=None,
                    max_lat=None, raster=False, gatefilter=None,
                    shapefile=None, draw_coastline=True, draw_borders=True,
                    show_timestamp=False, timestamp_fmt='%Y-%m-%d %H:%M UTC'):
    """
    Create a GIF animation of PPI sweeps on a projected map (cartopy).

    Parameters
    ----------
    projection : cartopy projection or None, optional
        Defaults to ``cartopy.crs.PlateCarree()``.
    extent : 4-tuple, optional
        Map extent (lon0, lon1, lat0, lat1).
    cmap : str or None, optional
        Colormap name forwarded to ``RadarMapDisplay.plot_ppi_map``.
    resolution : str, optional
        Cartopy feature resolution (e.g. ``'110m'``, ``'50m'``).
    mask_outside : bool, optional
        Mask data outside ``vmin``/``vmax``.
    lat_lines, lon_lines : array or None, optional
        Locations for latitude/longitude grid lines.
    min_lon, max_lon, min_lat, max_lat : float, optional
        Map projection region in degrees.
    raster : bool, optional
        Rasterize the pcolormesh.
    gatefilter : GateFilter or None, optional
        Optional gate filter applied to every frame.
    shapefile : str or None, optional
        Shapefile to overlay.
    draw_coastline, draw_borders : bool, optional
        Add coastline / country borders (cartopy features).
    show_timestamp : bool, optional
        Stamp the volume time in the top-right corner.
    timestamp_fmt : str, optional
        ``strftime`` format for the timestamp.
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
        import matplotlib.pyplot as plt
        display = pyart.graph.RadarMapDisplay(radar)
        fig = plt.figure(figsize=(10, 8))
        ax = fig.add_subplot(111, projection=projection)
        display.plot_ppi_map(
            field, sweep, ax=ax, vmin=vmin, vmax=vmax, cmap=cmap,
            resolution=resolution, mask_outside=mask_outside,
            lat_lines=lat_lines, lon_lines=lon_lines,
            min_lon=min_lon, max_lon=max_lon, min_lat=min_lat,
            max_lat=max_lat, raster=raster, gatefilter=gatefilter,
            shapefile=shapefile, colorbar_label='', **display_kwargs)
        if extent is not None:
            ax.set_extent(extent)
        _draw_basemap_features(ax, draw_coastline, draw_borders)
        if show_timestamp:
            _stamp_time(ax, radar, timestamp_fmt)
        _with_colorbar_units(fig, field, radar)
        if title_fmt is not None:
            plt.title(title_fmt.format(i=i))
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def animate_map_timespan(source, site, start, end, step, field='reflectivity',
                         sweep=0, out='timespan.gif', fps=4, cmap=None,
                         resolution='110m', mask_outside=False, lat_lines=None,
                         lon_lines=None, min_lon=None, max_lon=None,
                         min_lat=None, max_lat=None, draw_coastline=True,
                         draw_borders=True, show_timestamp=True,
                         timestamp_fmt='%Y-%m-%d %H:%M UTC',
                         title_fmt='{site} {time}', template=None,
                         gatefilter=None, share_colorbar=True,
                         display_kwargs=None):
    """
    Pull data for a time span from a remote source and make a map GIF.

    This composes :func:`pyart.io.read_time_span` with :func:`animate_map_ppi`,
    mirroring the ``zssherman/pyart_animation`` workflow of *list a time span*
    -> *read each volume* -> *animate onto a geographic map*.

    Parameters
    ----------
    source : str or RadarSource
        Source name (e.g. ``'nexrad'``) or instance.
    site : str
        Site identifier.
    start, end : datetime.datetime
        Time range. ``end='now'`` resolves to the current UTC time.
    step : datetime.timedelta
        Cadence between scans.
    title_fmt : str or None, optional
        Title template with ``{site}`` and ``{time}`` placeholders.
    share_colorbar : bool, optional
        Unused for a single field; kept for API symmetry with
        :func:`animate_map_ppi`.
    Others as in :func:`animate_map_ppi`.

    Returns
    -------
    out : str
        The output GIF path.

    """
    import pyart

    if template in TEMPLATES and title_fmt == '{site} {time}':
        title_fmt = TEMPLATES[template].get('title_fmt', title_fmt)

    radars = pyart.io.read_time_span(source, site, start, end, step)
    return animate_map_ppi(
        radars, field, sweep=sweep, out=out, fps=fps, cmap=cmap,
        resolution=resolution, mask_outside=mask_outside,
        lat_lines=lat_lines, lon_lines=lon_lines,
        min_lon=min_lon, max_lon=max_lon, min_lat=min_lat, max_lat=max_lat,
        draw_coastline=draw_coastline, draw_borders=draw_borders,
        show_timestamp=show_timestamp, timestamp_fmt=timestamp_fmt,
        title_fmt=title_fmt, gatefilter=gatefilter,
        display_kwargs=display_kwargs)


def animate_ppi_batch(files, field, out_dir='.', sweep=0, fps=4,
                       template=None, **kwargs):
    """
    Batch-generate PPI GIFs from a list of files or a glob pattern.

    Parameters
    ----------
    files : list or str
        List of file paths, or a glob pattern string.
    field : str
        Field to plot, e.g. ``'reflectivity'``.
    out_dir : str, optional
        Output directory for GIF files.
    sweep : int, optional
        Sweep number to display.
    fps : int, optional
        Frames per second.
    template : str or None, optional
        Animation template name.
    **kwargs
        Extra keyword arguments forwarded to :func:`animate_ppi`.

    Returns
    -------
    report : dict
        Dictionary with ``success``, ``failed``, and ``outputs`` lists.

    """
    import glob as glob_module
    import pyart

    if isinstance(files, str):
        files = sorted(glob_module.glob(files))
    if len(files) == 0:
        raise ValueError('No files matched for batch animation')

    os.makedirs(out_dir, exist_ok=True)
    success = []
    failed = []
    for filepath in files:
        try:
            radar = pyart.io.read(filepath)
            name = os.path.splitext(os.path.basename(filepath))[0]
            out = os.path.join(out_dir, '{0}_{1}.gif'.format(name, field))
            animate_ppi([radar], field, sweep=sweep, out=out, fps=fps,
                        template=template, **kwargs)
            success.append(out)
        except Exception as exc:
            failed.append({'file': filepath, 'error': str(exc)})
    return {'success': success, 'failed': failed}


def animate_multi_band(radars_by_band, field, out='multi_band.gif',
                        sweep=0, vmin=None, vmax=None, fps=4,
                        title_fmt=None, display_kwargs=None,
                        share_colorbar=True):
    """
    Create a side-by-side GIF comparing multiple radar bands.

    Parameters
    ----------
    radars_by_band : dict
        Mapping of band name to Radar object, e.g.
        ``{'S': s_radar, 'C': c_radar, 'X': x_radar}``.
    field : str
        Field to plot.
    out : str, optional
        Output GIF path.
    sweep : int, optional
        Sweep number to display.
    vmin, vmax : float, optional
        Color limits. When ``share_colorbar`` is True, these apply to all
        bands.
    fps : int, optional
        Frames per second.
    title_fmt : str or None, optional
        Title template with a ``{band}`` placeholder, e.g.
        ``'{band} band - {field}'``.
    display_kwargs : dict or None, optional
        Extra keyword arguments forwarded to ``RadarDisplay.plot``.
    share_colorbar : bool, optional
        If True, all bands share the same colorbar limits.

    Returns
    -------
    out : str
        The output GIF path.

    """
    import pyart

    bands = list(radars_by_band.keys())
    if len(bands) == 0:
        raise ValueError('radars_by_band must contain at least one band')
    n_bands = len(bands)

    display_kwargs = dict(display_kwargs or {})

    def plot_frame(i, radar_by_band):
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, n_bands, figsize=(5 * n_bands, 5),
                                 squeeze=False)
        axes = axes[0]
        for ax, band in zip(axes, bands):
            radar = radar_by_band[band]
            display = pyart.graph.RadarDisplay(radar)
            band_kwargs = dict(display_kwargs)
            if share_colorbar:
                band_kwargs.update({'vmin': vmin, 'vmax': vmax})
            display.plot(field, sweep, colorbar_label='', ax=ax,
                         **band_kwargs)
            ax.set_title(title_fmt.format(band=band, field=field)
                         if title_fmt else '{0} band'.format(band))
        fig.tight_layout()
        return fig

    radars_list = [radars_by_band]
    return _collect_frames(plot_frame, radars_list, out, fps)


__all__ = [
    'animate_ppi',
    'animate_rhi',
    'animate_map_ppi',
    'animate_map_timespan',
    'animate_ppi_batch',
    'animate_multi_band',
]