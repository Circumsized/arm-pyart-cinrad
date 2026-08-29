"""
Tests for the experimental non-standard X-band readers.

Synthetic byte streams are built with the same layout tables the readers
use, so these tests verify structure, magic-word validation and data
decoding without requiring real vendor sample files.
"""

import numpy as np
import pytest

pytest.importorskip('pyart')

from pyart.io.xband_native import (
    XSP724_HEADER, XSP724_RAY, XSP724_MOMENT,
    SCRXD_HEADER, SCRXD_RAY, SCRXD_MOMENT,
    read_xband_724xsp, read_xband_scrxd01,
    _fmt, _pack_structure)


def _build_volume(layouts, magic, ngates=64, nrays=16, az_step=5.0,
                  moments=(1, 2, 3, 4, 5, 6), seed=0):
    header, ray_layout, moment_layout = layouts
    rng = np.random.default_rng(seed)

    header_values = {
        'magic': magic,
        'major_version': 1,
        'minor_version': 0,
        'site_lat': 31.2304,
        'site_lon': 121.4737,
        'site_alt': 4.0,
        'ngates': ngates,
        'gate_spacing': 100.0,
        'nyquist_speed': 25.0,
        'nrays': nrays,
        'fixed_angle': 0.5,
    }
    keys = [name for name, _ in header]
    buf = _pack_structure([header_values[k] for k in keys], header)

    for ray in range(nrays):
        buf += _pack_structure([float(ray * az_step), 0.5], ray_layout)
        for code in moments:
            buf += _pack_structure([code, 10, 0], moment_layout)
            vals = rng.integers(1, 254, size=ngates, dtype='uint8')
            buf += vals.tobytes()
    return buf


_VOLS = ((XSP724_HEADER, XSP724_RAY, XSP724_MOMENT),
         (SCRXD_HEADER, SCRXD_RAY, SCRXD_MOMENT))


def _write(tmp_path, buf, name):
    path = tmp_path / name
    path.write_bytes(buf)
    return str(path)


def test_read_724xsp_roundtrip(tmp_path):
    buf = _build_volume(_VOLS[0], b'724X', ngates=64, nrays=16)
    path = _write(tmp_path, buf, 'sample.rsp')
    radar = read_xband_724xsp(path)
    assert radar.scan_type == 'ppi'
    assert radar.metadata['radar_band'] == 'X'
    assert radar.metadata['original_container'] == '724XSP'
    assert radar.nsweeps == 1
    assert radar.nrays == 16
    assert radar.ngates == 64
    assert 'reflectivity' in radar.fields
    assert 'velocity' in radar.fields
    assert 'spectrum_width' in radar.fields
    assert np.isclose(radar.latitude['data'][0], 31.2304)
    assert np.isclose(radar.longitude['data'][0], 121.4737)
    data = radar.fields['reflectivity']['data']
    assert data.shape == (16, 64)
    assert radar.range['data'][1] - radar.range['data'][0] == 100.0


def test_read_scrxd01_roundtrip(tmp_path):
    buf = _build_volume(_VOLS[1], b'SCRX', ngates=32, nrays=8, az_step=45.0)
    path = _write(tmp_path, buf, 'sample.scrx')
    radar = read_xband_scrxd01(path)
    assert radar.metadata['original_container'] == 'SCRXD-01'
    assert radar.nrays == 8
    assert radar.ngates == 32
    assert 'reflectivity' in radar.fields
    assert radar.fields['reflectivity']['data'].shape == (8, 32)


def test_bad_magic_raises(tmp_path):
    buf = bytearray(_build_volume(_VOLS[0], b'724X', ngates=8, nrays=2))
    buf[:4] = b'NOPE'
    path = _write(tmp_path, bytes(buf), 'bad.rsp')
    with pytest.raises(ValueError, match='bad magic'):
        read_xband_724xsp(path)


def test_truncated_header_raises(tmp_path):
    buf = b'724X'
    path = _write(tmp_path, buf, 'short.rsp')
    with pytest.raises(ValueError, match='truncated'):
        read_xband_724xsp(path)


def test_truncated_moment_raises(tmp_path):
    buf = _build_volume(_VOLS[1], b'SCRX', ngates=64, nrays=4)
    path = _write(tmp_path, buf[:-40], 'trunc.scrx')
    with pytest.raises(ValueError, match='truncated moment'):
        read_xband_scrxd01(path)