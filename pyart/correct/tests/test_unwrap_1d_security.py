"""
Security regression tests for the unwrap_1d empty-buffer defect (FU-07).

Covers the out-of-bounds read/write in ``pyart.correct._unwrap_1d``
(c5ca90, CWE-787). The kernel is compiled with ``boundscheck=False`` and
``wraparound=False``, but unconditionally executes
``unwrapped_image[0] = image[0]``: an empty ``image`` reads before the
allocation, and an ``unwrapped_image`` shorter than ``image`` writes past
its end once the loop advances. Zero-length rays are reachable through
``dealias_unwrap_phase`` with a zero-gate radar, so this is not
hypothetical.
"""

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.correct._unwrap_1d import unwrap_1d  # noqa: E402


def test_unwrap_1d_rejects_empty_image():
    """An empty image must raise, not touch unwrapped_image[0]."""
    with pytest.raises(ValueError):
        unwrap_1d(np.empty(0), np.empty(0))


def test_unwrap_1d_rejects_unwrapped_buffer_shorter_than_image():
    with pytest.raises(ValueError):
        unwrap_1d(np.ones(10), np.empty(4))


def test_unwrap_1d_normal_operation_unchanged():
    """The fix must not alter results for well-formed inputs."""
    image = np.array([0.0, 3.0, -3.0, 6.0])
    unwrapped = np.empty_like(image)
    unwrap_1d(image, unwrapped)
    expected = np.array([0.0, 3.0, -3.0 + 2 * np.pi, 6.0])
    np.testing.assert_allclose(unwrapped, expected)


def test_dealias_unwrap_1d_rejects_zero_gate_rays_cleanly():
    """The ray-by-ray caller must fail with ValueError, never a crash."""
    from pyart.correct.unwrap import _dealias_unwrap_1d

    with pytest.raises(ValueError):
        _dealias_unwrap_1d(np.empty((2, 0)), np.array([10.0]))
