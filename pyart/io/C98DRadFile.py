""" """

import struct
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np


class C98DRadFile:
    """
    Class for accessing data in a C 98D radar file.

    Parameters
    ----------
    filename : str
        Filename of C 98D file to read.

    Attributes
    ----------
    _fh : file-like
        File like object from which data is read.

    """

    def __init__(self, filename):
        """initalize the object."""
        # read the entire file into memory
        if hasattr(filename, "read"):
            fh = filename
        else:
            fh = open(filename, "rb")

        self._fh = fh
        buf = fh.read()  # string buffer containing file data

        self.pos = 0

        self.gen_header = _unpack_from_buf(buf, self.pos, GENERIC_HEADER)
        self.pos += _structure_size(GENERIC_HEADER)

        self.site_config = _unpack_from_buf(buf, self.pos, SITE_CONFIG)
        self.pos += _structure_size(SITE_CONFIG)

        self.task_config = _unpack_from_buf(buf, self.pos, TASK_CONFIG)
        self.pos += _structure_size(TASK_CONFIG)

        self.cutnum = self.task_config["cut_number"]
        # cut start and end index
        self.cut_start = []
        self.cut_end = []
        self.cut_moment_start = {}
        self.cut_moment_end = {}
        num = 0

        # create default dict
        self.cut_info = defaultdict(list)
        self.radial_info = defaultdict(list)
        self.moment_info = defaultdict(list)
        self.moment_data = defaultdict(list)
        self.moment_headers = {}

        self._get_cut_header(buf)

        while 1:
            self._get_radial_header(buf)

            if self.radial_header["radial_state"] in [0, 3]:
                self.cut_moment_start[len(self.cut_start)] = {
                    k: len(v) for k, v in self.moment_data.items()
                }
                self.cut_start.append(num)

            for _ in range(self.radial_header["moment_number"]):
                self._get_moment_header(buf)
                self._get_moment_data(buf)

            if self.radial_header["radial_state"] == 2:
                self.cut_moment_end[len(self.cut_end)] = {
                    k: len(v) for k, v in self.moment_data.items()
                }
                self.cut_end.append(num)
                num += 1
                continue
            elif self.radial_header["radial_state"] == 4:
                self.cut_moment_end[len(self.cut_end)] = {
                    k: len(v) for k, v in self.moment_data.items()
                }
                self.cut_end.append(num)
                return

            num += 1

    def get_data(self, moment, cutnum=None, raw=False):
        """Return the decoded data for a moment with shape (nrays, ngates)."""
        if moment not in self.moment_headers:
            raise KeyError(
                f'Unknown moment "{moment}", must be in {sorted(MOMENTS_TYPE.values())}'
            )

        header = self.moment_headers[moment]
        if cutnum is None:
            data = np.array(self.moment_data[moment]).T
        else:
            if cutnum >= self.cutnum:
                raise ValueError(f"Cut number must be less than {self.cutnum + 1}")
            start = self.cut_moment_start[cutnum].get(moment, 0)
            end = self.cut_moment_end[cutnum].get(moment, start)
            data = np.array(self.moment_data[moment][start:end]).T

        if raw:
            self.ranges = data.shape[0]
            return data

        if header["bin_length"] == 1:
            self.ranges = data.shape[0]
            return data.T
        elif header["bin_length"] == 2:
            combined = np.array(data[1::2] * 256 + data[::2], dtype=np.float64)
            self.ranges = combined.shape[0]
            combined[combined == 0] = np.nan
            combined = (combined - header["offset"]) / header["scale"]
            return combined.T
        else:
            raise ValueError(
                "Moment bin_length {} is WRONG!".format(header["bin_length"])
            )

    def get_nrays(self, cut):
        """ """
        return self.cut_end[cut] - self.cut_start[cut]

    def get_datetime(self, radial):
        """get assign radial time"""
        return datetime(1970, 1, 1) + timedelta(
            seconds=self.radial_info["seconds"][radial + 1]
        )

    def get_range(self, moment, cutnum):
        """Return an array of gate ranges in meters for a moment."""
        if moment is None:
            moment = "dBZ"

        data = self.get_data(moment, cutnum[0])
        if moment in [
            "dBT",
            "dBZ",
            "ZDR",
            "LDR",
            "CC",
            "QDP",
            "KDP",
            "Zc",
            "HCL",
            "SQI",
            "CPA",
            "CP",
            "FLAG",
            "CF",
        ]:
            space = self.cut_info["log_reso"][cutnum[0]]
        else:
            space = self.cut_info["doppler_reso"][cutnum[0]]

        return np.arange(0, data.shape[1]) * space

    @property
    def scans(self):
        """ """
        return np.arange(self.cutnum)

    def close(self):
        """Close the file."""
        self._fh.close()

    @property
    def get_site_info(self):
        """get radar site info"""
        return self.site_config

    @property
    def get_location(self):
        """Return the latitude, longitude and height of the radar."""
        latitude = self.site_config["latitude"]
        longitude = self.site_config["longitude"]
        height = self.site_config["height"]
        return latitude, longitude, height

    @property
    def get_cut_start_end(self):
        """ """
        return self.cut_start, self.cut_end

    @property
    def get_azimuth(self):
        """Return an array of starting azimuth angles in degrees."""
        return self.radial_info["azimuth"]

    @property
    def get_elevation(self):
        """Return the sweep elevation angle in degrees."""
        return self.radial_info["elevation"]

    @property
    def get_volume_start_time(self):
        """get volume start time"""
        return datetime(1970, 1, 1) + timedelta(
            seconds=self.task_config["volume_start_time"]
        )

    @property
    def get_cut_info(self):
        """get cut info"""
        return self.cut_info

    @property
    def get_radial_info(self):
        return self.radial_info

    @property
    def get_moment_info(self):
        return self.moment_info

    @property
    def get_nyquist_vel(self):
        return self.cut_info["nyquist_speed"]

    @property
    def get_unambiguous_range(self):
        """Return the unambiguous range (meters) for each cut."""
        return [
            max(self.cut_info["maxi_range1"][i], self.cut_info["maxi_range2"][i])
            for i in range(self.cutnum)
        ]

    @property
    def get_calibration(self):
        """Return ZDR, PhiDP and LDR system calibration offsets."""
        return {
            "zdr_calibration": self.task_config["zdr_calibration"],
            "phase_calibration": self.task_config["phase_calibration"],
            "ldr_calibration": self.task_config["ldr_calibration"],
        }

    @property
    def get_moment_type(self):
        """get moment type"""
        moment_type = []
        for i in np.unique(self.moment_info["data_type"]):
            moment_type.append(MOMENTS_TYPE[i])
        return moment_type

    @property
    def scan_type(self):
        return SCAN_TYPE[self.task_config["scan_type"]]

    def _get_cut_header(self, buf):
        """get cut header"""
        for _ in range(self.cutnum):
            self.cut = _unpack_from_buf(buf, self.pos, CUT_CONFIG)
            self.pos += _structure_size(CUT_CONFIG)
            self.cut_info = _combine_dict(self.cut_info, self.cut)

    def _get_radial_header(self, buf):
        """get radial header"""
        self.radial_header = _unpack_from_buf(buf, self.pos, RADIAL_HEADER)
        self.pos += _structure_size(RADIAL_HEADER)
        self.radial_info = _combine_dict(self.radial_info, self.radial_header)

    def _get_moment_header(self, buf):
        """get moment header"""
        self.moment_header = _unpack_from_buf(buf, self.pos, MOMENT_HEADER)
        self.pos += _structure_size(MOMENT_HEADER)
        self.moment_info = _combine_dict(self.moment_info, self.moment_header)
        moment_name = MOMENTS_TYPE.get(self.moment_header["data_type"])
        if moment_name is not None:
            self.moment_headers[moment_name] = self.moment_header

    def _get_moment_data(self, buf):
        """get moment data"""
        key, leng = (
            MOMENTS_TYPE[self.moment_header["data_type"]],
            self.moment_header["length"],
        )
        data = np.frombuffer(
            buf[self.pos : self.pos + leng], dtype=np.uint8, count=leng
        )
        self.moment_data[key].append(data)
        self.pos += leng

    def get_cut_moment_offsets(self, cut):
        """
        Return (start, end) indices into the per-moment ray lists for a cut.

        Ray lists in ``moment_data`` only contain rays that carried that
        moment, so cut boundaries are tracked separately from the radial
        record numbers.

        """
        return self.cut_moment_start[cut], self.cut_moment_end[cut]


