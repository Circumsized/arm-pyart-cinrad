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
    "dualpol": {
        "fields": [
            "reflectivity",
            "differential_reflectivity",
            "cross_correlation_ratio",
            "specific_differential_phase",
        ],
        "figsize": (12, 12),
        "title_fmt": "{field} - {i}",
    },
    "timeseries": {
        "fields": ["reflectivity"],
        "figsize": (10, 8),
        "title_fmt": "{time}",
    },
    "dualpol_map": {
        "fields": [
            "reflectivity",
            "differential_reflectivity",
            "cross_correlation_ratio",
            "specific_differential_phase",
        ],
        "figsize": (12, 12),
        "title_fmt": "{field} - {i}",
    },
    "timespan": {
        "fields": ["reflectivity"],
        "figsize": (10, 8),
        "title_fmt": "{site} {time}",
    },
}


def _apply_template(kwargs, template):
    """Return ``(display_kwargs, figsize, title_fmt)`` from a template.

    Template-derived ``figsize`` and ``title_fmt`` are returned separately so
    they are never forwarded into the plotting kwargs (which would crash
    ``pcolormesh`` with an unexpected keyword argument). Explicit values in
    ``kwargs`` take precedence over template presets.
    """
    if template is None or template not in TEMPLATES:
        return kwargs, None, None
    preset = TEMPLATES[template]
    out = dict(kwargs)
    figsize = out.pop("figsize", preset.get("figsize"))
    title_fmt = out.pop("title_fmt", preset.get("title_fmt"))
    return out, figsize, title_fmt


def _import_imageio():
    try:
        import imageio
    except ImportError as exc:
        raise ImportError(
            "imageio is required for animations; install it with "
            '"pip install arm_pyart[animation]"'
        ) from exc
    return imageio


def _iter_radars(radars_or_files):
    """Yield Radar objects, reading paths lazily.

    The previous implementation eagerly materialised the whole list via
    :func:`pyart.io.read` for every entry *before* a single frame was drawn.
    Combined with the 200-frame ``MAX_FRAMES`` cap and a 64+ MB per-radar
    memory footprint, that turned "stream frames to disk" into a lie:
    hundreds of Radar objects stayed resident in RAM, so :func:`_free_radar`
    could not free anything. Laziness keeps only the current radar alive,
    which actually lets :func:`_free_radar` recover memory and keeps the
    long timespan cases that the docstring promises within reasonable RAM.
    """
    import pyart

    for item in radars_or_files:
        if hasattr(item, "fields"):
            yield item
        else:
            yield pyart.io.read(item)


def _to_radars(radars_or_files):
    """Materialise a Radar/file iterable into a list.

    Thin compatibility shim preserved for callers and tests that legitimately
    need the full list (e.g. counting frames, asserting on a specific entry).
    """
    return list(_iter_radars(radars_or_files))


def _check_frames(radars, out):
    """Validate output directory; return a *count* when cheap, else ``None``.

    The previous version materialised the iterable into a truncated list, which
    defeated the streaming contract. We now return the count when it is cheap
    (``list`` / ``tuple``) and otherwise leave the iteration alone so the
    stream can take over.
    """
    directory = os.path.dirname(os.path.abspath(out))
    if not os.path.isdir(directory):
        raise ValueError(f"Output directory does not exist: {directory}")
    if isinstance(radars, (list, tuple)):
        if len(radars) == 0:
            raise ValueError("No radar frames provided")
        return len(radars)
    return None


def _iter_or_list(radars):
    """Return a sized list, draining a generator if necessary.

    Used only by the public entry points so the "empty input raises
    ValueError" contract is preserved without paying the cost twice (the
    streaming path does not need a sized collection).
    """
    if isinstance(radars, (list, tuple)):
        return radars
    return list(radars)


