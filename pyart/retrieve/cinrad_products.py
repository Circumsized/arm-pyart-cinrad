"""
pyart.retrieve.cinrad_products
==============================

Retrieval products for CINRAD radar objects, backed by pycwr (hydrometeor
classification) and PyCINRAD (column/volume-integrated products).

"""

import numpy as np

from ..config import get_field_name, get_fillvalue


def _import_classifier():
    try:
        from pycwr.retrieve import classify_hydrometeors
    except ImportError as exc:
        raise ImportError(
            'pycwr is required for hydro_class; install it with '
            '"pip install arm_pyart[cinrad]"') from exc
    return classify_hydrometeors


def _get(radar, key):
    name = get_field_name(key)
    return radar.fields.get(name)


def _pad_to(radar, arr):
    ngates = radar.ngates
    if arr is None or arr.shape[1] == ngates:
        return arr
    pad = np.full((arr.shape[0], ngates - arr.shape[1]), np.nan)
    return np.hstack([arr, pad])


def hydro_class(radar, band='C', method='hybrid', **kwargs):
    """
    Fuzzy-logic hydrometeor classification via pycwr.

    Parameters
    ----------
    radar : Radar
        Radar object containing reflectivity and at least one of ZDR, KDP or
        the correlation coefficient.
    band : {'S', 'C', 'X'}, optional
        Radar band used to select the membership beta parameters.
    method : {'hybrid', 'linear'}, optional
        Weighting scheme.

    Returns
    -------
    classification : numpy.ma.MaskedArray
        Class ids in ``[1, 10]`` with masked unclassified gates. The field
        ``hydrometeor_classification`` is also added to ``radar``.

    """
    classify = _import_classifier()

    refl = _get(radar, 'reflectivity')
    zdr = _get(radar, 'differential_reflectivity')
    kdp = _get(radar, 'specific_differential_phase')
    cc = _get(radar, 'cross_correlation_ratio')

    if refl is None:
        raise ValueError('Radar does not contain a reflectivity field')
    if zdr is None and kdp is None and cc is None:
        raise ValueError('Hydrometeor classification requires at least one '
                         'of ZDR, KDP, or correlation coefficient')

    classes = classify(
        dBZ=refl['data'],
        ZDR=_pad_to(radar, zdr['data']) if zdr is not None else None,
        KDP=_pad_to(radar, kdp['data']) if kdp is not None else None,
        CC=_pad_to(radar, cc['data']) if cc is not None else None,
        method=method, band=band, **kwargs)

    radar.add_field('hydrometeor_classification', {
        'data': np.ma.masked_invalid(classes),
        'units': 'class_id',
        'standard_name': 'hydrometeor_classification',
        'long_name': 'Hydrometeor classification',
        'valid_min': 0,
        'valid_max': 10,
        '_FillValue': get_fillvalue(),
    }, replace_existing=True)
    return radar.fields['hydrometeor_classification']['data']


def _read_tilts(filename, radius, dtype='REF'):
    from cinrad.io import CinradReader, StandardData
    try:
        cinrad_obj = StandardData(filename)
    except Exception:
        cinrad_obj = CinradReader(filename)
    return [cinrad_obj.get_data(i, radius, dtype)
            for i in cinrad_obj.angleindex_r]


def composite_reflectivity(filename, radius=230, dtype='REF'):
    """ Composite reflectivity via PyCINRAD's ``quick_cr``. """
    from cinrad.calc import quick_cr
    rl = _read_tilts(filename, radius, dtype)
    return quick_cr(rl)


def echo_tops(filename, radius=230):
    """ Echo-top heights via PyCINRAD's ``quick_et``. """
    from cinrad.calc import quick_et
    rl = _read_tilts(filename, radius, 'REF')
    return quick_et(rl)


def vert_integrated_liquid(filename, radius=230):
    """ Vertically integrated liquid via PyCINRAD's ``quick_vil``. """
    from cinrad.calc import quick_vil
    rl = _read_tilts(filename, radius, 'REF')
    return quick_vil(rl)


__all__ = ['hydro_class', 'composite_reflectivity', 'echo_tops',
           'vert_integrated_liquid']