"""
pyart.xradar
============

Thin interoperability layer between :mod:`xradar` datatrees and
:class:`pyart.core.Radar` (idea aligned with upstream Py-ART's xradar
compatibility work; kept minimal here).

Requires the optional ``xradar`` dependency::

    pip install arm_pyart[xradar]

"""

import numpy as np


def _import_xradar():
    try:
        import xradar
    except ImportError as exc:
        raise ImportError(
            'xradar is required for this bridge; install it with '
            '"pip install arm_pyart[xradar]"') from exc
    return xradar


# ODIM/xradar moment name -> Py-ART field name
_MOMENT_MAP = {
    'DBZH': 'reflectivity',
    'DBZV': 'reflectivity_v',
    'VRADH': 'velocity',
    'WRADH': 'spectrum_width',
    'ZDR': 'differential_reflectivity',
    'PHIDP': 'differential_phase',
    'RHOHV': 'cross_correlation_ratio',
    'KDP': 'specific_differential_phase',
}


def to_pyart_radar(datatree):
    """
    Convert an :mod:`xradar` datatree to a :class:`pyart.core.Radar`.

    Only the first sweep group (``sweep_0``) of each sweep is concatenated;
    fixed angles, azimuth and elevation are taken from the per-sweep datasets.

    """
    xradar = _import_xradar()
    from ..core.radar import Radar
    from ..config import FileMetadata, get_fillvalue

    filemetadata = FileMetadata('xradar')
    sweep_groups = [key for key in datatree.children
                    if key.startswith('sweep_')]
    if not sweep_groups:
        raise ValueError('datatree contains no sweep groups')

    datasets = [datatree[key].to_dataset() for key in sorted(sweep_groups)]

    nrays = int(sum(ds.sizes['azimuth'] for ds in datasets))
    ngates = max(int(ds.sizes['range']) for ds in datasets)

    time = filemetadata('time')
    time['data'] = np.concatenate(
        [ds['time'].values.astype('float64').ravel() for ds in datasets])
    time['units'] = str(datasets[0]['time'].encoding.get('units',
                        'seconds since 1970-01-01T00:00:00Z'))

    _range = filemetadata('range')
    first_gate = float(datasets[0]['range'].values[0])
    spacing = float(np.median(np.diff(datasets[0]['range'].values)))
    _range['data'] = first_gate + np.arange(ngates, dtype='float32') * spacing
    _range['meters_to_center_of_first_gate'] = first_gate
    _range['meters_between_gates'] = spacing

    metadata = filemetadata('metadata')
    metadata['original_container'] = 'xradar'

    latitude = filemetadata('latitude')
    longitude = filemetadata('longitude')
    altitude = filemetadata('altitude')
    latitude['data'] = np.array([float(datasets[0]['latitude'])],
                                dtype='float64')
    longitude['data'] = np.array([float(datasets[0]['longitude'])],
                                 dtype='float64')
    altitude['data'] = np.array([float(datasets[0]['altitude'])],
                                dtype='float64')

    nsweeps = len(datasets)
    rays_per_sweep = [int(ds.sizes['azimuth']) for ds in datasets]

    sweep_number = filemetadata('sweep_number')
    sweep_mode = filemetadata('sweep_mode')
    sweep_start = filemetadata('sweep_start_ray_index')
    sweep_end = filemetadata('sweep_end_ray_index')
    fixed_angle = filemetadata('fixed_angle')

    sweep_number['data'] = np.arange(nsweeps, dtype='int32')
    sweep_mode['data'] = np.array(nsweeps * ['azimuth_surveillance'],
                                  dtype='S')
    sweep_end['data'] = np.cumsum(rays_per_sweep, dtype='int32') - 1
    sweep_start['data'] = np.concatenate(
        [[0], np.cumsum(rays_per_sweep)[:-1]]).astype('int32')
    fixed_angle['data'] = np.array(
        [float(ds['sweep_fixed_angle']) for ds in datasets], dtype='float32')

    azimuth = filemetadata('azimuth')
    elevation = filemetadata('elevation')
    azimuth['data'] = np.concatenate(
        [ds['azimuth'].values.astype('float64').ravel() for ds in datasets])
    elevation['data'] = np.concatenate(
        [np.broadcast_to(float(ds['sweep_fixed_angle']),
                         ds.sizes['azimuth']).astype('float64')
         for ds in datasets])

    fields = {}
    for moment, field_name in _MOMENT_MAP.items():
        if not all(moment in ds for ds in datasets):
            continue
        dic = filemetadata(field_name)
        dic['_FillValue'] = get_fillvalue()
        columns = []
        for ds in datasets:
            values = np.ma.asarray(ds[moment].values, dtype=float)
            values = values.reshape(ds.sizes['azimuth'], ds.sizes['range'])
            if values.shape[1] < ngates:
                pad = np.ma.masked_all(
                    (values.shape[0], ngates - values.shape[1]))
                values = np.ma.hstack([values, pad])
            columns.append(values)
        dic['data'] = np.ma.vstack(columns)
        fields[field_name] = dic

    return Radar(time, _range, fields, metadata, 'ppi',
                 latitude, longitude, altitude,
                 sweep_number, sweep_mode, fixed_angle,
                 sweep_start, sweep_end,
                 azimuth, elevation)


def from_pyart_radar(radar):
    """
    Convert a :class:`pyart.core.Radar` to an :mod:`xradar` datatree.

    Requires xradar; sweeps are emitted as ``sweep_N`` groups with CF/Radial
    style variables.

    """
    xradar = _import_xradar()
    import xarray as xr

    sweep_slices = [radar.get_slice(s) for s in range(radar.nsweeps)]

    root_vars = {
        'latitude': ((), float(radar.latitude['data'][0])),
        'longitude': ((), float(radar.longitude['data'][0])),
        'altitude': ((), float(radar.altitude['data'][0])),
    }
    root = xr.Dataset(
        {k: xr.DataArray(v) for k, v in root_vars.items()})

    volume = root
    for i, sl in enumerate(sweep_slices):
        ds = xr.Dataset(
            {
                'azimuth': (('azimuth',),
                            radar.azimuth['data'][sl].astype('float32')),
                'elevation': (('azimuth',),
                              radar.elevation['data'][sl].astype('float32')),
                'range': (('range',), radar.range['data'].astype('float32')),
                'sweep_fixed_angle': ((), float(radar.fixed_angle['data'][i])),
            }
        )
        for field_name, dic in radar.fields.items():
            ds[field_name] = (
                ('azimuth', 'range'), np.ma.asarray(dic['data'][sl]))
        volume = xr.merge([volume, ds]) if i == 0 else volume
        # name the sweep dataset via a dict-style group assignment below
    # xradar datatree creation is delegated to xradar when available
    datatree = xradar.io.create_xradar_volume_tree(volume)
    return datatree


__all__ = ['to_pyart_radar', 'from_pyart_radar']