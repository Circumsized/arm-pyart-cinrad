"""
pyart.io.cinrad_bridge
======================

Read Chinese CINRAD files through PyCINRAD and convert them to
:class:`pyart.core.Radar`.

Requires the optional ``cinrad`` dependency (PyCINRAD)::

    pip install arm_pyart[cinrad]

"""

import os

import numpy as np

XBAND_FILENAME_PATTERNS = ('AXPT', 'DXK', 'XAD', 'XCD', 'XSP')

XBAND_DEFAULT_RADIUS = 150
CBAND_DEFAULT_RADIUS = 230
SBAND_DEFAULT_RADIUS = 460


def is_xband_filename(filename):
    """ Return True when the filename looks like a CINRAD X-band file. """
    name = os.path.basename(filename).upper()
    return any(pattern in name for pattern in XBAND_FILENAME_PATTERNS)


def _read_via_pywr(filename):
    """ Read a CINRAD file through pycwr, returning a pyart.core.Radar. """
    try:
        from pycwr.io import read_auto
        from pycwr.reader import standard_data_to_pyart as pycwr_to_pyart
    except ImportError as exc:
        raise ImportError(
            'pycwr is required for the pycwr backend; install it with '
            '"pip install arm_pyart[cinrad]"') from exc
    return pycwr_to_pyart(read_auto(filename))


def _detect_reader(filename):
    """ Return a callable that opens ``filename`` with PyCINRAD. """
    def _reader():
        CinradReader, StandardData, _ = _import_cinrad()
        try:
            return StandardData(filename)
        except Exception:
            return CinradReader(filename)
    return _reader


def read_xband(filename, radius=XBAND_DEFAULT_RADIUS, station=None,
                align_gates=True):
    """
    Read a CINRAD X-band base-data file, falling back to pycwr.

    Parameters
    ----------
    filename : str
        Path to an X-band (AXPT/DXK/XAD/XCD/XSP) base-data file.
    radius : int, optional
        Maximum range to read, in kilometres. Defaults to 150 km.
    station : 3-tuple of float, optional
        (latitude, longitude, altitude) override.
    align_gates : bool, optional
        Pad shorter fields to a common range axis.

    Returns
    -------
    radar : pyart.core.Radar
        Radar object tagged with ``radar_band='X'`` metadata.

    """
    try:
        reader = _detect_reader(filename)
        cinrad_obj = reader()
        _, _, to_pyart = _import_cinrad()
        radar = to_pyart(cinrad_obj, radius=radius)
    except ValueError:
        if not is_xband_filename(filename):
            raise
        radar = _read_via_pywr(filename)
    except ImportError:
        raise

    if station is not None:
        lat, lon, alt = station
        radar.latitude['data'] = np.array([lat], dtype='float64')
        radar.longitude['data'] = np.array([lon], dtype='float64')
        radar.altitude['data'] = np.array([alt], dtype='float64')

    if align_gates:
        align_range_gates(radar)

    radar.metadata['radar_band'] = 'X'
    radar.metadata['original_container'] = 'CINRAD-X'
    return radar


def _import_cinrad():
    """ Return the PyCINRAD reader and exporter entry points. """
    try:
        from cinrad.io import CinradReader, StandardData
        from cinrad.io.export import standard_data_to_pyart
    except ImportError as exc:
        raise ImportError(
            'PyCINRAD is required for read_cinrad; install it with '
            '"pip install arm_pyart[cinrad]"') from exc
    return CinradReader, StandardData, standard_data_to_pyart


