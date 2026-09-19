"""
Security regression tests for the h_factor length contract (FU-11).

Covers the out-of-bounds read in ``DistBeamRoI.get_roi``
(4f04a8, CWE-125). ``map_gates_to_grid`` converts a tuple/list ``h_factor``
straight to an array of whatever length the caller passed, and
``get_roi`` then indexes ``h_factor[0]..h_factor[2]`` with
``boundscheck(False)``/``wraparound(False)``: a 2-component (or empty)
factor reads outside the buffer. The documented contract is exactly three
components (z, y, x weighting), so anything else must be rejected.
"""

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.map.gates_to_grid import map_gates_to_grid  # noqa: E402
from pyart.testing import sample_objects  # noqa: E402


def _setup():
    radar = sample_objects.make_empty_ppi_radar(10, 4, 1)
    radar.fields["reflectivity"] = {
        "data": np.ma.masked_array(np.zeros((4, 10), dtype="float32"))
    }
    shape = (2, 40, 40)
    limits = ((0, 10000), (-40000, 40000), (-40000, 40000))
    return radar, shape, limits


@pytest.mark.parametrize(
    "h_factor",
    [(1.0, 1.0), (), (1.0,), (1.0, 1.0, 1.0, 1.0), tuple([1.0] * 5)],
)
def test_map_gates_to_grid_rejects_h_factor_not_length_3(h_factor):
    radar, shape, limits = _setup()
    with pytest.raises(ValueError, match="h_factor"):
        map_gates_to_grid(radar, shape, limits, roi_func="dist_beam", h_factor=h_factor)


def test_map_gates_to_grid_dist_beam_explicit_3_matches_default():
    """A valid 3-component factor must keep working, default unchanged."""
    radar, shape, limits = _setup()
    g_default = map_gates_to_grid(radar, shape, limits, roi_func="dist_beam")
    radar2, shape2, limits2 = _setup()
    g_explicit = map_gates_to_grid(
        radar2, shape2, limits2, roi_func="dist_beam", h_factor=(1.0, 1.0, 1.0)
    )
    np.testing.assert_allclose(g_default["reflectivity"], g_explicit["reflectivity"])
