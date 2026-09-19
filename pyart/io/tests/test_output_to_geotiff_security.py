"""
Security regression tests for pyart.io.output_to_geotiff (FU-01).

Covers the OS command injection sink (ed8eb0, CWE-78): ``write_grid_geotiff``
used to concatenate the caller-supplied output filename into an ``os.system``
gdalwarp command line. Shell metacharacters in the filename were interpreted
by /bin/sh, yielding arbitrary command execution with the process's
privileges.

The tests are hermetic: GDAL is faked and the subprocess/shell sinks are
recorded, so no real GDAL installation or external process is required.
"""

import os

import numpy as np
import pytest

pytest.importorskip("pyart")

import pyart.io.output_to_geotiff as og  # noqa: E402
from pyart.testing import sample_objects  # noqa: E402


class _SentinelExec(Exception):
    """Raised by the recording sink to stop before a real child process."""


class _FakeBand:
    def WriteArray(self, *args, **kwargs):
        pass

    def FlushCache(self):
        pass


class _FakeDataset:
    def SetGeoTransform(self, *args, **kwargs):
        pass

    def SetProjection(self, *args, **kwargs):
        pass

    def GetRasterBand(self, idx):
        return _FakeBand()

    def FlushCache(self):
        pass


class _FakeDriver:
    def Create(self, *args, **kwargs):
        return _FakeDataset()


class _FakeGdal:
    GDT_Float32 = 6
    GDT_Byte = 1

    def GetDriverByName(self, name):
        return _FakeDriver()


def _grid_with_field():
    grid = sample_objects.make_empty_grid(
        (2, 40, 40), ((0, 1000), (-40000, 40000), (-40000, 40000))
    )
    grid.fields["reflectivity"] = {
        "data": np.ma.masked_array(np.zeros((2, 40, 40), dtype="float32"))
    }
    return grid


def _patch_sinks(monkeypatch, tmp_path):
    """Install fake GDAL plus recording subprocess/os.system sinks."""
    # raising=False keeps the harness hermetic when GDAL is absent.
    monkeypatch.setattr(og, "gdal", _FakeGdal(), raising=False)
    monkeypatch.setattr(og, "IMPORT_FLAG", True)

    calls = {"subprocess": [], "os_system": []}

    class _FakeSubprocess:
        @staticmethod
        def run(cmd, *args, **kwargs):
            calls["subprocess"].append((cmd, kwargs))
            raise _SentinelExec

    def fake_system(cmd):
        calls["os_system"].append(cmd)
        raise _SentinelExec

    # raising=False: the pre-fix module has no ``subprocess`` attribute;
    # patching it in keeps the RED failure attributable to os.system use.
    monkeypatch.setattr(og, "subprocess", _FakeSubprocess, raising=False)
    monkeypatch.setattr(og.os, "system", fake_system)
    return calls


def test_write_grid_geotiff_warp_does_not_use_shell(monkeypatch, tmp_path):
    """A crafted filename must not be able to escape an argv execution."""
    calls = _patch_sinks(monkeypatch, tmp_path)
    marker = tmp_path / "pwned"
    crafted = f"out.tif; touch {marker}; #"

    with pytest.raises(_SentinelExec):
        og.write_grid_geotiff(
            _grid_with_field(),
            str(tmp_path / crafted),
            "reflectivity",
            warp=True,
        )

    assert calls["os_system"] == [], "os.system must not be used for gdalwarp"
    assert len(calls["subprocess"]) == 1
    cmd, kwargs = calls["subprocess"][0]
    assert isinstance(cmd, (list, tuple)), "command must be an argv list"
    assert kwargs.get("shell", False) is False
    assert not marker.exists(), "shell metacharacters must not execute commands"


def test_write_grid_geotiff_warp_quotes_or_argv_escapes_metacharacters(
    monkeypatch, tmp_path
):
    """Metacharacters must remain a single literal argv element."""
    calls = _patch_sinks(monkeypatch, tmp_path)
    crafted = "out.tif; touch /tmp/pwned; #"

    with pytest.raises(_SentinelExec):
        og.write_grid_geotiff(
            _grid_with_field(), str(tmp_path / crafted), "reflectivity", warp=True
        )

    cmd, _ = calls["subprocess"][0]
    argv = list(cmd)
    assert argv[0] in (
        "gdalwarp",
        os.path.join(os.sep, "usr", "bin", "gdalwarp"),
    ) or argv[0].endswith("gdalwarp")
    literal = [a for a in argv if crafted in a]
    assert len(literal) == 2, "input and output filenames must be literal argv elements"
    assert not any(
        a in (";", "&&", "|") for a in argv
    ), "no separate shell operator tokens"


def test_create_sld_uses_splitext_for_dotted_directories(tmp_path):
    """'/dir.v2/out.tif' must map to '/dir.v2/out.sld', not '/dir.sld'."""
    monkey_target = tmp_path / "dir.v2"
    monkey_target.mkdir()
    outfile = monkey_target / "out.tif"
    outfile.write_bytes(b"")

    og._create_sld("viridis", 0, 1, str(outfile))

    expected = monkey_target / "out.sld"
    assert expected.exists()
    assert not (tmp_path / "dir.sld").exists()
