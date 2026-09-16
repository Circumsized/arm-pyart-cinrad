"""
pyart.correct.cinrad_wrappers
=============================

Thin wrappers around external CINRAD tooling (pycwr) for dual-polarization
quality control, plus a native region-based velocity dealiasing helper.

"""

import numpy as np

from ..config import get_field_name


def _import_qc():
    try:
        from pycwr.qc import run_dualpol_qc
    except ImportError as exc:
        raise ImportError(
            "pycwr is required for dualpol_qc; install it with "
            '"pip install arm_pyart[cinrad]"'
        ) from exc
    return run_dualpol_qc


def _get(radar, key):
    name = get_field_name(key)
    return radar.fields.get(name)


def _gate_spacing_km(radar):
    rng = np.asarray(radar.range["data"], dtype="float64")
    if rng.size < 2:
        return 0.075
    return float(np.median(np.diff(rng)) / 1000.0)


def dualpol_qc(
    radar,
    band="C",
    write_back=True,
    clear_air_mode="label",
    use_existing_kdp=True,
    **kwargs,
):
    """
    Run dual-polarization quality control on every sweep via pycwr.

    Parameters
    ----------
    radar : Radar
        Radar object containing reflectivity and at least one polarimetric
        moment (PhiDP or KDP).
    band : {'S', 'C', 'X'}, optional
        Radar band used to select the attenuation coefficients.
    write_back : bool, optional
        Add corrected fields back onto ``radar``.
    clear_air_mode : {'label', 'mask', 'ignore'}, optional
        How to treat clear-air echoes.
    use_existing_kdp : bool, optional
        Reuse the KDP field when present instead of re-deriving it.

    Returns
    -------
    results : dict
        Dictionary keyed by output product name (``ref_corrected``,
        ``zdr_corrected``, ``pia``, ``kdp_used``, masks, ...) whose values are
        arrays with one row per ray.

    """
    run_dualpol_qc = _import_qc()

    refl = _get(radar, "reflectivity")
    zdr = _get(radar, "differential_reflectivity")
    phidp = _get(radar, "differential_phase")
    kdp = _get(radar, "specific_differential_phase")
    rhohv = _get(radar, "cross_correlation_ratio")

    if refl is None:
        raise ValueError("Radar does not contain a reflectivity field")
    if phidp is None and kdp is None:
        raise ValueError(
            "Dual-polarization QC requires a differential_phase "
            "or specific_differential_phase field"
        )

    dr = _gate_spacing_km(radar)
    results = []
    for sweep in range(radar.nsweeps):
        s = radar.get_slice(sweep)
        res = run_dualpol_qc(
            refl["data"][s],
            zdr=None if zdr is None else zdr["data"][s],
            phidp=None if phidp is None else phidp["data"][s],
            kdp=None if kdp is None else kdp["data"][s],
            rhohv=None if rhohv is None else rhohv["data"][s],
            dr=dr,
            band=band,
            use_existing_kdp=use_existing_kdp,
            clear_air_mode=clear_air_mode,
            **kwargs,
        )
        results.append(res)

    out = {}
    for key in results[0]:
        value = results[0][key]
        if isinstance(value, dict):
            out[key] = value
        else:
            out[key] = np.ma.concatenate([r[key] for r in results], axis=0)

    if write_back:
        _write_back(radar, out)
    return out


def _write_back(radar, out):
    from ..config import get_fillvalue

    mapping = {
        "ref_corrected": ("corrected_reflectivity", "dBZ"),
        "zdr_corrected": ("corrected_differential_reflectivity", "dB"),
        "kdp_used": ("corrected_specific_differential_phase", "degrees/km"),
    }
    for src, (name, units) in mapping.items():
        if src in out:
            radar.add_field(
                name,
                {
                    "data": np.ma.masked_invalid(out[src]),
                    "units": units,
                    "_FillValue": get_fillvalue(),
                },
                replace_existing=True,
            )


def dealias_region(radar, **kwargs):
    """
    Region-based Doppler velocity dealiasing.

    Py-ART already ships a region-based dealiasing algorithm that operates
    directly on the :class:`pyart.core.Radar` object, so the equivalent
    ``cinrad.correct.dealias`` routine (which operates on PyCINRAD's own
    objects) is not reused here.

    """
    from .region_dealias import dealias_region_based

    return dealias_region_based(radar, **kwargs)


__all__ = ["dualpol_qc", "dealias_region"]
