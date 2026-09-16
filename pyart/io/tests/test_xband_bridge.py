"""
Tests for the PyCINRAD/pycwr X-band bridge routing.

These tests mock the optional dependencies (no real CINRAD sample files
required); real-data regression is gated on the ``CINRAD_TEST_FILE``
environment variable.
"""

import os
from unittest import mock

import pytest

pytest.importorskip("pyart")

from pyart.io.cinrad_bridge import (
    _detect_reader,
    _read_via_pywr,
    is_xband_filename,
    read_xband,
)


def test_is_xband_filename():
    assert is_xband_filename("Z_RADR_I_ZA601_20240415183600_O_DOR_AXPT0364_CRA_FMT.bin")
    assert is_xband_filename("Z_RADR_I_ZBJ02_20210815155836_O_DOR_DXK_CAR.bin")
    assert is_xband_filename("XAD_some_file.bin")
    assert not is_xband_filename("Z_RADR_I_Z9515_20160623063100_O_DOR_SA_CAP.bin")
    assert not is_xband_filename("NUIST.20140928.070704.AR2")


def test_missing_dependency_message():
    with mock.patch.dict("sys.modules", {"cinrad": None, "cinrad.io": None}):
        with pytest.raises(ImportError, match="arm_pyart\\[cinrad\\]"):
            _detect_reader("foo_AXPT_bar.bin")()


def test_read_xband_routes_via_mocked_cinrad():
    fake_radar = mock.MagicMock()
    fake_radar.metadata = {}
    fake_obj = object()

    with (
        mock.patch(
            "pyart.io.cinrad_bridge._detect_reader", return_value=lambda: fake_obj
        ),
        mock.patch(
            "pyart.io.cinrad_bridge._import_cinrad",
            return_value=(None, None, mock.MagicMock(return_value=fake_radar)),
        ),
    ):
        radar = read_xband("Z_RADR_I_ZA601_20240415183600_O_DOR_AXPT0364_CRA_FMT.bin")

    assert radar is fake_radar
    assert radar.metadata["radar_band"] == "X"
    assert radar.metadata["original_container"] == "CINRAD-X"


def test_read_xband_falls_back_to_pycwr():
    fake_radar = mock.MagicMock()
    fake_radar.metadata = {}

    def broken_reader():
        raise ValueError("not decodable by cinrad")

    with (
        mock.patch("pyart.io.cinrad_bridge._detect_reader", return_value=broken_reader),
        mock.patch("pyart.io.cinrad_bridge._read_via_pywr", return_value=fake_radar),
    ):
        radar = read_xband("Z_RADR_I_ZBJ02_20210815155836_O_DOR_DXK_CAR.bin")

    assert radar is fake_radar
    assert radar.metadata["radar_band"] == "X"


def test_read_xband_non_xband_reraises():
    def broken_reader():
        raise ValueError("decoding failed")

    with mock.patch(
        "pyart.io.cinrad_bridge._detect_reader", return_value=broken_reader
    ):
        with pytest.raises(ValueError):
            read_xband("Z_RADR_I_Z9515_20160623063100_O_DOR_SA_CAP.bin")


def test_read_via_pywr_error_message():
    with mock.patch.dict("sys.modules", {"pycwr": None, "pycwr.io": None}):
        with pytest.raises(ImportError, match="arm_pyart\\[cinrad\\]"):
            _read_via_pywr("whatever.bin")


@pytest.mark.realdata
@pytest.mark.skipif(
    os.environ.get("CINRAD_TEST_FILE") is None, reason="CINRAD_TEST_FILE not set"
)
def test_read_xband_real_file():
    radar = read_xband(os.environ["CINRAD_TEST_FILE"])
    assert "reflectivity" in radar.fields
    assert radar.metadata["radar_band"] == "X"
