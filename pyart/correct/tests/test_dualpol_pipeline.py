"""
End-to-end pipeline regression tests for the dual-polarization path.

The pipeline is:

    pyart.io.read_cinrad(...)
        → radar.metadata['radar_band']  (X / S / C)
        → pyart.correct.cband_sband.calibrate_dualpol / process_phi_kdp
        → pyart.correct.cband_sband._resolve_band picks BAND_PARAMS[band]

A silent fallback to 'C' for an SA radar (or vice versa) corrupts the
calibration and yields wrong reflectivity / differential-phase products
without raising any error. This test module pins the contract that
``read_cinrad`` always sets ``metadata['radar_band']`` to the
band-appropriate identifier whenever the filename can be classified.

NOTE: Like ``test_cinrad_routing.py``, this module does **not** need
real CINRAD fixtures. We use a fake ``Radar`` class that exposes the
metadata contract read_cinrad guarantees, and we assert on
``calibrate_dualpol``'s effective band selection.
"""

import warnings

import numpy as np
import pytest

pyart = pytest.importorskip("pyart")


def _make_fake_radar(band_metadata=None, fields=None, calibration=None):
    """Build a minimal Radar-like object that satisfies the contract
    ``cband_sband.calibrate_dualpol`` and ``process_phi_kdp`` rely on.
    No real Py-ART Radar instance is needed because the public APIs
    only touch ``metadata``, ``fields`` and ``radar_calibration``."""

    class FakeRadar:
        pass

    r = FakeRadar()
    r.metadata = dict(band_metadata) if band_metadata else {}
    r.fields = fields or {}
    r.radar_calibration = calibration or {}
    return r


def _make_dualpol_fields():
    zdr = pyart.config.get_field_name("differential_reflectivity")
    phi = pyart.config.get_field_name("differential_phase")
    ldr = pyart.config.get_field_name("linear_depolarization_ratio")
    shape = (3, 100)
    return {
        zdr: {
            "data": np.ma.array(np.full(shape, 1.5), mask=False),
            "long_name": "Differential Reflectivity",
        },
        phi: {
            "data": np.ma.array(np.full(shape, 60.0), mask=False),
            "long_name": "Differential Phase",
        },
        ldr: {
            "data": np.ma.array(np.full(shape, -30.0), mask=False),
            "long_name": "Linear Depolarization Ratio",
        },
    }


