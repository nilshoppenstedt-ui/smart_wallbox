import pytest
from grid_meter import GridMeter, GridMeterError

# IP-Adresse deines Lesekopfes
GRID_IP = "192.168.178.191"


def test_gridmeter_live_power_range():
    """
    Testet den Live-Wert der Wirkleistung.
    Erwartung: der Wert muss in einem physikalisch sinnvollen Bereich liegen.
    
    Bereich: -20 ... +20 kW
    """
    meter = GridMeter(GRID_IP)

    try:
        power_kw = meter.read_power_kw()
    except GridMeterError as e:
        pytest.fail(f"GridMeter konnte nicht ausgelesen werden: {e}")

    assert isinstance(power_kw, float), "Wert ist nicht vom Typ float"

    assert -20.0 <= power_kw <= 20.0, (
        f"Leistungswert außerhalb des erwarteten Bereichs: {power_kw:.2f} kW"
    )
