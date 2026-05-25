# tests/test_car_client_integration.py
"""Integration test for the real Renault car client.

This test verifies that the application can access the Renault backend with
the configured MyRenault credentials and read the current vehicle battery
status through the production CarClient implementation.

The test requires the environment variables MYRENAULT_EMAIL and
MYRENAULT_PASSWORD to be set. If either variable is missing, the test is
skipped instead of failing.
"""

import os
import pytest

from smart_home.car_client import CarClient, CarStatus


def test_real_renault_status_can_be_read():
    email = os.getenv("MYRENAULT_EMAIL")
    password = os.getenv("MYRENAULT_PASSWORD")

    if not email or not password:
        pytest.skip("MYRENAULT_EMAIL und MYRENAULT_PASSWORD nicht gesetzt")

    client = CarClient(email=email, password=password)
    status = client.read_status()

    assert isinstance(status, CarStatus)
    assert status.soc is not None
    assert 0 <= status.soc <= 100
    assert status.autonomy_km is None or status.autonomy_km >= 0
    assert status.timestamp is not None