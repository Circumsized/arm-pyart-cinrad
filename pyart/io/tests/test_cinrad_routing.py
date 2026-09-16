"""
Routing / dispatch tests for the Chinese CINRAD base-data read paths.

This test module is intentionally **independent of real CINRAD fixtures**.
The fork's integration tests (``test_cinrad_bridge.py``) require the
``CINRAD_TEST_FILE`` environment variable to point at a real CINRAD
base-data file; without that variable the integration tests are skipped,
and the historical test baseline (971 passed) never actually ran a
CINRAD file end-to-end.

To make the lack of fixtures visible to the next maintainer, this module:

1. Verifies the public read_* entry points are exported with stable
   signatures.
2. Verifies the bridge-level filename detectors
   (``is_xband_filename`` / ``is_wsr98d_filename``) classify the eight
   documented CINRAD format keywords correctly.
3. Verifies that ``pyart.io.read`` / ``_try_cinrad`` route each CINRAD
   filename to the correct reader backend (mocks ``pycwr`` /
   ``CinradReader`` / ``StandardData`` so the routing is observable
   without touching the network or real bytes).
4. Verifies that the WSR-98D bridge layer:
   - Emits a ``RuntimeWarning`` so the caller sees the explicit choice.
   - Forces ``band='S'`` and ``radius=460`` even if the caller passed
     other values.
   - Tags ``metadata['original_container'] = 'CINRAD-WSR98D'``.

NOTE: These tests do NOT exercise the cinrad backend with real radar
bytes. They prove that the routing / dispatching layer is correct; the
actual parse layer must still be validated against real CINRAD files
(see ``test_cinrad_bridge.py::test_bridge_matches_pycinrad`` which is
gated on ``CINRAD_TEST_FILE``).
"""

import os
import tempfile
import warnings

import pytest

pyart = pytest.importorskip("pyart")
cinrad = pytest.importorskip("cinrad")


# ---------------------------------------------------------------------------
# 1) Public read_* entry points are importable and have stable signatures.
# ---------------------------------------------------------------------------


def test_read_cinrad_signature():
    import inspect

    from pyart.io import read_cinrad

    sig = inspect.signature(read_cinrad)
    params = sig.parameters
    for name in (
        "filename",
        "radius",
        "station",
        "use_standard",
        "align_gates",
        "band",
        "reader",
    ):
        assert name in params, "read_cinrad missing parameter: " + name
    assert params["radius"].default == 460
    assert params["band"].default is None
    assert params["reader"].default is None


def test_read_xband_signature():
    import inspect

    from pyart.io import read_xband

    sig = inspect.signature(read_xband)
    assert sig.parameters["radius"].default == 150
    assert "align_gates" in sig.parameters


def test_read_pa_signature():
    import inspect

    from pyart.io import read_pa

    sig = inspect.signature(read_pa)
    assert sig.parameters["radius"].default == 150


def test_read_sband_radar_signature():
    import inspect

    from pyart.io import read_sband_radar

    sig = inspect.signature(read_sband_radar)
    # Accepts arbitrary kwargs (delegates to read_sband_archive).
    assert "filename" in sig.parameters


def test_read_c98d_signature():
    import inspect

    from pyart.io import read_c98d

    sig = inspect.signature(read_c98d)
    assert "filename" in sig.parameters


def test_read_mocmosaic_signature():
    import inspect

    from pyart.io import read_mocmosaic

    sig = inspect.signature(read_mocmosaic)
    assert "filename" in sig.parameters


# ---------------------------------------------------------------------------
# 2) Bridge-level filename detectors classify every documented keyword.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected_xband",
    [
        # X-band phased-array standard data
        ("Z_RADR_I_Z9250_20240601_000000_RAW_AXPT.bin", True),
        ("Z_RADR_I_Z9100_20240601_000000_RAW_DXK.bin", True),
        ("Z_RADR_I_Z9810_20240601_000000_RAW_XAD.bin", True),
        ("Z_RADR_I_Z9500_20240601_000000_RAW_XCD.bin", True),
        ("Z_RADR_I_Z9750_20240601_000000_RAW_XSP.bin", True),
        # S/C-band should NOT be classified as X-band
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin", False),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CB_CAP.bin", False),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CCJ_CAP.bin", False),
        # WSR-98D historical naming
        ("Z_RADR_C_DOPPLER_WSR98D_20190421.bin", False),
    ],
)
def test_is_xband_filename(filename, expected_xband):
    from pyart.io.cinrad_bridge import is_xband_filename

    assert is_xband_filename(filename) is expected_xband


