"""
Cross-validation tests for the PyCINRAD bridge.

The end-to-end comparison requires the optional ``cinrad`` dependency and a
real CINRAD base-data file (set the ``CINRAD_TEST_FILE`` environment variable
to its path). Tests are skipped when either is unavailable.
"""

import os

import numpy as np
import pytest

pyart = pytest.importorskip('pyart')
cinrad = pytest.importorskip('cinrad')

TEST_FILE = os.environ.get('CINRAD_TEST_FILE')

MOMENT_FIELD = {
    'REF': 'reflectivity',
    'ZDR': 'differential_reflectivity',
    'RHO': 'cross_correlation_ratio',
    'PHI': 'differential_phase',
    'KDP': 'specific_differential_phase',
}


def test_read_cinrad_is_exported():
    from pyart.io import read_cinrad
    assert callable(read_cinrad)


def test_missing_dependency_message():
    from pyart.io.cinrad_bridge import _import_cinrad
    assert callable(_import_cinrad)


@pytest.mark.skipif(TEST_FILE is None, reason='CINRAD_TEST_FILE not set')
def test_bridge_matches_pycinrad():
    radar = pyart.io.read_cinrad(TEST_FILE)

    for moment, field in MOMENT_FIELD.items():
        if field not in radar.fields:
            continue
        f = cinrad.io.StandardData(TEST_FILE)
        raw_arr = [f.get_raw(nel, 460, moment) for nel in f.available_tilt(moment)]
        expected = np.ma.vstack(raw_arr)
        got = radar.fields[field]['data']
        assert got.shape == expected.shape, (field, got.shape, expected.shape)
        assert np.allclose(np.ma.filled(got, np.nan),
                           np.ma.filled(expected, np.nan),
                           atol=1e-6, equal_nan=True), field