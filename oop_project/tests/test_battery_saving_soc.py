import threading
from datetime import datetime, timedelta

import pytest

from smart_home.backend_app import (
    AppState,
    BATTERY_SAVING_SOC_LIMIT,
    BATTERY_SAVING_MAX_AGE_SEC,
)


def _make_appstate_for_soc_test(soc, age_sec, valid):
    """
    Hilfsfunktion: erzeugt einen minimalen AppState, der nur für
    check_battery_saving_stop() geeignet ist, ohne den vollen __init__
    auszuführen.
    """
    state = AppState.__new__(AppState)  # __init__ wird nicht aufgerufen
    state.lock = threading.Lock()

    if age_sec is None:
        ts_str = None
    else:
        now = datetime.now()
        ts = now - timedelta(seconds=age_sec)
        ts_str = ts.isoformat(timespec="seconds")

    state.status = {
        "car_soc": soc,
        "car_status_timestamp": ts_str,
        "car_status_valid": valid,
    }
    return state


def test_check_battery_saving_stop_various_cases():
    """
    Testet die SoC-Policy check_battery_saving_stop() in mehreren Szenarien:

    - hoher SoC, frische Daten, valid=True  -> stop = True
    - niedriger SoC, frische Daten         -> stop = False
    - hoher SoC, aber Daten zu alt         -> stop = False
    - hoher SoC, aber car_status_valid=False -> stop = False
    """

    # Fall 1: SoC über Limit, Daten frisch und valid=True -> Stop erwartet
    soc_high = BATTERY_SAVING_SOC_LIMIT + 2.0
    age_fresh = BATTERY_SAVING_MAX_AGE_SEC - 60  # 1 min "jünger" als Max-Alter
    state = _make_appstate_for_soc_test(soc_high, age_fresh, valid=True)

    stop, soc_value = state.check_battery_saving_stop()
    assert stop is True, "Battery-saving stop sollte bei hohem SoC und frischen Daten aktiv sein."
    assert soc_value == pytest.approx(soc_high)

    # Fall 2: SoC unter Limit, Daten frisch -> kein Stop
    soc_low = BATTERY_SAVING_SOC_LIMIT - 5.0
    state = _make_appstate_for_soc_test(soc_low, age_fresh, valid=True)

    stop, soc_value = state.check_battery_saving_stop()
    assert stop is False, "Bei SoC unterhalb des Limits darf kein Stop ausgelöst werden."

    # Fall 3: SoC über Limit, aber Daten zu alt -> kein Stop
    age_old = BATTERY_SAVING_MAX_AGE_SEC + 60  # 1 min "älter" als zulässig
    state = _make_appstate_for_soc_test(soc_high, age_old, valid=True)

    stop, soc_value = state.check_battery_saving_stop()
    assert stop is False, "Bei zu alten Fahrzeugdaten darf kein Stop ausgelöst werden."

    # Fall 4: SoC über Limit, Daten frisch, aber valid=False -> kein Stop
    state = _make_appstate_for_soc_test(soc_high, age_fresh, valid=False)

    stop, soc_value = state.check_battery_saving_stop()
    assert stop is False, "Wenn car_status_valid=False ist, darf kein Stop ausgelöst werden."