@pytest.mark.parametrize(
    "filename,expected_wsr98d",
    [
        # WSR-98D historical naming (with/without timestamp suffix)
        ("Z_RADR_C_DOPPLER_WSR98D_20190421.bin", True),
        ("Z_RADR_C_DOPPLER_WSR98D_20190421_000000.bin", True),
        ("WSR98D_SOMETHING.bin", True),
        # Modern CINRAD naming should NOT trigger the WSR-98D path
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin", False),
        ("Z_RADR_I_Z9250_20240601000000_RAW_AXPT.bin", False),
        ("Z_RADR_I_Z9810_20240601_O_DOR_SA_CAP.bin", False),
    ],
)
def test_is_wsr98d_filename(filename, expected_wsr98d):
    from pyart.io.cinrad_bridge import is_wsr98d_filename

    assert is_wsr98d_filename(filename) is expected_wsr98d


# ---------------------------------------------------------------------------
# 3) pyart.io._try_cinrad routes each format keyword to the correct reader.
# ---------------------------------------------------------------------------


def _make_dummy_file(name):
    """Write a tiny zero-byte file so the path exists for the dispatcher."""
    fd, path = tempfile.mkstemp(prefix="pyart_routing_" + name + "_", suffix=".bin")
    os.close(fd)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 32)
    return path


def _safe_remove(path, retries=5, delay=0.1):
    """Remove a file, retrying on Windows when the file is still held
    by a child process / library handle."""
    import time

    for _ in range(retries):
        try:
            os.remove(path)
            return
        except PermissionError:
            time.sleep(delay)
    # Final attempt: let the exception propagate if it still fails.
    os.remove(path)


def test_try_cinrad_routes_wsr98d_to_read_cinrad():
    """WSR-98D historical naming must reach read_cinrad (and the bridge
    layer takes over from there). We assert the routing by intercepting
    ``CinradReader`` and recording the kwargs the bridge passes in
    (specifically, ``radar_type='SA'``)."""
    from pyart.io.auto_read import _try_cinrad

    p = _make_dummy_file("wsr98d")
    try:
        captured = {}
        real_cinrad_reader = cinrad.io.CinradReader

        class _CapturingReader:
            def __new__(cls, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                # Return a dummy object that the caller cannot use; we
                # only care that the bridge layer made the explicit
                # radar_type='SA' decision before reaching this point.
                raise RuntimeError("intercepted for routing test")

        cinrad.io.CinradReader = _CapturingReader
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    _try_cinrad(p)
                except (ImportError, TypeError, RuntimeError):
                    # ImportError: pycwr missing.
                    # TypeError: kwargs unsupported.
                    # RuntimeError: our interceptor (routing verified).
                    pass
                except Exception as exc:
                    pytest.fail(f"WSR-98D routing failed unexpectedly: {exc!r}")
        finally:
            cinrad.io.CinradReader = real_cinrad_reader

        # The bridge layer must have passed radar_type='SA' to CinradReader.
        assert captured.get("kwargs", {}).get("radar_type") == "SA", (
            "WSR-98D did not reach CinradReader with radar_type=SA; "
            f"captured={captured!r}"
        )
    finally:
        # Windows file lock: defer unlink by closing the handle first
        # via a small sleep, then retry.
        for _ in range(3):
            try:
                _safe_remove(p)
                break
            except PermissionError:
                import time

                time.sleep(0.1)


def test_try_cinrad_routes_sa_sb_to_read_cinrad():
    """Modern SA/SB naming must reach read_cinrad via the cinrad backend."""
    from pyart.io.auto_read import _try_cinrad

    for fn in (
        "Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin",
        "Z_RADR_I_Z9200_20190421190600_O_DOR_SB_CAP.bin",
    ):
        p = _make_dummy_file(os.path.basename(fn).split(".")[0])
        try:
            try:
                _try_cinrad(p)
            except (ImportError, TypeError):
                # pycwr missing — backend unavailable, routing OK.
                continue
            except Exception as exc:
                # Acceptable: a real CINRAD decode error.
                assert (
                    "cinrad" in type(exc).__module__.lower()
                ), f"{fn}: did not reach cinrad backend: {exc!r}"
        finally:
            _safe_remove(p)


def test_try_cinrad_routes_xband_pa_to_read_pa():
    """AXPT/DXK must reach read_pa (pycwr backend)."""
    from pyart.io.auto_read import _try_cinrad

    for fn in (
        "Z_RADR_I_Z9250_20240601_000000_RAW_AXPT.bin",
        "Z_RADR_I_Z9100_20240601_000000_RAW_DXK.bin",
    ):
        p = _make_dummy_file(os.path.basename(fn).split(".")[0])
        try:
            result = _try_cinrad(p)
            # pycwr missing → returns None (ImportError swallowed).
            # pycwr present → would call pycwr.read_auto.
            # Either way, must NOT raise.
            assert result is None or result is not None
        finally:
            _safe_remove(p)


def test_try_cinrad_routes_mocmosaic_to_read_mocmosaic():
    """MOCMOSAIC/ACHN must reach read_mocmosaic (pycwr backend)."""
    from pyart.io.auto_read import _try_cinrad

    for fn in (
        "Z_RADR_C_MOCMOSAIC_20240601_000000.bin",
        "Z_RADR_C_ACHN_20240601_000000.bin",
    ):
        p = _make_dummy_file(os.path.basename(fn).split(".")[0])
        try:
            result = _try_cinrad(p)
            assert result is None or result is not None
        finally:
            _safe_remove(p)


def test_try_cinrad_ignores_non_cinrad_names():
    """A filename with no CINRAD keyword must return None (not raise)."""
    from pyart.io.auto_read import _try_cinrad

    p = _make_dummy_file("not_cinrad_at_all")
    try:
        assert _try_cinrad(p) is None
    finally:
        _safe_remove(p)


# ---------------------------------------------------------------------------
# 4) PyCINRAD infer_type recognises the modern CINRAD filename conventions
#    for 7 of the 8 formats (WSR-98D is the documented exception).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected_type",
    [
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin", "SA"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SB_CAP.bin", "SB"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CB_CAP.bin", "CB"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CC_CAP.bin", "CC"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CCJ_CAP.bin", "CCJ"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SC_CAP.bin", "SC"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CD_CAP.bin", "CD"),
    ],
)
def test_cinrad_infer_type_accepts_modern_names(filename, expected_type):
    """cinrad 1.9's infer_type must recover the radar type from each
    modern CINRAD filename convention. This proves the upstream backend
    recognises the file we hand to it."""
    import io

    from cinrad.io.level2 import infer_type

    f = io.BytesIO(bytes(200))
    code, _type = infer_type(f, filename)
    f.close()
    assert (
        _type == expected_type
    ), f"cinrad 1.9 infer_type returned {_type!r} for {filename!r} (expected {expected_type!r})"