def _structure_size(structure):
    """Find the size of a structure in bytes."""
    return struct.calcsize("".join([i[1] for i in structure]))


def _unpack_from_buf(buf, pos, structure):
    """Unpack a structure from a buffer."""
    size = _structure_size(structure)
    return _unpack_structure(buf[pos : pos + size], structure)


def _unpack_structure(string, structure):
    """Unpack a structure from a string"""
    fmt = "".join([i[1] for i in structure])
    lst = struct.unpack(fmt, string)
    return dict(zip([i[0] for i in structure], lst))


def _combine_dict(dicts, item):
    """combine multie dict"""
    for key, value in item.items():
        dicts[key].append(value)

    return dicts


# format of structure elements
# Figure E-1, page E-1
BYTE = "B"  # not in table but used in Product Description
INT2 = "h"
INT4 = "i"
UINT4 = "I"
REAL4 = "f"
LONG8 = "q"

GENERIC_HEADER = (
    ("magic_word", INT4),
    ("major_version", INT2),
    ("minor_version", INT2),
    ("generic_type", INT4),
    ("product_type", INT4),
    ("reserved", "16s"),
)

GENERIC_DATA_TYPE = {1: "Base Data", 2: "Product"}

SITE_CONFIG = (
    ("site_code", "8s"),
    ("site_name", "32s"),
    ("latitude", REAL4),
    ("longitude", REAL4),
    ("height", INT4),
    ("ground", INT4),
    ("frequency", REAL4),
    ("beam_width_hori", REAL4),
    ("beam_width_vert", REAL4),
    ("reserved", "60s"),
)

