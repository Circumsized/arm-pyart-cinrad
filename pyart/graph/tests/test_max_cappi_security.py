"""
Security regression tests for pyart.graph.max_cappi (FU-06).

Covers the path traversal sink (94f00e, CWE-22): ``plot_maxcappi`` built its
output filename by concatenating ``savedir``, the caller-provided ``title``
and the ``instrument_name`` metadata read from the input file, then handed
the result to ``plt.savefig`` without basename extraction, path
normalization or directory containment checks. A crafted radar/grid file
carrying ``instrument_name`` traversal sequences could therefore write PNG
files outside ``savedir``.
"""

import numpy as np
import pytest

pytest.importorskip("pyart")

from pyart.graph.max_cappi import plot_maxcappi  # noqa: E402
from pyart.testing import sample_objects  # noqa: E402

FIELD = "reflectivity"


def _grid_with_instrument_name(name):
    # A taller z extent than make_target_grid's 0-500 m keeps the
    # Max-CAPPI sidetick arithmetic well defined (max_height > 0).
    grid = sample_objects.make_empty_grid(
        (2, 40, 40), ((0, 10000), (-40000, 40000), (-40000, 40000))
    )
    fdata = np.zeros((2, 40, 40), dtype="float32")
    fdata[:, 5:-5, 5:-5] = 10.0
    grid.fields = {FIELD: {"data": fdata, "long_name": "reflectivity", "units": "dBz"}}
    grid.metadata["instrument_name"] = name
    return grid


def _all_pngs(root):
    return sorted(p for p in root.rglob("*.png"))


def _assert_contained(tmp_path, savedir):
    pngs = _all_pngs(tmp_path)
    assert pngs, "expected exactly the figure to be written"
    for png in pngs:
        assert png.resolve().parent == savedir.resolve()


def test_plot_maxcappi_instrument_name_traversal_stays_in_savedir(tmp_path):
    savedir = tmp_path / "plots"
    # Pre-create the intermediate component the crafted name walks through so
    # the pre-fix behaviour is a concrete escape (file written outside
    # savedir) rather than a missing-directory error.
    (savedir / "t_d").mkdir(parents=True)
    grid = _grid_with_instrument_name("d/../../evil")
    plot_maxcappi(
        grid, FIELD, title="t", savedir=str(savedir), show_figure=False, add_map=False
    )
    _assert_contained(tmp_path, savedir)


def test_plot_maxcappi_absolute_instrument_name_stays_in_savedir(tmp_path):
    savedir = tmp_path / "plots"
    savedir.mkdir()
    grid = _grid_with_instrument_name("/tmp/abs_pwned")
    plot_maxcappi(grid, FIELD, savedir=str(savedir), show_figure=False, add_map=False)
    _assert_contained(tmp_path, savedir)


def test_plot_maxcappi_title_traversal_stays_in_savedir(tmp_path):
    savedir = tmp_path / "plots"
    savedir.mkdir()
    grid = _grid_with_instrument_name("KAZR")
    plot_maxcappi(
        grid,
        FIELD,
        title="x/../../evil",
        savedir=str(savedir),
        show_figure=False,
        add_map=False,
    )
    _assert_contained(tmp_path, savedir)


def test_plot_maxcappi_benign_names_still_written(tmp_path):
    """Benign instrument/title values must keep working (regression guard)."""
    savedir = tmp_path / "plots"
    savedir.mkdir()
    grid = _grid_with_instrument_name("KAZR")
    plot_maxcappi(grid, FIELD, savedir=str(savedir), show_figure=False, add_map=False)
    _assert_contained(tmp_path, savedir)
    assert len(_all_pngs(savedir)) == 1
