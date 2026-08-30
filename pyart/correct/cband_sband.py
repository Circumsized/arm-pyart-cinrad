"""
pyart.correct.cband_sband
=========================

Thin C/S-band specific wrappers around Py-ART's dual-polarization correction
and retrieval routines, intended for C98D (C-band) and CINRAD (S-band) radar
objects produced by :func:`pyart.io.c98dfile_archive` and
:func:`pyart.io.read_sband_archive`.

Recommended workflow::

    calibrate_dualpol(radar)            # apply ZDR/PhiDP/LDR system offsets
    process_phi_kdp(radar, offset=0.0)  # unfold PhiDP and compute KDP
    calculate_attenuation(radar, ...)   # optional attenuation correction

"""

import warnings

import numpy as np

from ..config import get_field_name
from .phase_proc import phase_proc_lp


# Band-dependent processing defaults. The system offsets (zdr/phase/ldr) are
# used as fallbacks when the in-file ``radar_calibration`` does not carry a
# value; ``self_const`` is the default self-consistency factor forwarded to
# :func:`pyart.correct.phase_proc_lp`.
BAND_PARAMS = {
    'X': {
        'zdr_offset': 0.0,
        'phase_offset': 0.0,
        'ldr_offset': 0.0,
        'self_const': 100000.0,
    },
    'C': {
        'zdr_offset': 0.0,
        'phase_offset': 0.0,
        'ldr_offset': 0.0,
        'self_const': 60000.0,
    },
    'S': {
        'zdr_offset': 0.0,
        'phase_offset': 0.0,
        'ldr_offset': 0.0,
        'self_const': 60000.0,
    },
}


def _resolve_band(radar):
    """Return the X/C/S band key for ``radar``, defaulting to ``'C'``."""
    band = (radar.metadata or {}).get('radar_band')
    if band is not None:
        band = str(band).upper()
        if band in BAND_PARAMS:
            return band
    warnings.warn(
        "radar_band metadata missing or unknown; defaulting to 'C'",
        UserWarning)
    return 'C'


def calibrate_dualpol(radar):
    """
    Apply system calibration offsets to the dual-polarization fields.

    The ZDR, PhiDP and LDR system offsets are read from
    ``radar.radar_calibration`` (populated when reading C98D files using
    :func:`pyart.io.c98dfile_archive`), subtracted from the corresponding
    fields, and returned in a new dictionary. When an offset is missing from
    ``radar_calibration``, the band-specific default from :data:`BAND_PARAMS`
    (selected via ``radar.metadata['radar_band']``) is used. The original
    fields are not modified.

    Parameters
    ----------
    radar : Radar
        Radar object containing the dual-polarization fields.

    Returns
    -------
    corrections : dict
        Dictionary of corrected field dictionaries keyed by field name. Only
        fields present in the radar and having a non-zero offset are included
        (beyond the ones already copied unchanged).

    """
    band = _resolve_band(radar)
    band_params = BAND_PARAMS[band]

    offsets = [
        (get_field_name('differential_reflectivity'), 'zdr_calibration',
         band_params['zdr_offset']),
        (get_field_name('differential_phase'), 'phase_calibration',
         band_params['phase_offset']),
        (get_field_name('linear_depolarization_ratio'), 'ldr_calibration',
         band_params['ldr_offset']),
    ]

    calibration = getattr(radar, 'radar_calibration', {}) or {}

    corrections = {}
    for field, key, default_offset in offsets:
        if field not in radar.fields:
            continue
        offset = _cal_offset(calibration, key)
        if offset == 0.0:
            offset = default_offset
        corrected = dict(radar.fields[field])
        if offset != 0.0:
            corrected['data'] = radar.fields[field]['data'] - offset
        corrected['long_name'] = 'Corrected ' + radar.fields[field].get(
            'long_name', field)
        corrections[field] = corrected

    return corrections


def _cal_offset(calibration, key):
    """ Return the calibration offset value or 0.0. """
    entry = calibration.get(key, {})
    data = entry.get('data') if isinstance(entry, dict) else None
    if data is None:
        return 0.0
    return float(np.asarray(data).ravel()[0])


def process_phi_kdp(radar, offset=0.0, **kwargs):
    """
    Unfold differential phase and compute specific differential phase (KDP).

    This is a thin wrapper around :func:`pyart.correct.phase_proc_lp`, which
    requires a linear programming solver (CyLP, CVXOPT or PyGLPK) to be
    installed. Results should be validated against real C/S/X-band data. The
    band-dependent ``self_const`` default is selected from :data:`BAND_PARAMS`
    unless overridden via ``kwargs``.

    Parameters
    ----------
    radar : Radar
        Radar object containing a differential phase field.
    offset : float, optional
        System phase offset (in degrees) to remove before unfolding.
    kwargs : dict
        Additional keyword arguments forwarded to
        :func:`pyart.correct.phase_proc_lp`.

    Returns
    -------
    radar : Radar
        The input Radar object with the unfolded differential phase and
        specific differential phase fields added.

    """
    phi_field = get_field_name('differential_phase')
    if radar.fields.get(phi_field) is None:
        raise ValueError(
            'Radar does not contain a "{0}" field.'.format(phi_field))

    band = _resolve_band(radar)
    kwargs.setdefault('self_const', BAND_PARAMS[band]['self_const'])

    phase_proc_lp(radar, offset, **kwargs)
    return radar


__all__ = [
    'BAND_PARAMS',
    'calibrate_dualpol',
    'process_phi_kdp',
    '_resolve_band',
]