class _PeekedFirst:
    """Stream-like wrapper that yields ``head`` first, then ``tail``.

    Lets a public entry point honour "empty input raises ValueError" **and**
    hand the rest of the iterator to :func:`_collect_frames` so the 200-frame
    truncation, ``_free_radar`` memory hygiene and streaming imageio writer
    still apply. Without this, every public function would have to materialise
    the full list just to discover it is non-empty, which is exactly the
    defect that was hiding behind ``_to_radars``.
    """

    __slots__ = ("_head", "_tail", "_exhausted")

    def __init__(self, head, tail):
        self._head = head
        self._tail = tail
        self._exhausted = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._head is not None:
            value = self._head
            self._head = None
            return value
        if self._exhausted:
            raise StopIteration
        try:
            return next(self._tail)
        except StopIteration:
            self._exhausted = True
            raise

    def __getitem__(self, index):
        """Support ``radars[0]`` and similar subscript access.

        ``_PeekedFirst`` is a streaming wrapper, so the only safe indices
        are non-negative integers relative to the already-yielded head
        (``0`` returns the head frame; ``1+`` advances through the tail).
        Slices and negative indices raise :class:`TypeError` because we
        cannot honour them without materialising the entire stream —
        callers that need random access should call :func:`_iter_or_list`
        on the input first.
        """
        if isinstance(index, slice):
            raise TypeError(
                "_PeekedFirst does not support slicing; use _iter_or_list "
                "if you need indexed/len access to every frame"
            )
        if not isinstance(index, int):
            raise TypeError(
                f"_PeekedFirst indices must be int, not {type(index).__name__}"
            )
        if index < 0:
            raise TypeError(
                "_PeekedFirst does not support negative indices; use "
                "_iter_or_list if you need random access"
            )
        # Stream through self until the requested index is reached.
        for offset, value in enumerate(self):
            if offset == index:
                return value
        raise IndexError(f"_PeekedFirst index {index} out of range")


def _check_non_empty(radars_or_files):
    """Validate that ``radars_or_files`` is non-empty.

    Returns a :class:`_PeekedFirst` that yields the pre-validated first frame
    followed by the rest of the stream, so the animation pipeline keeps its
    streaming contract (and per-frame memory hygiene) end-to-end. Output
    directory validation lives in :func:`_check_frames`.
    """
    iterator = _iter_radars(radars_or_files)
    try:
        first = next(iterator)
    except StopIteration:
        raise ValueError("No radar frames provided")
    return _PeekedFirst(first, iterator)


def _collect_frames(plot_frame, radars, out, fps, max_frames=MAX_FRAMES):
    imageio = _import_imageio()

    # Stream each frame to disk with get_writer (v2) so we never hold every
    # RGBA buffer in memory (200 frames ~ >1 GB otherwise). imageio v3 uses
    # a plugin-based writer instead.
    try:
        writer = imageio.get_writer(out, fps=fps, loop=0)
    except TypeError:
        import imageio.v3 as iio3

        writer = iio3.imopen(out, "w", plugin="pillow", fps=fps, loop=0)

    with matplotlib.rc_context({"backend": "Agg"}):
        import matplotlib.pyplot as plt

        truncated = False
        with writer:
            for i, radar in enumerate(radars):
                if i >= max_frames:
                    truncated = True
                    break
                with _free_radar(radar) as radar:
                    fig = plot_frame(i, radar)
                    fig.canvas.draw()
                    buf = np.asarray(fig.canvas.buffer_rgba())
                    writer.append_data(buf)
                    plt.close(fig)
    if truncated:
        warnings.warn(f"Truncating animation at MAX_FRAMES={max_frames} frames")
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


def _radar_time_str(radar, fmt="%Y-%m-%d %H:%M UTC"):
    """Return a formatted volume start time, or '' when unavailable."""
    try:
        from pyart.util.datetime_utils import datetime_from_radar

        return datetime_from_radar(radar).strftime(fmt)
    except Exception:
        return ""


