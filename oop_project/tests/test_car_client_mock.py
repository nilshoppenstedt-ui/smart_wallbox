# tests/test_car_client_mock.py

from datetime import datetime
from typing import Optional

import pytest

from smart_home.car_client import CarClient, CarClientError, CarStatus


class FakeCarClientOK(CarClient):
    """Fake CarClient that returns a fixed status without real API calls."""

    def __init__(self) -> None:
        # Do not call the real __init__ (no credentials needed here)
        pass

    async def _fetch_status_async(self) -> CarStatus:
        """Return a fixed CarStatus for testing."""
        return CarStatus(
            soc=78,
            autonomy_km=150,
            plug_status=1,
            charging_status=0.0,
            timestamp=datetime(2025, 1, 1, 12, 0, 0),
        )


class FakeCarClientError(CarClient):
    """Fake CarClient that always fails in the async method."""

    def __init__(self) -> None:
        # Do not call the real __init__
        pass

    async def _fetch_status_async(self) -> CarStatus:
        raise RuntimeError("Simulated Renault API failure")


def test_car_client_read_status_ok():
    """read_status should return a CarStatus object with expected values."""
    client = FakeCarClientOK()
    status = client.read_status()

    assert isinstance(status, CarStatus)
    assert status.soc == 78
    assert status.autonomy_km == 150
    assert status.plug_status == 1
    assert status.charging_status == 0.0
    # Timestamp should be exactly the one we set in the fake
    assert status.timestamp == datetime(2025, 1, 1, 12, 0, 0)


def test_car_client_read_status_raises_on_error():
    """read_status should wrap errors in CarClientError."""
    client = FakeCarClientError()

    with pytest.raises(CarClientError):
        _ = client.read_status()