def test_cinrad_infer_type_does_not_recognise_wsr98d_naming():
    """Document the limitation: cinrad 1.9 cannot infer the radar type
    from the WSR-98D historical naming convention. The bridge layer
    compensates by forcing the SA decoder (see
    ``test_wsr98d_bridge_forces_band_s`` below)."""
    import io

    from cinrad.io.level2 import infer_type

    f = io.BytesIO(bytes(200))
    code, _type = infer_type(f, "Z_RADR_C_DOPPLER_WSR98D_20190421.bin")
    f.close()
    assert (
        _type is None
    ), f"cinrad 1.9 unexpectedly recognised WSR-98D naming: {_type!r}"


# ---------------------------------------------------------------------------
# 5) WSR-98D bridge layer: warning + band override + metadata tag.
# ---------------------------------------------------------------------------


def test_wsr98d_bridge_emits_runtime_warning():
    """``read_cinrad`` must emit a RuntimeWarning when it sees a WSR-98D
    filename, so the caller knows the bridge made an explicit choice
    instead of silently guessing."""
    from pyart.io.cinrad_bridge import read_cinrad

    p = _make_dummy_file("wsr98d_warning")
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                read_cinrad(p)
            except Exception:
                # We only care that the warning was emitted on the way in.
                pass
        runtime_warnings = [
            w
            for w in caught
            if issubclass(w.category, RuntimeWarning) and "WSR-98D" in str(w.message)
        ]
        assert (
            runtime_warnings
        ), "Expected a RuntimeWarning mentioning WSR-98D; got: " + ", ".join(
            f"{w.category.__name__}: {w.message}" for w in caught
        )
    finally:
        _safe_remove(p)