def _format_title(title_fmt, **context):
    """Format a title template with a context dict, never raising KeyError.

    Templates may reference ``{field}``, ``{i}``, ``{time}``, ``{site}`` or
    ``{band}``; any placeholder missing from the provided context is rendered
    as an empty string instead of crashing the animation.
    """
    if title_fmt is None:
        return None
    try:
        return title_fmt.format(**context)
    except (KeyError, IndexError, ValueError):
        import string

        class _Safe(string.Formatter):
            def get_field(self, field_name, args, kwargs):
                try:
                    return super().get_field(field_name, args, kwargs)
                except (KeyError, IndexError):
                    return "", field_name

        return _Safe().format(title_fmt, **context)


def _draw_basemap_features(ax, draw_coastline=True, draw_borders=True):
    """Add coastline and country-border features to a cartopy GeoAxes.

    This is the cartopy equivalent of the Basemap-era
    ``display.basemap.drawcounties()`` call from zssherman/pyart_animation.
    """
    import cartopy.feature as cfeature

    if draw_coastline:
        ax.add_feature(cfeature.COASTLINE.with_scale("50m"))
    if draw_borders:
        ax.add_feature(cfeature.BORDERS.with_scale("50m"))


def _stamp_time(ax, radar, timestamp_fmt="%Y-%m-%d %H:%M UTC"):
    """Write the volume start time in the top-right corner of the axes."""
    ts = None
    try:
        from pyart.util.datetime_utils import datetime_from_radar

        ts = datetime_from_radar(radar).strftime(timestamp_fmt)
    except Exception:
        ts = None
    if ts:
        ax.text(
            0.99,
            0.99,
            ts,
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize="small",
            bbox=dict(facecolor="white", alpha=0.6, edgecolor="none"),
        )


def _with_colorbar_units(fig, field, radar):
    """Attach the field ``units`` to the most recent colorbar, if present.

    Matplotlib attaches the colorbar to the *mappable* (QuadMesh, ScalarMappable)
    rather than to the axes, so scan every mappable in the figure for a
    ``colorbar`` attribute instead of ``ax.colorbar``.
    """
    units = None
    try:
        units = radar.fields[field].get("units")
    except (AttributeError, KeyError):
        units = None
    if not units:
        return
    for ax in fig.axes:
        mappables = list(getattr(ax, "collections", []))
        mappables += list(getattr(ax, "images", []))
        for m in mappables:
            cbar = getattr(m, "colorbar", None)
            if cbar is not None and hasattr(cbar, "set_label"):
                cbar.set_label(units)
                return


def animate_ppi(
    radars_or_files,
    field,
    sweep=0,
    out="ppi.gif",
    vmin=None,
    vmax=None,
    fps=4,
    title_fmt=None,
    gatefilter=None,
    display_kwargs=None,
    template=None,
):
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

    radars = _check_non_empty(radars_or_files)
    _check_frames(radars, out)  # validate dir + count
    # The 200-frame cap is enforced inside ``_collect_frames`` (it breaks
    # out of the iteration loop) so the stream contract is preserved; we
    # used to slice here, which forced an eager materialisation.
    display_kwargs = dict(display_kwargs or {})
    display_kwargs, t_figsize, t_title_fmt = _apply_template(display_kwargs, template)
    if title_fmt is None:
        title_fmt = t_title_fmt

    def plot_frame(i, radar):
        import matplotlib.pyplot as plt

        display = pyart.graph.RadarDisplay(radar)
        figsize = t_figsize or (8, 8)
        fig = plt.figure(figsize=figsize)
        display.plot(
            field,
            sweep,
            vmin=vmin,
            vmax=vmax,
            gatefilter=gatefilter,
            colorbar_label="",
            ax=fig.add_subplot(111),
            **display_kwargs,
        )
        title = _format_title(title_fmt, i=i, field=field, time=_radar_time_str(radar))
        if title is not None:
            plt.title(title)
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def _azimuth_to_sweep(radar, azimuth):
    """Return the sweep index for an RHI cross-section.

    An integer is interpreted directly as a sweep index; a float is
    interpreted as an azimuth in degrees and mapped to the sweep with the
    closest mean azimuth.  ``None`` selects the first sweep.
    """
    if azimuth is None:
        return 0
    if not isinstance(azimuth, bool) and isinstance(azimuth, (int, np.integer)):
        return max(0, min(int(azimuth), radar.nsweeps - 1))
    target = float(azimuth) % 360.0
    az_values = np.asarray(radar.azimuth["data"], dtype="float64") % 360.0
    best, best_diff = 0, 361.0
    for sweep in range(radar.nsweeps):
        start, end = radar.get_start_end(sweep)
        mean_az = float(np.median(az_values[start:end]))
        diff = min(abs(mean_az - target), 360.0 - abs(mean_az - target))
        if diff < best_diff:
            best_diff = diff
            best = sweep
    return best


