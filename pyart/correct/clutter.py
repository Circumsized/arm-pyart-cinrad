"""
pyart.correct.clutter
=====================

Lightweight ground-clutter masking inspired by MeteoSwiss operational
practice (texture + RhoHV dual gating). Implemented from the published
algorithm descriptions; no MeteoSwiss source code is used.

"""

import numpy as np

from ..config import get_field_name


def _reflectivity_texture(refl, window=5):
    """
    Along-ray standard deviation of reflectivity over a rolling window.

    Clutter gates typically show low spatial variability, so small texture
    values indicate (possible) clutter.

    """
    from scipy.ndimage import uniform_filter1d

    data = np.ma.filled(np.ma.asarray(refl, dtype=float), np.nan)
    mean = uniform_filter1d(data, size=window, axis=1, mode="nearest")
    sq_mean = uniform_filter1d(data**2, size=window, axis=1, mode="nearest")
    variance = np.clip(sq_mean - mean**2, 0.0, None)
    texture = np.sqrt(variance)
    return np.ma.masked_invalid(texture)


def clutter_mask(
    radar,
    refl_field=None,
    rho_field=None,
    texture_window=5,
    max_texture=3.0,
    min_rho=0.95,
):
    """
    Return a boolean ground-clutter mask for a Radar object.

    A gate is flagged as clutter when BOTH conditions hold:

    * reflectivity texture (along-ray rolling std-dev) <= ``max_texture``
    * cross-correlation ratio (RhoHV) >= ``min_rho``

    Parameters
    ----------
    radar : Radar
        Radar object to analyze.
    refl_field : str, optional
        Reflectivity field name; defaults to the Py-ART configuration name.
    rho_field : str, optional
        Cross-correlation ratio field name; defaults to the configuration.
    texture_window : int, optional
        Rolling window (gates) for the texture calculation.
    max_texture : float, optional
        Texture threshold in dB.
    min_rho : float, optional
        Minimum RhoHV for a gate to be considered clutter.

    Returns
    -------
    mask : numpy.ndarray of bool
        Array of shape (nrays, ngates); True marks suspected clutter gates.

    """
    refl_name = refl_field or get_field_name("reflectivity")
    rho_name = rho_field or get_field_name("cross_correlation_ratio")

    if refl_name not in radar.fields:
        raise ValueError(f'Radar does not contain a "{refl_name}" field')

    texture = _reflectivity_texture(
        radar.fields[refl_name]["data"], window=texture_window
    )

    clutter = texture <= max_texture

    if rho_name in radar.fields:
        rho = radar.fields[rho_name]["data"]
        rho_arr = np.ma.filled(np.ma.asarray(rho, dtype=float), np.nan)
        clutter &= (rho_arr >= min_rho) | np.isnan(rho_arr)

    return np.asarray(clutter, dtype=bool)


def apply_clutter_mask(radar, mask, refl_field=None, fill_value=np.nan):
    """
    Mask suspected clutter gates in the reflectivity field (in place).

    Parameters
    ----------
    radar : Radar
        Radar object modified in place.
    mask : ndarray of bool
        Clutter mask from :func:`clutter_mask`.
    refl_field : str, optional
        Reflectivity field name.
    fill_value : float, optional
        Value used to replace clutter gates (defaults to NaN, which the
        masked-array conversion turns into masked entries).

    """
    refl_name = refl_field or get_field_name("reflectivity")
    if refl_name not in radar.fields:
        raise ValueError(f'Radar does not contain a "{refl_name}" field')
    data = radar.fields[refl_name]["data"]
    masked = np.ma.masked_where(mask, np.ma.asarray(data, dtype=float))
    if fill_value is not None:
        masked = np.ma.filled(masked, fill_value)
        masked = np.ma.masked_invalid(masked)
    radar.fields[refl_name]["data"] = masked
    return radar


__all__ = ["clutter_mask", "apply_clutter_mask"]