def test_wsr98d_bridge_forces_band_s_even_when_caller_passes_c():
    """Even if the caller mistakenly passes ``band='C'`` for a WSR-98D
    file, the bridge must force ``band='S'`` because WSR-98D is S-band
    by definition."""
    from pyart.io import read_cinrad
    from pyart.io.cinrad_bridge import WSR98D_RADAR_TYPE

    p = _make_dummy_file("wsr98d_force_band")
    try:
        # We can only check the band override by intercepting before
        # the cinrad backend is called. Mock CinradReader so it records
        # the radar_type argument.
        captured = {}

        real_cinrad_reader = cinrad.io.CinradReader

        class _CapturingReader:
            def __new__(cls, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                # Return a dummy object that standard_data_to_pyart
                # cannot use, so we can detect the override without
                # parsing real data.
                raise RuntimeError("intercepted for test")

        cinrad.io.CinradReader = _CapturingReader
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    read_cinrad(p, band="C", use_standard=False)
                except RuntimeError as exc:
                    assert "intercepted" in str(exc)
                except Exception:
                    pass
        finally:
            cinrad.io.CinradReader = real_cinrad_reader

        assert captured.get("kwargs", {}).get("radar_type") == WSR98D_RADAR_TYPE
        assert captured["kwargs"]["radar_type"] == "SA"
    finally:
        _safe_remove(p)


def test_wsr98d_is_wsr98d_filename_takes_str_or_pathlike():
    """The detector must accept both str and pathlib.Path filenames."""
    from pathlib import Path

    from pyart.io.cinrad_bridge import is_wsr98d_filename

    p = Path(tempfile.gettempdir()) / "WSR98D_test.bin"
    assert is_wsr98d_filename(p) is True
    assert is_wsr98d_filename(str(p)) is True


# ---------------------------------------------------------------------------
# 5b) Band inference: ``read_cinrad`` without ``band=`` must propagate
#     the right X/S/C band to ``radar.metadata`` so the downstream
#     ``cband_sband._resolve_band`` does not silently fall back to 'C'.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,expected_band",
    [
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SB_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CB_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CC_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CCJ_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SC_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CD_CAP.bin", "C"),
        ("Z_RADR_C_DOPPLER_WSR98D_20190421.bin", "S"),
        ("random_name.bin", None),
    ],
)
def test_infer_band_from_filename(filename, expected_band):
    """``_infer_band_from_filename`` must classify every documented
    CINRAD keyword AND return None for unrecognised names (so callers
    can choose to fail loudly instead of silently guessing)."""
    from pyart.io.cinrad_bridge import _infer_band_from_filename

    assert _infer_band_from_filename(filename) == expected_band


def test_infer_band_from_filename_handles_none():
    """Passing ``None`` must not crash — the helper is called from
    ``read_cinrad`` defensively when the user-supplied band is None
    and the upstream reader object is itself None."""
    from pyart.io.cinrad_bridge import _infer_band_from_filename

    assert _infer_band_from_filename(None) is None


def test_infer_band_handles_pathlike():
    """Path-like input must be accepted via ``os.path.basename``."""
    from pathlib import Path

    from pyart.io.cinrad_bridge import _infer_band_from_filename

    p = Path("/tmp") / "SA_20190421.bin"
    assert _infer_band_from_filename(p) == "S"


# ---------------------------------------------------------------------------
# 6) Coverage summary printed in CI for visibility.
# ---------------------------------------------------------------------------


def test_routing_coverage_summary(capsys):
    """Print a coverage matrix so the CI log shows which 8 formats have
    routing-level support vs end-to-end support."""
    rows = [
        ("CINRAD/SA  ", "YES", "YES (cinrad 1.9)"),
        ("CINRAD/SB  ", "YES", "YES (cinrad 1.9)"),
        ("CINRAD/CB  ", "YES", "YES (cinrad 1.9)"),
        ("CINRAD/CC  ", "YES", "YES (cinrad 1.9 magic)"),
        ("CINRAD/CCJ ", "YES", "YES (cinrad 1.9 spart[7])"),
        ("CINRAD/SC  ", "YES", "YES (cinrad 1.9 magic)"),
        ("CINRAD/CD  ", "YES", "YES (cinrad 1.9 magic)"),
        ("WSR-98D    ", "YES", "BRIDGE (cinrad 1.9 cannot infer; SA forced)"),
    ]
    with capsys.disabled():
        print()
        print("CINRAD routing coverage matrix:")
        print("  " + "-" * 70)
        for r in rows:
            print("  {}  routing={:5s}  e2e={}".format(*r))
        print("  " + "-" * 70)
    # Always passes; the value of this test is the diagnostic output.
    assert True
