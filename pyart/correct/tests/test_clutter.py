"""
Tests for the MeteoSwiss-inspired clutter mask (pyart.correct.clutter).
"""

import matplotlib
matplotlib.use('Agg')

import numpy as np
import pytest

pytest.importorskip('pyart')

from pyart.correct.clutter import clutter_mask, apply_clutter_mask
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


def _build_c98d_with_rho():
    ngates = 6
    moments = [
        (2, 2, 66, [86, 88, 90, 92, 94, 96]),      # reflectivity
        (9, 1000, 0, [990, 990, 900, 900, 990, 900]),  # CC
    ]
    buf = bytearray()
    buf += _pack(GENERIC_HEADER, {'magic_word': 1, 'major_version': 1,
                                  'minor_version': 0, 'generic_type': 1,
                                  'product_type': 1})
    buf += _pack(SITE_CONFIG, {'latitude': 32.2, 'longitude': 118.7,
                               'height': 45})
    buf += _pack(TASK_CONFIG, {'polarization_type': 3, 'scan_type': 0,
                               'volume_start_time': 1000000000,
                               'cut_number': 1})
    buf += _pack(CUT_CONFIG, {'log_reso': 250, 'doppler_reso': 250,
                              'nyquist_speed': 10.0, 'maxi_range1': 150000,
                              'maxi_range2': 150000, 'start_range': 0,
                              'elevation': 0.5})
    buf += _pack(RADIAL_HEADER, {'radial_state': 0, 'azimuth': 0.0,
                                 'elevation': 0.5, 'seconds': 1000000000,
                                 'moment_number': len(moments)})
    for dtype, scale, offset, raw in moments:
        buf += _pack(MOMENT_HEADER, {'data_type': dtype, 'scale': scale,
                                     'offset': offset, 'bin_length': 2,
                                     'length': 2 * ngates})
        buf += _encode16(raw)
    buf += _pack(RADIAL_HEADER, {'radial_state': 4, 'azimuth': 1.0,
                                 'elevation': 0.5, 'seconds': 1000000001,
                                 'moment_number': 0})
    return bytes(buf)


@pytest.fixture(scope='module')
def radar():
    import pyart
    return c98dfile_archive(io.BytesIO(_build_c98d_with_rho()))


def test_clutter_mask_shape(radar):
    mask = clutter_mask(radar, max_texture=3.0, min_rho=0.95)
    assert mask.shape == (radar.nrays, radar.ngates)
    assert mask.dtype == bool


def test_clutter_mask_flags_constant_high_rho(radar):
    mask = clutter_mask(radar, max_texture=3.0, min_rho=0.95)
    # gates 0-1 and 4 have rho=0.99 and constant reflectivity -> clutter
    assert mask[0, 0] and mask[0, 1] and mask[0, 4]
    # gates 2-3 and 5 have rho=0.90 -> not clutter
    assert not mask[0, 2] and not mask[0, 3] and not mask[0, 5]


def test_clutter_mask_requires_reflectivity(radar):
    saved = radar.fields.pop('reflectivity')
    try:
        with pytest.raises(ValueError, match='reflectivity'):
            clutter_mask(radar)
    finally:
        radar.fields['reflectivity'] = saved


def test_apply_clutter_mask(radar):
    mask = clutter_mask(radar, max_texture=3.0, min_rho=0.95)
    apply_clutter_mask(radar, mask)
    data = radar.fields['reflectivity']['data']
    assert np.ma.is_masked(data[0, 0])
    assert not np.ma.is_masked(data[0, 2])