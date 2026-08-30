"""
Functions to read and write radar and grid data to and from a number of file
formats.

In most cases the :py:func:`pyart.io.read` function should be used to read
in radar data from a file. In certain cases the function the read function
for the format in question should be used.

"""

from .arm_sonde import read_arm_sonde, read_arm_sonde_vap  # noqa
from .auto_read import read  # noqa
from .c98d_archive import c98dfile_archive, read_c98d  # noqa
from .cfradial import read_cfradial, write_cfradial  # noqa
from .chl import read_chl  # noqa
from .cinrad_bridge import (  # noqa
    read_cinrad, read_xband, read_pa, read_mocmosaic)
from .sband_archive import read_sband_archive, read_sband_radar  # noqa
from .xband_native import read_xband_724xsp, read_xband_scrxd01  # noqa
from .common import prepare_for_read  # noqa
from .grid_io import read_grid, write_grid  # noqa
from .mdv_grid import read_grid_mdv, write_grid_mdv  # noqa
from .mdv_radar import read_mdv  # noqa
from .nexrad_archive import read_nexrad_archive  # noqa
from .nexrad_cdm import read_nexrad_cdm  # noqa
from .nexradl3_read import read_nexrad_level3  # noqa
from .output_to_geotiff import write_grid_geotiff  # noqa
from .rsl import read_rsl  # noqa
from .sigmet import read_sigmet  # noqa
from .uf import read_uf  # noqa
from .uf_write import write_uf  # noqa

try:  # optional remote-acquisition layer (requests/s3fs/pooch)
    from .remote import (  # noqa
        RemoteDataError, RadarSite, RadarSource, get_source, list_sources,
        read_time_span, register_source)
except ImportError:  # pragma: no cover - optional deps missing
    pass

__all__ = [s for s in dir() if not s.startswith("_")]
