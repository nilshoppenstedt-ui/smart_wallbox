import pytest

from pv_inverter import PVInverter, PVInverterError

# IP/Port/Unit wie in deinem bisherigen Skript
PV_IP = "192.168.178.56"
PV_PORT = 1502
PV_UNIT = 71


def test_pvinverter_live_power_range():
    """
    Live test: read current PV power and check that it is in a
    physically reasonable range (-20 ... +20 kW).

    This test assumes that the inverter is reachable on the given IP/port.
    """
    inverter = PVInverter(PV_IP, port=PV_PORT, unit_id=PV_UNIT)

    try:
        power_kw = inverter.read_total_power_kw()
    except PVInverterError as e:
        pytest.fail(f"PVInverter could not be read: {e}")

    assert isinstance(power_kw, float), "PV power is not a float value"

    assert -20.0 <= power_kw <= 20.0, (
        f"PV power out of expected range: {power_kw:.2f} kW"
    )