TASK_CONFIG = (
    ("task_name", "32s"),
    ("task_description", "128s"),
    ("polarization_type", INT4),
    ("scan_type", INT4),
    ("pulse_width", INT4),
    ("volume_start_time", INT4),
    ("cut_number", INT4),
    ("horiontal_noise", REAL4),
    ("vertical_noise", REAL4),
    ("horizontal_calibration", REAL4),
    ("vertical_calibration", REAL4),
    ("horizontal_noise_temperature", REAL4),
    ("vertical_noise_temperature", REAL4),
    ("zdr_calibration", REAL4),
    ("phase_calibration", REAL4),
    ("ldr_calibration", REAL4),
    ("reserved", "40s"),
)

CUT_CONFIG = (
    ("process_mode", INT4),
    ("wave_form", INT4),
    ("prf1", INT4),
    ("prf2", INT4),
    ("unfold_mode", INT4),
    ("azimuth", REAL4),
    ("elevation", REAL4),
    ("angle_start", REAL4),
    ("angle_end", REAL4),
    ("angle_reso", REAL4),
    ("scan_speed", REAL4),
    ("log_reso", INT4),
    ("doppler_reso", INT4),
    ("maxi_range1", INT4),
    ("maxi_range2", INT4),
    ("start_range", INT4),
    ("sample1", INT4),
    ("sample2", INT4),
    ("phase_mode", INT4),
    ("atmos_loss", REAL4),
    ("nyquist_speed", REAL4),
    ("moments_mask", LONG8),
    ("moments_size_mask", LONG8),
    ("sqi_threshold", REAL4),
    ("sig_threshold", REAL4),
    ("csr_threshold", REAL4),
    ("log_threshold", REAL4),
    ("cpa_threshold", REAL4),
    ("pmi_threshold", REAL4),
    ("threshold_reserved", "8s"),
    ("dbt_mask", INT4),
    ("dbz_mask", INT4),
    ("velocity_mask", INT4),
    ("spectrum_width_mask", INT4),
    ("zdr_mask", INT4),
    ("mask_reserved", "12s"),
    ("scan_sync", INT4),
    ("direction", INT4),
    ("ground_clutter_classifer_type", INT2),
    ("ground_clutter_filter_type", INT2),
    ("ground_clutter_filter_notch_width", INT2),
    ("ground_clutter_filter_window", INT2),
    ("spare", "72s"),
)

RADIAL_HEADER = (
    ("radial_state", INT4),
    ("spot_blank", INT4),
    ("sequence_number", INT4),
    ("radial_number", INT4),
    ("elevation_number", INT4),
    ("azimuth", REAL4),
    ("elevation", REAL4),
    ("seconds", INT4),
    ("microseconds", INT4),
    ("data_length", INT4),
    ("moment_number", INT4),
    ("reserved", "20s"),
)

MOMENT_HEADER = (
    ("data_type", INT4),
    ("scale", INT4),
    ("offset", INT4),
    ("bin_length", INT2),
    ("flags", INT2),
    ("length", INT4),
    ("reserved", "12s"),
)

MOMENTS_TYPE = {
    1: "dBT",
    2: "dBZ",
    3: "V",
    4: "W",
    5: "SQI",
    6: "CPA",
    7: "ZDR",
    8: "LDR",
    9: "CC",
    10: "QDP",
    11: "KDP",
    12: "CP",
    13: "FLAG",
    14: "HCL",
    15: "CF",
    16: "Zc",
    17: "Vc",
    18: "Wc",
}

FILTERS = {
    0: "Intererence Filter",
    1: "Censor Filter",
    2: "1D Surveillance Speckle",
    3: "1D Doppler Speckle",
    4: "2D Surveillance Speckle",
    5: "2D Doppler Speckle",
}

POLARIZATION_TYPE = {
    1: "Horizontal",
    2: "Vertical",
    3: "Simultaneously",
    4: "Alternation",
}

SCAN_TYPE = {0: "ppi", 1: "Single PPI", 2: "rhi", 3: "Single Sector"}

PROCESS_MODE = {1: "PPP", 2: "FFT"}

WAVE_FORM = {
    0: "CS",
    1: "CD",
    2: "CDX",
    3: "Rx Test",
    4: "BATCH",
    5: "Dual PRF",
    6: "Random Phase",
    7: "SZ",
}