def read_cinrad(filename, radius=SBAND_DEFAULT_RADIUS, station=None,
                use_standard=True, align_gates=True, band=None, reader=None):
    """
    Read a CINRAD file using PyCINRAD/pycwr and return a :class:`pyart.core.Radar`.

    Parameters
    ----------
    filename : str
        Path to a CINRAD base-data file (SA/SB/CB/CC/SC/CD, WSR98D,
        phased-array standard data, or SWAN).
    radius : int, optional
        Maximum range to read, in kilometres. Defaults depend on ``band``.
    station : 3-tuple of float, optional
        (latitude, longitude, altitude) override. When None, the station
        location stored in the file is used.
    use_standard : bool, optional
        Try ``StandardData`` first and fall back to ``CinradReader``.
    align_gates : bool, optional
        Pad shorter fields to the maximum number of gates so all fields share
        a common range axis.
    band : str or None, optional
        Band hint ``'S'``, ``'C'``, or ``'X'``. When provided, the default
        ``radius`` is chosen as 460 km for S-band, 230 km for C-band, and
        150 km for X-band.
    reader : str or None, optional
        Force a specific reader backend: ``'cinrad'`` or ``'pycwr'``.
        When None, PyCINRAD is tried first and pycwr is used as fallback.

    Returns
    -------
    radar : pyart.core.Radar
        Radar object. Dual-polarization fields (ZDR/RHOHV/PhiDP/KDP) are
        included when present in the file.

    """
    if band is not None:
        band = band.upper()
        if radius == SBAND_DEFAULT_RADIUS:
            radius = {
                'S': SBAND_DEFAULT_RADIUS,
                'C': CBAND_DEFAULT_RADIUS,
                'X': XBAND_DEFAULT_RADIUS,
            }.get(band, radius)

    backend = reader or 'cinrad'
    radar = None

    if backend in ('cinrad', None):
        try:
            CinradReader, StandardData, standard_data_to_pyart = _import_cinrad()
            if use_standard:
                try:
                    cinrad_obj = StandardData(filename)
                except Exception:
                    cinrad_obj = CinradReader(filename)
            else:
                cinrad_obj = CinradReader(filename)
            radar = standard_data_to_pyart(cinrad_obj, radius=radius)
        except Exception:
            if backend == 'cinrad':
                radar = None
            raise

    if radar is None and backend in ('pycwr', None):
        try:
            from pycwr.io import read_auto
            from pycwr.reader import standard_data_to_pyart as pycwr_to_pyart
            cinrad_obj = read_auto(filename)
            radar = pycwr_to_pyart(cinrad_obj, radius=radius)
        except ImportError as exc:
            raise ImportError(
                'pycwr is required for the pycwr backend; install it with '
                '"pip install arm_pyart[cinrad]"') from exc
        except Exception:
            raise

    if radar is None:
        raise IOError('Failed to read CINRAD file with available backends')

    if station is not None:
        lat, lon, alt = station
        radar.latitude['data'] = np.array([lat], dtype='float64')
        radar.longitude['data'] = np.array([lon], dtype='float64')
        radar.altitude['data'] = np.array([alt], dtype='float64')

    if align_gates:
        align_range_gates(radar)

    return radar


def align_range_gates(radar):
    """
    Pad every field to the maximum number of gates and rebuild the range axis.

    PyCINRAD's base-data conversion can yield fields with differing gate
    counts (reflectivity uses the log resolution while Doppler moments use
    the Doppler resolution). This helper masks shorter fields to a common
    gate axis so plotting and gridding do not break.

    Parameters
    ----------
    radar : Radar
        Radar object modified in place.

    Returns
    -------
    radar : Radar
        The same Radar object.

    """
    if not radar.fields or len(radar.fields) == 0:
        return radar

    max_ngates = max(dic['data'].shape[1] for dic in radar.fields.values())

    rng = np.asarray(radar.range['data'], dtype='float64')
    if rng.size < max_ngates:
        spacing = rng[-1] - rng[-2] if rng.size >= 2 else 1.0
        appended = rng[-1] + spacing + spacing * np.arange(max_ngates - rng.size)
        radar.range['data'] = np.concatenate([rng, appended])
        radar.range['meters_between_gates'] = float(spacing)

    for dic in radar.fields.values():
        data = dic['data']
        ngates = data.shape[1]
        if ngates == max_ngates:
            continue
        if max_ngates % ngates == 0:
            repeat = max_ngates // ngates
            dic['data'] = data.repeat(repeat, axis=1)
        else:
            pad = np.ma.masked_all((data.shape[0], max_ngates - ngates))
            dic['data'] = np.ma.hstack([data, pad])

    radar.ngates = max_ngates
    return radar


def read_pa(filename, radius=XBAND_DEFAULT_RADIUS, station=None,
            align_gates=True):
    """
    Read a CINRAD phased-array file (AXPT/DXK) and return a Radar.

    Parameters
    ----------
    filename : str
        Path to a phased-array standard-data file.
    radius : int, optional
        Maximum range in kilometres.
    station : 3-tuple of float, optional
        (latitude, longitude, altitude) override.
    align_gates : bool, optional
        Pad shorter fields to a common range axis.

    Returns
    -------
    radar : pyart.core.Radar

    """
    return read_cinrad(
        filename, radius=radius, station=station,
        align_gates=align_gates, band='X', reader='pycwr')


def read_mocmosaic(filename, product=None):
    """
    Read a CINRAD MocMosaic composite product and return a Radar or Grid.

    Parameters
    ----------
    filename : str
        Path to a MocMosaic product file.
    product : str or None, optional
        Product hint (``'CREF'``, ``'ET'``, ``'VIL'``).

    Returns
    -------
    radar_or_grid : pyart.core.Radar or pyart.core.Grid

    """
    try:
        from pycwr.io import read_auto
        from pycwr.reader import standard_data_to_pyart as pycwr_to_pyart
        from pycwr.grid import get_griddata as pycwr_grid
    except ImportError as exc:
        raise ImportError(
            'pycwr is required for read_mocmosaic; install it with '
            '"pip install arm_pyart[cinrad]"') from exc

    cinrad_obj = read_auto(filename)
    if product and product.upper() in ('CREF', 'ET', 'VIL'):
        return pycwr_grid(cinrad_obj, product=product.upper())
    return pycwr_to_pyart(cinrad_obj)