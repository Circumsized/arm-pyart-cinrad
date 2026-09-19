"""
Security regression tests for the 3D phase unwrapper (FU-08).

Covers the unchecked allocations and integer overflow in
``pyart/correct/unwrap_3d_ljmu.c:unwrap3D`` (1a7f21, CWE-476/190):

* the three ``calloc`` return values were never checked, so an allocation
  failure was dereferenced as NULL inside ``extend_mask``;
* ``volume_size`` and ``No_of_Edges_initially`` were computed as ``int``,
  so a volume whose ``width * height * depth`` overflows 32 bits wrapped to
  a negative or truncated value and under-allocated every buffer;
* the Cython wrapper took ``&image[0, 0, 0]`` unconditionally, so an empty
  axis was an out-of-bounds address, and mismatched mask/unwrapped_image
  shapes let the C routine write past a smaller output buffer.

The C entry point now validates the dimensions (with SIZE_MAX pre-checks
and an INT_MAX/3 cap that keeps the int-indexed internals sound), checks
every allocation and reports failure through its return code. The wrapper
rejects empty/mismatched inputs and maps the codes to ValueError /
MemoryError.
"""

import glob
import os
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.correct._unwrap_3d import unwrap_3d  # noqa: E402

# 0 success, -1 invalid dimensions, -2 allocation failure
CALL_OVERFLOW_DIMS = (2048, 2048, 2048)  # 2**33 overflows a 32-bit int
CALL_ZERO_DIM = (0, 5, 5)
# 100M voxels: ~100 MB mask but ~8 GB of VOXELM, so the second allocation
# fails under a 1 GB address-space limit.
CALL_ALLOC_FAILURE_DIMS = (1000, 1000, 100)


def _library_path():
    from pyart.correct import _unwrap_3d

    matches = glob.glob(
        os.path.join(os.path.dirname(_unwrap_3d.__file__), "_unwrap_3d*.so")
    )
    assert matches, "compiled _unwrap_3d extension not found"
    return matches[0]


def _call_unwrap3D(width, height, depth, ulimit_kb=None):
    """Call the C entry point directly in a subprocess.

    Isolation is required because a regression crashes the process (NULL
    dereference after a failed allocation) instead of returning an error.
    """
    script = (
        "import ctypes, sys\n"
        "lib = ctypes.CDLL(sys.argv[1])\n"
        "lib.unwrap3D.restype = ctypes.c_int\n"
        "lib.unwrap3D.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int] * 6\n"
        f"print(lib.unwrap3D(None, None, None, {width}, {height}, {depth}, 0, 0, 0))\n"
    )
    if ulimit_kb is None:
        cmd = [sys.executable, "-c", script, _library_path()]
    else:
        cmd = [
            "bash",
            "-c",
            f"ulimit -v {ulimit_kb}; exec {sys.executable} -c '{script}' "
            f"{_library_path()}",
        ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


# --------------------- wrapper-level contract ---------------------


@pytest.mark.parametrize("shape", [(0, 4, 4), (4, 0, 4), (4, 4, 0)])
def test_unwrap_3d_rejects_empty_axes(shape):
    """An empty axis must be rejected, not turned into &image[0, 0, 0]."""
    image = np.empty(shape)
    mask = np.empty(shape, dtype="uint8")
    unwrapped = np.empty(shape)
    with pytest.raises(ValueError, match="non-empty"):
        unwrap_3d(image, mask, unwrapped, [False, False, False])


def test_unwrap_3d_rejects_short_unwrapped_image():
    # A smaller output buffer let the C routine write past its end.
    image = np.zeros((2, 3, 4))
    mask = np.full((2, 3, 4), 255, dtype="uint8")
    unwrapped = np.zeros((2, 3, 3))
    with pytest.raises(ValueError, match="unwrapped_image shape"):
        unwrap_3d(image, mask, unwrapped, [False, False, False])


def test_unwrap_3d_rejects_mismatched_mask():
    image = np.zeros((2, 3, 4))
    mask = np.full((2, 3, 5), 255, dtype="uint8")
    unwrapped = np.zeros((2, 3, 4))
    with pytest.raises(ValueError, match="mask shape"):
        unwrap_3d(image, mask, unwrapped, [False, False, False])


def test_unwrap_3d_normal_operation_unchanged():
    """Regression: the unwrapping result itself must not change."""
    # a ramp that wraps once, replicated across rays
    image = np.tile(np.array([[[0.0, 1.0, 2.0, 3.0, -3.1, -2.2]]]), (1, 3, 1))
    mask = np.full(image.shape, 255, dtype="uint8")
    unwrapped = np.empty_like(image)
    unwrap_3d(image, mask, unwrapped, [False, False, False])
    np.testing.assert_allclose(
        unwrapped,
        np.tile(np.array([[[0.0, 1.0, 2.0, 3.0, -3.1, -2.2]]]), (1, 3, 1)),
    )
    assert float(unwrapped.sum()) == pytest.approx(2.1, abs=1e-9)

    # already unwrapped random phases must round-trip unchanged
    rng = np.random.default_rng(42)
    image = rng.uniform(-np.pi, np.pi, size=(3, 5, 7))
    mask = np.full(image.shape, 255, dtype="uint8")
    unwrapped = np.empty_like(image)
    unwrap_3d(image, mask, unwrapped, [False, False, False])
    np.testing.assert_allclose(unwrapped, image)


# --------------------- C entry-point contract ---------------------


def test_unwrap3D_rejects_dimension_overflow():
    proc = _call_unwrap3D(*CALL_OVERFLOW_DIMS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "-1"


def test_unwrap3D_rejects_zero_dimension():
    proc = _call_unwrap3D(*CALL_ZERO_DIM)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "-1"


def test_unwrap3D_reports_allocation_failure():
    proc = _call_unwrap3D(*CALL_ALLOC_FAILURE_DIMS, ulimit_kb=1000000)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "-2"