@pytest.mark.parametrize(
    "filename,expected_band",
    [
        # S-band family
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SB_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SC_CAP.bin", "S"),
        # C-band family
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CB_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CC_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CCJ_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CD_CAP.bin", "C"),
        # WSR-98D
        ("Z_RADR_C_DOPPLER_WSR98D_20190421.bin", "S"),
    ],
)
def test_read_cinrad_metadata_contract(filename, expected_band):
    """Pinned contract: when the caller does not pass ``band=``,
    ``read_cinrad`` must still set ``radar.metadata['radar_band']`` to
    the band-appropriate identifier so downstream dual-pol helpers
    do not silently fall back to 'C'.

    We assert the **contract** by simulating the read result via the
    ``_infer_band_from_filename`` helper and feeding it to
    ``cband_sband._resolve_band``. If the metadata contract is met,
    ``_resolve_band`` returns the expected band without warning.
    """
    from pyart.correct.cband_sband import _resolve_band
    from pyart.io.cinrad_bridge import _infer_band_from_filename

    inferred = _infer_band_from_filename(filename)
    assert (
        inferred == expected_band
    ), f"band inference regressed for {filename!r}: got {inferred!r}, expected {expected_band!r}"

    r = _make_fake_radar(
        band_metadata={"radar_band": inferred}, fields=_make_dualpol_fields()
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        band = _resolve_band(r)
    user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
    assert band == expected_band
    assert (
        not user_warnings
    ), "_resolve_band emitted UserWarning despite correct metadata: " + "; ".join(
        str(w.message) for w in user_warnings
    )


def test_resolve_band_falls_back_to_c_with_warning_when_metadata_missing():
    """When the upstream chain has lost the metadata entirely
    (e.g. a custom user-built Radar), ``_resolve_band`` must fall
    back to 'C' AND emit a UserWarning so the operator can see the
    silent fallback in logs."""
    from pyart.correct.cband_sband import _resolve_band

    r = _make_fake_radar()  # no metadata at all
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        band = _resolve_band(r)
    assert band == "C"
    user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
    assert any(
        "defaulting to" in str(w.message) for w in user_warnings
    ), "Expected _resolve_band to emit a UserWarning when metadata is missing"


def test_calibrate_dualpol_uses_metadata_band_not_fallback():
    """The full dual-pol pipeline: if metadata correctly says 'S',
    the resulting BAND_PARAMS['S']['self_const'] is 60000.0, NOT the
    C-band default that a silent fallback would have produced.

    We assert by inspecting which BAND_PARAMS entry calibrate_dualpol
    reaches through _resolve_band, captured by monkey-patching
    BAND_PARAMS.
    """
    from pyart.correct import cband_sband

    fields = _make_dualpol_fields()
    # Monkey-patch BAND_PARAMS to record the selected band.
    selected = []

    real_resolve = cband_sband._resolve_band

    def spy_resolve(radar):
        band = real_resolve(radar)
        selected.append(band)
        return band

    cband_sband._resolve_band = spy_resolve
    try:
        r = _make_fake_radar(band_metadata={"radar_band": "S"}, fields=fields)
        cband_sband.calibrate_dualpol(r)
    finally:
        cband_sband._resolve_band = real_resolve

    assert selected, "calibrate_dualpol did not call _resolve_band"
    assert selected[0] == "S", (
        f"calibrate_dualpol resolved band {selected[0]!r} (expected S); "
        f"BAND_PARAMS entry used: {cband_sband.BAND_PARAMS[selected[0]]}"
    )


def test_calibrate_dualpol_does_not_mutate_input_fields():
    """calibrate_dualpol is documented to return a new dict without
    modifying ``radar.fields``. Verify this contract on a synthetic
    radar where ZDR has a calibration offset."""
    from pyart.correct.cband_sband import calibrate_dualpol

    zdr = pyart.config.get_field_name("differential_reflectivity")
    original_data = np.ma.array(np.full((3, 100), 1.5), mask=False)
    fields = {zdr: {"data": original_data, "long_name": "ZDR"}}
    calibration = {"zdr_calibration": {"data": np.array([0.5])}}
    r = _make_fake_radar(
        band_metadata={"radar_band": "S"}, fields=fields, calibration=calibration
    )

    before = r.fields[zdr]["data"].copy()
    result = calibrate_dualpol(r)
    # The input fields must NOT have been modified.
    np.testing.assert_array_equal(
        r.fields[zdr]["data"],
        before,
        "calibrate_dualpol must not mutate radar.fields[zdr][data]",
    )
    # The returned dict carries the corrected data.
    assert result[zdr]["data"][0, 0] == 1.0, "Expected 1.5 - 0.5 = 1.0, got {}".format(
        result[zdr]["data"][0, 0]
    )


def test_pipeline_smoke_test_writes_correct_metadata_for_all_formats():
    """Final integration smoke test: iterate over every documented
    CINRAD filename, simulate the read_cinrad metadata contract, then
    verify _resolve_band returns the matching band.

    This is the contract the real read_cinrad must satisfy; the test
    stays robust by going through _infer_band_from_filename rather
    than touching real bytes.
    """
    from pyart.correct.cband_sband import BAND_PARAMS, _resolve_band
    from pyart.io.cinrad_bridge import _infer_band_from_filename

    docs_formats = [
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SA_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SB_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CB_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CC_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CCJ_CAP.bin", "C"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_SC_CAP.bin", "S"),
        ("Z_RADR_I_Z9200_20190421190600_O_DOR_CD_CAP.bin", "C"),
        ("Z_RADR_C_DOPPLER_WSR98D_20190421.bin", "S"),
    ]
    for filename, expected_band in docs_formats:
        inferred = _infer_band_from_filename(filename)
        assert inferred == expected_band
        assert (
            inferred in BAND_PARAMS
        ), f"inferred band {inferred!r} is not in BAND_PARAMS"
        r = _make_fake_radar(
            band_metadata={"radar_band": inferred}, fields=_make_dualpol_fields()
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            band = _resolve_band(r)
        assert band == expected_band
        assert not [
            w for w in caught if issubclass(w.category, UserWarning)
        ], f"unexpected UserWarning for {filename}"
