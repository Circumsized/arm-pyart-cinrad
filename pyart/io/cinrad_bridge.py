"""
pyart.io.cinrad_bridge
======================

Read Chinese CINRAD files through PyCINRAD and convert them to
:class:`pyart.core.Radar`.

Requires the optional ``cinrad`` dependency (PyCINRAD)::

    pip install arm_pyart[cinrad]

"""

import numpy as np


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


def read_cinrad(filename, radius=460, station=None, use_standard=True,
                align_gates=True):
    """
    Read a CINRAD file using PyCINRAD and return a :class:`pyart.core.Radar`.

    Parameters
    ----------
    filename : str
        Path to a CINRAD base-data file (SA/SB/CB/CC/SC/CD, WSR98D,
        phased-array standard data, or SWAN).
    radius : int, optional
        Maximum range to read, in kilometres.
    station : 3-tuple of float, optional
        (latitude, longitude, altitude) override. When None, the station
        location stored in the file is used.
    use_standard : bool, optional
        Try ``StandardData`` first and fall back to ``CinradReader``.
    align_gates : bool, optional
        Pad shorter fields to the maximum number of gates so all fields share
        a common range axis.

    Returns
    -------
    radar : pyart.core.Radar
        Radar object. Dual-polarization fields (ZDR/RHOHV/PhiDP/KDP) are
        included when present in the file.

    """
    CinradReader, StandardData, standard_data_to_pyart = _import_cinrad()

    if hasattr(filename, 'read'):
        raise TypeError('read_cinrad requires a filename string, not a '
                        'file-like object')

    if use_standard:
        try:
            cinrad_obj = StandardData(filename)
        except Exception:
            cinrad_obj = CinradReader(filename)
    else:
        cinrad_obj = CinradReader(filename)

    radar = standard_data_to_pyart(cinrad_obj, radius=radius)

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