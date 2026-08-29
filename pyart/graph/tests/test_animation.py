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

from pyart.graph.animation import animate_ppi, animate_rhi, animate_map_ppi
from pyart.io.c98d_archive import c98dfile_archive
from pyart.io.C98DRadFile import (
    GENERIC_HEADER, SITE_CONFIG, TASK_CONFIG, CUT_CONFIG, RADIAL_HEADER,
    MOMENT_HEADER)

import io
import re
import struct


def _pack(structure, overrides):
    fmt = '=' + ''.join(code for _, code in structure)
    values = []
    for name, code in structure:
        m = re.match(r'(\d*)([a-zA-Z])', code)
        count = int(m.group(1) or 1)
        typ = m.group(2)
        if typ == 's':
            values.append(overrides.get(name, b''))
        elif typ == 'B':
            v = overrides.get(name, 0)
            if isinstance(v, (bytes, bytearray, list, tuple)):
                values.extend(v[:count])
            else:
                values.extend([v] * count)
        else:
            values.append(overrides.get(name, 0))
    return struct.pack(fmt, *values)


def _encode16(values):
    out = bytearray()
    for v in values:
        v = int(v)
        out.append(v & 0xFF)
        out.append((v >> 8) & 0xFF)
    return bytes(out)


def _build_two_frame_c98d():
    ngates = 4
    moments = [(2, 2, 66, [86, 88, 90, 92])]  # reflectivity only
    buf = bytearray()
    buf += _pack(GENERIC_HEADER, {'magic_word': 1, 'major_version': 1,
                                  'minor_version': 0, 'generic_type': 1,
                                  'product_type': 1})
    buf += _pack(SITE_CONFIG, {'latitude': 32.2, 'longitude': 118.7,
                               'height': 45})
    buf += _pack(TASK_CONFIG, {'polarization_type': 3, 'scan_type': 0,
                               'volume_start_time': 1000000000,
                               'cut_number': 2})
    for elev in (0.5, 1.5):
        buf += _pack(CUT_CONFIG, {'log_reso': 250, 'doppler_reso': 250,
                                  'nyquist_speed': 10.0,
                                  'maxi_range1': 150000,
                                  'maxi_range2': 150000,
                                  'start_range': 0, 'elevation': elev})
    for radial in range(2):
        elev = 0.5 if radial == 0 else 1.5
        state = 0 if radial == 0 else 4
        buf += _pack(RADIAL_HEADER, {'radial_state': state,
                                     'azimuth': float(radial),
                                     'elevation': elev,
                                     'seconds': 1000000000 + radial,
                                     'moment_number': len(moments)})
        for dtype, scale, offset, raw in moments:
            buf += _pack(MOMENT_HEADER, {'data_type': dtype, 'scale': scale,
                                         'offset': offset, 'bin_length': 2,
                                         'length': 2 * ngates})
            buf += _encode16(raw)
    return bytes(buf)


@pytest.fixture(scope='module')
def radars(tmp_path_factory):
    frames = []
    for i in range(2):
        data = bytearray(_build_two_frame_c98d())
        data[128:132] = bytes([86 + i, 88, 90, 92])
        frames.append(c98dfile_archive(io.BytesIO(bytes(data))))
    return frames


def test_animate_ppi(radars, tmp_path):
    out = str(tmp_path / 'ppi.gif')
    animate_ppi(radars, 'reflectivity', sweep=0, out=out, vmin=0, vmax=70)
    import imageio
    frames = imageio.mimread(out)
    assert len(frames) == 2


def test_animate_rhi(radars, tmp_path):
    out = str(tmp_path / 'rhi.gif')
    animate_rhi(radars, 'reflectivity', out=out, vmin=0, vmax=70)
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