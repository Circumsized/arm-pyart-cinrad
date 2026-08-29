"""
Unit tests for reading C98D (C-band dual-polarization) archive files.

The file bytes are synthesized from the structure definitions in
:mod:`pyart.io.C98DRadFile`, so these tests do not require any real data.
"""

import io
import re
import struct

import numpy as np

from pyart.io.c98d_archive import c98dfile_archive
from pyart.io.C98DRadFile import (
    GENERIC_HEADER, SITE_CONFIG, TASK_CONFIG, CUT_CONFIG, RADIAL_HEADER,
    MOMENT_HEADER)


def _pack_struct(structure, overrides, prefix=''):
    """ Pack a structure defined as a list of (name, format_code) tuples. """
    fmt = prefix + ''.join(code for _, code in structure)
    values = []
    for name, code in structure:
        match = re.match(r'(\d*)([a-zA-Z])', code)
        count = int(match.group(1) or 1)
        typ = match.group(2)
        if typ == 's':
            values.append(overrides.get(name, b''))
        elif typ == 'B':
            val = overrides.get(name, 0)
            if isinstance(val, (bytes, bytearray, list, tuple)):
                values.extend(val[:count])
            else:
                values.extend([val] * count)
        else:
            values.append(overrides.get(name, 0))
    return struct.pack(fmt, *values)


def _encode_16bit(values):
    """ Encode integer gate values as little-endian byte pairs. """
    out = bytearray()
    for value in values:
        value = int(value)
        out.append(value & 0xFF)
        out.append((value >> 8) & 0xFF)
    return bytes(out)


def _build_c98d_file():
    ngates = 4
    moments = [
        (2, 2, 66, [86, 88, 90, 92]),        # dBZ -> reflectivity
        (7, 100, 0, [150, 160, 170, 180]),   # ZDR -> differential_reflectivity
        (9, 1000, 0, [900, 910, 920, 930]),  # CC  -> cross_correlation_ratio
        (10, 2, 0, [360, 361, 362, 363]),    # QDP -> differential_phase
        (11, 10, 0, [5, 6, 7, 8]),           # KDP -> specific_differential_phase
    ]

    buf = bytearray()
    buf += _pack_struct(GENERIC_HEADER, {
        'magic_word': 0x12345678, 'major_version': 1, 'minor_version': 0,
        'generic_type': 1, 'product_type': 1})
    buf += _pack_struct(SITE_CONFIG, {
        'latitude': 32.2, 'longitude': 118.7, 'height': 45})
    buf += _pack_struct(TASK_CONFIG, {
        'polarization_type': 3, 'scan_type': 0,
        'volume_start_time': 1000000000, 'cut_number': 1,
        'zdr_calibration': 0.5, 'phase_calibration': 2.0,
        'ldr_calibration': 0.0})
    buf += _pack_struct(CUT_CONFIG, {
        'log_reso': 250, 'doppler_reso': 250, 'nyquist_speed': 10.0,
        'maxi_range1': 150000, 'maxi_range2': 150000, 'elevation': 0.5})

    buf += _pack_struct(RADIAL_HEADER, {
        'radial_state': 0, 'azimuth': 0.0, 'elevation': 0.5,
        'seconds': 1000000000, 'moment_number': len(moments)})
    for data_type, scale, offset, raw in moments:
        buf += _pack_struct(MOMENT_HEADER, {
            'data_type': data_type, 'scale': scale, 'offset': offset,
            'bin_length': 2, 'length': 2 * ngates})
        buf += _encode_16bit(raw)

    buf += _pack_struct(RADIAL_HEADER, {
        'radial_state': 4, 'azimuth': 1.0, 'elevation': 0.5,
        'seconds': 1000000001, 'moment_number': 0})

    return bytes(buf)


def test_c98dfile_archive_reads_dualpol_fields():
    radar = c98dfile_archive(io.BytesIO(_build_c98d_file()))

    for name in ['reflectivity', 'differential_reflectivity',
                 'cross_correlation_ratio', 'differential_phase',
                 'specific_differential_phase']:
        assert name in radar.fields

    assert radar.fields['reflectivity']['units'] == 'dBZ'
    assert radar.fields['differential_reflectivity']['units'] == 'dB'
    assert radar.fields['reflectivity']['_FillValue'] == -9999.0

    refl = radar.fields['reflectivity']['data']
    assert refl.shape == (1, 4)
    assert np.allclose(refl, [[10.0, 11.0, 12.0, 13.0]])

    assert np.allclose(
        radar.fields['differential_reflectivity']['data'][0, 0], 1.5)
    assert np.allclose(
        radar.fields['cross_correlation_ratio']['data'][0, 0], 0.9)
    assert np.allclose(
        radar.fields['differential_phase']['data'][0, 0], 180.0)
    assert np.allclose(
        radar.fields['specific_differential_phase']['data'][0, 0], 0.5)


def test_c98dfile_archive_metadata():
    radar = c98dfile_archive(io.BytesIO(_build_c98d_file()))

    assert np.allclose(radar.latitude['data'][0], 32.2)
    assert np.allclose(radar.longitude['data'][0], 118.7)
    assert radar.altitude['data'][0] == 45

    assert radar.radar_calibration['zdr_calibration']['data'][0] == 0.5
    assert radar.radar_calibration['phase_calibration']['data'][0] == 2.0

    assert radar.instrument_parameters['nyquist_velocity']['data'][0] == 10.0
    assert radar.instrument_parameters[
        'unambiguous_range']['data'][0] == 150000

    assert len(radar.range['data']) == 4
    assert np.allclose(radar.range['data'], [0, 250, 500, 750])
    assert radar.nrays == 1
    assert radar.nsweeps == 1
    assert radar.scan_type == 'ppi'