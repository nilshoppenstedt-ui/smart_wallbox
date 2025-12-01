import time
from datetime import datetime, timedelta

from smart_home.backend_app import (
    AppState,
    BATTERY_SAVING_SOC_LIMIT,
    BATTERY_SAVING_MAX_AGE_SEC,
)


def _iso_now_minus(delta_sec: float) -> str:
    """Helper: ISO-String für (jetzt - delta_sec)."""
    dt = datetime.now() - timedelta(seconds=delta_sec)
    return dt.isoformat(timespec="seconds")


def test_check_battery_saving_stop_triggers_for_high_soc_and_fresh_data():
    """High SoC, frische Daten, car_status_valid=True -> Stop = True."""
    app = AppState()

    soc = BATTERY_SAVING_SOC_LIMIT + 1.0  # sicher über der Schwelle
    ts_iso = _iso_now_minus(10.0)        # 10 s alt, also frisch

    with app.lock:
        app.status["car_soc"] = soc
        app.status["car_status_valid"] = True
        app.status["car_status_timestamp"] = ts_iso

    stop, soc_value = app.check_battery_saving_stop()

    assert stop is True
    assert soc_value == soc


def test_check_battery_saving_stop_no_stop_when_soc_below_limit():
    """SoC unterhalb der Schwelle -> kein Stop, auch bei frischen Daten."""
    app = AppState()

    soc = BATTERY_SAVING_SOC_LIMIT - 5.0
    ts_iso = _iso_now_minus(10.0)

    with app.lock:
        app.status["car_soc"] = soc
        app.status["car_status_valid"] = True
        app.status["car_status_timestamp"] = ts_iso

    stop, soc_value = app.check_battery_saving_stop()

    assert stop is False
    assert soc_value == soc


def test_check_battery_saving_stop_no_stop_when_data_too_old():
    """SoC über Limit, aber Daten älter als BATTERY_SAVING_MAX_AGE_SEC -> kein Stop."""
    app = AppState()

    soc = BATTERY_SAVING_SOC_LIMIT + 5.0
    # Zeitstempel absichtlich älter als die erlaubte Maximalzeit
    ts_iso = _iso_now_minus(BATTERY_SAVING_MAX_AGE_SEC + 30.0)

    with app.lock:
        app.status["car_soc"] = soc
        app.status["car_status_valid"] = True
        app.status["car_status_timestamp"] = ts_iso

    stop, soc_value = app.check_battery_saving_stop()

    assert stop is False
    assert soc_value == soc


def test_check_battery_saving_stop_no_stop_when_status_invalid():
    """car_status_valid=False -> immer kein Stop, auch bei hohem SoC."""
    app = AppState()

    soc = BATTERY_SAVING_SOC_LIMIT + 10.0
    ts_iso = _iso_now_minus(5.0)

    with app.lock:
        app.status["car_soc"] = soc
        app.status["car_status_valid"] = False
        app.status["car_status_timestamp"] = ts_iso

    stop, soc_value = app.check_battery_saving_stop()

    assert stop is False
    assert soc_value == soc


def test_check_battery_saving_stop_handles_missing_timestamp():
    """Fehlender/None-Timestamp -> defensiv kein Stop, aber SoC wird zurückgegeben."""
    app = AppState()

    soc = BATTERY_SAVING_SOC_LIMIT + 5.0

    with app.lock:
        app.status["car_soc"] = soc
        app.status["car_status_valid"] = True
        app.status["car_status_timestamp"] = None

    stop, soc_value = app.check_battery_saving_stop()

    assert stop is False
    assert soc_value == soc
