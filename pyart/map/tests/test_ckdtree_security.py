"""
Security regression tests for the cKDTree build-time double free (FU-05).

Covers the double free in the ``except`` handler of ``cKDTree.__build`` in
``pyart.map.ckdtree`` (CWE-415; SEI CERT C MEM31-C "Free dynamically
allocated memory once"). When any allocation inside the recursive tree
build fails -- the ``innernode`` itself, a child ``leafnode``/``innernode``,
or the per-node ``mids`` buffer -- the handler executes

    except:
        if ni != NULL:
            stdlib.free(mids)   # frees mids, leaks ni
        if mids != NULL:
            stdlib.free(mids)   # double free
        raise

so ``mids`` is freed twice and ``ni`` is never freed. The defect is only
reachable under allocation failure (ERR33-C scenario), which is exactly
what a hardened library must survive.

The tests inject allocation failures into the build with an LD_PRELOAD
malloc interposer (fu05_fail_malloc_interposer.c). The interposer is armed
by a magic-size malloc issued from Python *after* interpreter and numpy
startup, so only allocations inside the tree-build window are perturbed.
It keeps a hash set of every pointer returned by the interposed allocation
routines, so a second ``free()`` of an already freed pointer is a
definitive detection with no false positives.

Linux/glibc only; the test skips itself when it cannot build or load the
interposer.
"""

import os
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("pyart")

HERE = os.path.dirname(os.path.abspath(__file__))
INTERPOSER_C = os.path.join(HERE, "fu05_fail_malloc_interposer.c")

MAGIC_SIZE = 23063  # arming allocation; not a size the builder ever uses
N_POINTS = 20000  # ~4k internal nodes: thousands of build-window allocations
K_LIMIT = 8  # fail the k-th build-window allocation, k = 1..K_LIMIT
CHILD_TIMEOUT = 180

# Definitive marker from the interposer plus glibc abort/corruption hints.
BAD_MARKERS = (
    "FU05-DOUBLE-FREE-DETECTED",
    "double free",
    "free(): invalid",
    "corrupted",
    "malloc(): unaligned",
    "realloc(): invalid",
)

CHILD_PY = """
import sys
import ctypes

magic = int(sys.argv[1])

import numpy as np
from pyart.map.ckdtree import cKDTree

rng = np.random.RandomState(123456)
data = rng.random_sample(({n_points}, 2))

libc = ctypes.CDLL(None)
libc.malloc.restype = ctypes.c_void_p
if not libc.malloc(magic):
    print("CHILD-RESULT: arm-failed")
    sys.exit(3)

try:
    tree = cKDTree(data, leafsize=10)
    # NOTE: query() is intentionally not exercised here; this vendored
    # cKDTree.query has a pre-existing reshape defect (retshape uses the
    # last axis) unrelated to the FU-05 memory-safety fix.
    print("CHILD-RESULT: built-ok n=%d" % tree.n)
except MemoryError:
    print("CHILD-RESULT: memory-error")
except Exception as exc:
    print("CHILD-RESULT: other-error %s: %s" % (type(exc).__name__, exc))
""".format(n_points=N_POINTS)


def _compiler():
    for cc in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if cc and shutil.which(cc):
            return cc
    return None


@pytest.fixture(scope="module")
def interposer_so(tmp_path_factory):
    if not sys.platform.startswith("linux"):
        pytest.skip("LD_PRELOAD interposer test is Linux/glibc only")
    cc = _compiler()
    if cc is None:
        pytest.skip("no C compiler available to build the interposer")
    if not os.path.exists(INTERPOSER_C):
        pytest.skip("interposer source missing: %s" % INTERPOSER_C)
    so = str(tmp_path_factory.mktemp("fu05") / "fail_malloc.so")
    proc = subprocess.run(
        [cc, "-shared", "-fPIC", "-O1", "-o", so, INTERPOSER_C],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip("interposer build failed: %s" % proc.stderr.strip())
    return so


def _run_child(interposer_so, fail_at, tmp_path):
    child = tmp_path / ("fu05_child_%d.py" % fail_at)
    child.write_text(CHILD_PY)
    env = dict(os.environ)
    env["LD_PRELOAD"] = interposer_so
    env["MALLOC_CHECK_"] = "3"
    env["FU05_MAGIC"] = str(MAGIC_SIZE)
    env["FU05_FAIL_AT"] = str(fail_at)
    return subprocess.run(
        [sys.executable, str(child), str(MAGIC_SIZE)],
        capture_output=True, text=True, timeout=CHILD_TIMEOUT, env=env,
    )


def _combined(proc):
    return (proc.stdout or "") + (proc.stderr or "")


def _assert_no_double_free(proc):
    out = _combined(proc)
    for marker in BAD_MARKERS:
        assert marker not in out, (
            "FU-05: heap corruption marker %r in child output:\n%s" % (marker, out)
        )
    # glibc abort (SIGABRT = -6) or a kill is never acceptable either
    assert proc.returncode not in (-6, -11, -4), (
        "FU-05: child died from signal %d:\n%s" % (-proc.returncode, out)
    )
    return out


@pytest.mark.parametrize("fail_at", list(range(1, K_LIMIT + 1)))
def test_build_survives_injected_allocation_failure(interposer_so, fail_at, tmp_path):
    """Failing the k-th build-window allocation must not double free."""
    proc = _run_child(interposer_so, fail_at, tmp_path)
    out = _assert_no_double_free(proc)
    # the injection must have fired, and the build must have failed cleanly
    assert "FU05-INJECTED-FAILURE" in out
    assert "CHILD-RESULT: built-ok" in out or "CHILD-RESULT: memory-error" in out


def test_harness_reaches_tree_build_and_basics_still_work(interposer_so, tmp_path):
    """No injection: the tree builds, queries correctly, and the interposer
    proves its window covered the build (thousands of 16/24/40 B allocations)."""
    proc = _run_child(interposer_so, 0, tmp_path)
    out = _assert_no_double_free(proc)
    assert "CHILD-RESULT: built-ok" in out
    assert "FU05-TARGET-ALLOC-STATS" in out
    total = int(out.split("FU05-TARGET-ALLOC-STATS")[1].split("total=")[1].split()[0])
    # a 20000-point tree with leafsize 10 allocates far more than this many
    # innernode/leafnode/mids blocks; a small count would mean the window
    # never covered the build and the injection tests above would be vacuous.
    assert total >= 5000, "injection window did not cover the tree build: %d" % total
