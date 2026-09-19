"""
Security regression tests for the Maesaka KDP retrieval kernels (FU-04).

Covers two out-of-bounds reads (21f732/291237, CWE-125) in
``pyart.retrieve._kdp_proc``:

* ``lowpass_maesaka_term`` indexes ``k[r, g+2]`` / ``k[r, g-2]`` at the ray
  boundaries, which is only valid for rays with at least 3 gates;
* ``lowpass_maesaka_jac`` uses a g-2 .. g+2 difference template, valid only
  for rays with at least 4 gates.

Both functions are compiled with ``boundscheck(False)`` and
``wraparound(False)``, so the contracts were never enforced: a radar with
fewer than 3 (resp. 4) gates per ray -- entirely possible from a crafted
file -- made the generated C code read outside the NumPy buffer. The number
of gates comes from ``radar.ngates``, i.e. from the input file.
"""

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.retrieve import _kdp_proc, kdp_proc  # noqa: E402
from pyart.testing import sample_objects  # noqa: E402


def _radar_with_psidp(ngates, nrays=2, nsweeps=1):
    radar = sample_objects.make_empty_ppi_radar(ngates, nrays, nsweeps)
    gates = np.arange(ngates, dtype="float64")
    psidp = (2.0e-3 * gates)[np.newaxis, :].repeat(nrays, axis=0)
    radar.add_field(
        "differential_phase",
        {"data": np.ma.masked_array(psidp), "units": "deg"},
    )
    return radar


@pytest.mark.parametrize("ngates", [2, 3])
def test_kdp_maesaka_rejects_too_few_gates(ngates):
    radar = _radar_with_psidp(ngates)
    with pytest.raises(ValueError, match="gates"):
        kdp_proc.kdp_maesaka(radar)


def test_lowpass_maesaka_term_rejects_rays_with_lt_3_gates():
    k = np.ascontiguousarray(np.ones((2, 2)))
    out = np.empty_like(k)
    with pytest.raises(ValueError, match="3"):
        _kdp_proc.lowpass_maesaka_term(k, 250.0, "low", out)


@pytest.mark.parametrize("ngates", [1, 2, 3])
def test_lowpass_maesaka_jac_rejects_rays_with_lt_4_gates(ngates):
    d2kdr2 = np.ascontiguousarray(np.ones((2, ngates)))
    out = np.empty_like(d2kdr2)
    with pytest.raises(ValueError, match="4"):
        _kdp_proc.lowpass_maesaka_jac(d2kdr2, 250.0, 1.0, "low", out)


def test_kdp_maesaka_on_normal_radar_still_works():
    """Regression guard: full-width rays must keep working unchanged."""
    radar = _radar_with_psidp(200, nrays=3)
    kdp_dict, phidpf_dict, phidpr_dict = kdp_proc.kdp_maesaka(radar)
    result = kdp_dict["data"]
    assert result.shape == (3, 200)
    assert np.all(np.isfinite(result))
