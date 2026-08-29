"""
Tests for the GIF animation helpers (pyart.graph.animation).

Frame generation runs on the Agg backend against synthetic two-frame
radars; the map variant skips when cartopy is unavailable.
"""

import os

import matplotlib
matplotlib.use('Agg')

import numpy as np
import pytest

pytest.importorskip('pyart')
pytest.importorskip('imageio')

from pyart.graph.animation import (
    animate_ppi, animate_rhi, animate_map_ppi,
    animate_ppi_batch, animate_multi_band,
)


@pytest.fixture(scope='module')
def radars():
    from pyart.testing import make_empty_ppi_radar

    frames = []
    for i in range(2):
        radar = make_empty_ppi_radar(10, 5, 1)
        data = np.full((radar.nrays, radar.ngates), 20.0 + 5 * i,
                       dtype='float32')
        radar.add_field('reflectivity',
                        {'data': data, 'units': 'dBZ'})
        frames.append(radar)
    return frames


def test_animate_ppi(radars, tmp_path):
    out = str(tmp_path / 'ppi.gif')
    animate_ppi(radars, 'reflectivity', sweep=0, out=out, vmin=0, vmax=70)
    import imageio
    frames = imageio.mimread(out)
    assert len(frames) == 2


def test_animate_rhi(tmp_path):
    from pyart.testing import make_empty_rhi_radar

    frames = []
    for i in range(2):
        radar = make_empty_rhi_radar(10, 5, 1)
        data = np.full((radar.nrays, radar.ngates), 20.0 + 5 * i,
                       dtype='float32')
        radar.add_field('reflectivity',
                        {'data': data, 'units': 'dBZ'})
        frames.append(radar)
    out = str(tmp_path / 'rhi.gif')
    animate_rhi(frames, 'reflectivity', out=out, vmin=0, vmax=70)
    import imageio
    frames = imageio.mimread(out)
    assert len(frames) == 2


def test_animate_ppi_empty_raises():
    with pytest.raises(ValueError):
        animate_ppi([], 'reflectivity', out='x.gif')


def test_animate_ppi_bad_outdir(radars, tmp_path):
    with pytest.raises(ValueError):
        animate_ppi(radars, 'reflectivity',
                    out=str(tmp_path / 'nope' / 'x.gif'))


@pytest.mark.skipif(pytest.importorskip('pyart') is None and False,
                    reason='cartopy availability tested below')
def test_animate_map_ppi(radars, tmp_path):
    pytest.importorskip('cartopy')
    out = str(tmp_path / 'map.gif')
    animate_map_ppi(radars, 'reflectivity', sweep=0, out=out, vmin=0, vmax=70)
    import imageio
    frames = imageio.mimread(out)
    assert len(frames) == 2


def test_animate_ppi_batch(radars, tmp_path):
    from pyart.io import write_cfradial

    files = [str(tmp_path / 'frame_{0}.nc'.format(i)) for i in range(3)]
    for i in range(3):
        write_cfradial(files[i], radars[i % 2])
    out_dir = str(tmp_path / 'batch')
    report = animate_ppi_batch(files, 'reflectivity', out_dir=out_dir,
                               sweep=0, vmin=0, vmax=70)
    assert len(report['success']) == 3
    assert len(report['failed']) == 0
    import imageio
    for out in report['success']:
        frames = imageio.mimread(out)
        assert len(frames) == 1


def test_animate_multi_band(radars, tmp_path):
    radars_by_band = {'S': radars[0], 'C': radars[1]}
    out = str(tmp_path / 'multi.gif')
    animate_multi_band(radars_by_band, 'reflectivity', out=out,
                       sweep=0, vmin=0, vmax=70)
    import imageio
    frames = imageio.mimread(out)
    assert len(frames) == 1


def test_animate_ppi_batch_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        animate_ppi_batch([], 'reflectivity', out_dir=str(tmp_path))


def test_animate_multi_band_empty_raises():
    with pytest.raises(ValueError):
        animate_multi_band({}, 'reflectivity')