import pytest

from wallbox import Wallbox, WallboxError

WALLBOX_IP = "192.168.178.21"


def test_wallbox_is_vehicle_connected_returns_bool():
    """
    Live test: verify that is_vehicle_connected() returns a boolean.
    The actual value (True/False) does NOT matter.
    """
    wb = Wallbox(WALLBOX_IP)

    try:
        result = wb.is_vehicle_connected()
    except WallboxError as e:
        pytest.fail(f"Wallbox could not be read: {e}")

    assert isinstance(result, bool), (
        f"is_vehicle_connected() returned non-bool value: {result}"
    )
