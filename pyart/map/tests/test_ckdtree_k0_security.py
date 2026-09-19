"""
Security regression tests for the cKDTree k<=0 query defect (FU-12).

Covers the heap underflow/overflow in ``cKDTree.query`` (a8abf7,
CWE-122/787). ``__query`` allocates ``neighbors = heap(k)``; with k == 0
the first in-range neighbor takes the ``neighbors.n == k`` branch and
calls ``remove()`` on the empty heap. ``remove()`` unconditionally
executes ``self.heap[0] = self.heap[self.n - 1]`` on a raw C pointer (no
bounds checking possible), i.e. it touches ``heap[-1]`` of a zero-length
allocation, and then drives ``self.n`` negative so subsequent ``push()``
calls write before the allocation as well. No exception is raised today,
so the corruption is silent.
"""

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.map.ckdtree import cKDTree  # noqa: E402


def _tree(n=200, seed=123456):
    rng = np.random.RandomState(seed)
    return cKDTree(rng.random_sample((n, 2)), leafsize=10)


@pytest.mark.parametrize("k", [0, -1, -1000])
def test_query_rejects_non_positive_k(k):
    tree = _tree()
    with pytest.raises(ValueError, match="k must be at least 1"):
        tree.query([[0.5, 0.5], [0.1, 0.2]], k=k)


def test_query_k1_matches_brute_force():
    """The guard must not alter well-formed k=1 results."""
    rng = np.random.RandomState(7)
    data = rng.random_sample((200, 2))
    tree = cKDTree(data, leafsize=10)
    q = rng.random_sample((2, 2))
    d, i = tree.query(q, k=1)
    for j in range(2):
        dists = np.sum((data - q[j]) ** 2, axis=1)
        assert i[j] == int(np.argmin(dists))
        np.testing.assert_allclose(d[j], np.sqrt(dists[i[j]]))