def animate_rhi(
    radars_or_files,
    field,
    azimuth=None,
    out="rhi.gif",
    vmin=None,
    vmax=None,
    fps=4,
    title_fmt=None,
    display_kwargs=None,
):
    """
    Create a GIF animation of RHI cross-sections.

    Parameters are as in :func:`animate_ppi`; ``azimuth`` selects the RHI
    cross-section, either as a sweep index (integer) or an azimuth value in
    degrees (defaults to the first sweep when None).

    """
    import pyart

    radars = _check_non_empty(radars_or_files)
    _check_frames(radars, out)  # validate dir + count
    # The 200-frame cap is enforced inside ``_collect_frames`` (it breaks
    # out of the iteration loop) so the stream contract is preserved; we
    # used to slice here, which forced an eager materialisation.
    display_kwargs = dict(display_kwargs or {})

    def plot_frame(i, radar):
        import matplotlib.pyplot as plt

        display = pyart.graph.RadarDisplay(radar)
        sweep = _azimuth_to_sweep(radar, azimuth)
        fig = plt.figure(figsize=(10, 6))
        display.plot_rhi(
            field,
            sweep,
            vmin=vmin,
            vmax=vmax,
            colorbar_label="",
            ax=fig.add_subplot(111),
            **display_kwargs,
        )
        title = _format_title(title_fmt, i=i, field=field, time=_radar_time_str(radar))
        if title is not None:
            plt.title(title)
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def animate_map_ppi(
    radars_or_files,
    field,
    sweep=0,
    out="map.gif",
    vmin=None,
    vmax=None,
    fps=4,
    title_fmt=None,
    projection=None,
    extent=None,
    display_kwargs=None,
    cmap=None,
    resolution="110m",
    mask_outside=False,
    lat_lines=None,
    lon_lines=None,
    min_lon=None,
    max_lon=None,
    min_lat=None,
    max_lat=None,
    raster=False,
    gatefilter=None,
    shapefile=None,
    draw_coastline=True,
    draw_borders=True,
    show_timestamp=False,
    timestamp_fmt="%Y-%m-%d %H:%M UTC",
):
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
            "cartopy is required for map animations; install it with "
            '"pip install cartopy"'
        ) from exc

    radars = _check_non_empty(radars_or_files)
    _check_frames(radars, out)  # validate dir + count
    # The 200-frame cap is enforced inside ``_collect_frames`` (it breaks
    # out of the iteration loop) so the stream contract is preserved; we
    # used to slice here, which forced an eager materialisation.
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
            field,
            sweep,
            ax=ax,
            vmin=vmin,
            vmax=vmax,
            cmap=cmap,
            resolution=resolution,
            mask_outside=mask_outside,
            lat_lines=lat_lines,
            lon_lines=lon_lines,
            min_lon=min_lon,
            max_lon=max_lon,
            min_lat=min_lat,
            max_lat=max_lat,
            raster=raster,
            gatefilter=gatefilter,
            shapefile=shapefile,
            colorbar_label="",
            **display_kwargs,
        )
        if extent is not None:
            ax.set_extent(extent)
        _draw_basemap_features(ax, draw_coastline, draw_borders)
        if show_timestamp:
            _stamp_time(ax, radar, timestamp_fmt)
        _with_colorbar_units(fig, field, radar)
        title = _format_title(title_fmt, i=i, field=field, time=_radar_time_str(radar))
        if title is not None:
            plt.title(title)
        return fig

    return _collect_frames(plot_frame, radars, out, fps)


