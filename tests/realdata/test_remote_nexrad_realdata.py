"""Gated real-data test for the NEXRAD remote source (network + S3)."""

import os
from datetime import datetime, timedelta

import pytest

pytest.importorskip("s3fs")
from pyart.io.remote import NexradSource  # noqa: E402


@pytest.mark.realdata
def test_nexrad_source_list_files_real():
    """Pull a short NEXRAD timespan from the public S3 bucket."""
    if os.environ.get("PYART_RUN_REALDATA") != "1":
        pytest.skip("set PYART_RUN_REALDATA=1 to run real network tests")

    src = NexradSource(anon=True)
    start = datetime(2023, 6, 1, 0, 0)
    end = datetime(2023, 6, 1, 0, 10)
    files = src.list_files("KTLX", start, end, timedelta(minutes=5))
    assert isinstance(files, list)
