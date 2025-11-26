import pytest

from smart_home.pv_inverter import PVInverter, PVInverterError

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


def test_pvinverter_string_powers_range():
    """
    Live test: read current PV string powers and check that they are in a
    physically reasonable range (-20 ... +20 kW) for each string.

    This test assumes that the inverter is reachable on the given IP/port.
    """
    inverter = PVInverter(PV_IP, port=PV_PORT, unit_id=PV_UNIT)

    try:
        powers = inverter.read_string_powers_kw()
    except PVInverterError as e:
        pytest.fail(f"PVInverter string powers could not be read: {e}")

    # Basic structure
    assert isinstance(powers, dict), "String powers result is not a dict"

    for key in ("pv1_kw", "pv2_kw", "pv3_kw"):
        assert key in powers, f"Key {key} missing in string powers dict"

        value = powers[key]
        assert isinstance(value, float), f"{key} is not a float value"

        assert -20.0 <= value <= 20.0, (
            f"{key} out of expected range: {value:.2f} kW"
        )