def animate_map_timespan(
    source,
    site,
    start,
    end,
    step,
    field="reflectivity",
    sweep=0,
    out="timespan.gif",
    fps=4,
    cmap=None,
    resolution="110m",
    mask_outside=False,
    lat_lines=None,
    lon_lines=None,
    min_lon=None,
    max_lon=None,
    min_lat=None,
    max_lat=None,
    draw_coastline=True,
    draw_borders=True,
    show_timestamp=True,
    timestamp_fmt="%Y-%m-%d %H:%M UTC",
    title_fmt="{site} {time}",
    template=None,
    gatefilter=None,
    share_colorbar=True,
    display_kwargs=None,
):
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

    if template in TEMPLATES and title_fmt == "{site} {time}":
        title_fmt = TEMPLATES[template].get("title_fmt", title_fmt)

    radars = pyart.io.read_time_span(source, site, start, end, step)
    return animate_map_ppi(
        radars,
        field,
        sweep=sweep,
        out=out,
        fps=fps,
        cmap=cmap,
        resolution=resolution,
        mask_outside=mask_outside,
        lat_lines=lat_lines,
        lon_lines=lon_lines,
        min_lon=min_lon,
        max_lon=max_lon,
        min_lat=min_lat,
        max_lat=max_lat,
        draw_coastline=draw_coastline,
        draw_borders=draw_borders,
        show_timestamp=show_timestamp,
        timestamp_fmt=timestamp_fmt,
        title_fmt=title_fmt,
        gatefilter=gatefilter,
        display_kwargs=display_kwargs,
    )


def animate_ppi_batch(
    files, field, out_dir=".", sweep=0, fps=4, template=None, **kwargs
):
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
        Dictionary with two entries: ``success`` -- list of generated GIF
        paths; ``failed`` -- list of ``{'file': path, 'error': message}``
        dicts.

    """
    import glob as glob_module

    import pyart

    if isinstance(files, str):
        files = sorted(glob_module.glob(files))
    if len(files) == 0:
        raise ValueError("No files matched for batch animation")

    os.makedirs(out_dir, exist_ok=True)
    success = []
    failed = []
    for filepath in files:
        try:
            radar = pyart.io.read(filepath)
            name = os.path.splitext(os.path.basename(filepath))[0]
            out = os.path.join(out_dir, f"{name}_{field}.gif")
            animate_ppi(
                [radar],
                field,
                sweep=sweep,
                out=out,
                fps=fps,
                template=template,
                **kwargs,
            )
            success.append(out)
        except Exception as exc:
            failed.append({"file": filepath, "error": str(exc)})
    return {"success": success, "failed": failed}


def animate_multi_band(
    radars_by_band,
    field,
    out="multi_band.gif",
    sweep=0,
    vmin=None,
    vmax=None,
    fps=4,
    title_fmt=None,
    display_kwargs=None,
    share_colorbar=True,
):
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
        raise ValueError("radars_by_band must contain at least one band")
    n_bands = len(bands)

    display_kwargs = dict(display_kwargs or {})

    def plot_frame(i, radar_by_band):
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, n_bands, figsize=(5 * n_bands, 5), squeeze=False)
        axes = axes[0]
        for ax, band in zip(axes, bands):
            radar = radar_by_band[band]
            display = pyart.graph.RadarDisplay(radar)
            band_kwargs = dict(display_kwargs)
            if share_colorbar:
                band_kwargs.update({"vmin": vmin, "vmax": vmax})
            display.plot(field, sweep, colorbar_label="", ax=ax, **band_kwargs)
            title = _format_title(
                title_fmt, band=band, field=field, i=i, time=_radar_time_str(radar)
            )
            ax.set_title(title or f"{band} band")
        fig.tight_layout()
        return fig

    radars_list = [radars_by_band]
    return _collect_frames(plot_frame, radars_list, out, fps)


__all__ = [
    "animate_ppi",
    "animate_rhi",
    "animate_map_ppi",
    "animate_map_timespan",
    "animate_ppi_batch",
    "animate_multi_band",
]
