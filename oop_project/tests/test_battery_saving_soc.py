import pytest
from datetime import datetime, timedelta

from smart_home.backend_app import (
    AppState,
    BATTERY_SAVING_SOC_LIMIT,
    BATTERY_SAVING_MAX_AGE_SEC,
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _iso_now_minus(seconds: float) -> str:
    """Return ISO-8601 string for 'now - seconds' (naive datetime)."""
    return (datetime.now() - timedelta(seconds=seconds)).isoformat()


def _make_appstate_for_soc_test(
    raw_soc=None,
    est_soc=None,
    age_sec=None,
    valid=True,
) -> AppState:
    """
    Erzeugt ein AppState-Objekt und setzt die relevanten Statusfelder
    für check_battery_saving_stop().

    Parameter:
        raw_soc : realer Renault-SoC (car_soc) oder None
        est_soc : geschätzter SoC (car_soc_est) oder None
        age_sec : Alter der Renault-Daten in Sekunden.
                  None -> kein Timestamp gesetzt.
        valid   : Wert für car_status_valid
    """
    app = AppState()

    if age_sec is None:
        ts_iso = None
    else:
        ts_iso = _iso_now_minus(age_sec)

    with app.lock:
        app.status["car_soc"] = raw_soc
        app.status["car_soc_est"] = est_soc
        app.status["car_status_valid"] = valid
        app.status["car_status_timestamp"] = ts_iso

    return app


# ---------------------------------------------------------------------------
# Tests für die SoC-Schutzlogik
# ---------------------------------------------------------------------------

def test_stop_when_fresh_real_soc_above_limit():
    """
    Fall 1:
    - realer SoC > Limit
    - Daten frisch
    - valid = True
    => realer SoC hat Vorrang, Ladung MUSS gestoppt werden.
    """
    soc_high = BATTERY_SAVING_SOC_LIMIT + 2.0
    age_fresh = BATTERY_SAVING_MAX_AGE_SEC - 60.0  # 1 min jünger als Max-Alter

    app = _make_appstate_for_soc_test(
        raw_soc=soc_high,
        est_soc=None,
        age_sec=age_fresh,
        valid=True,
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is True
    assert soc_value == pytest.approx(soc_high)


def test_no_stop_when_real_soc_below_limit_even_if_estimate_high():
    """
    Fall 2:
    - realer SoC < Limit, Daten frisch und valid=True
    - geschätzter SoC > Limit
    => realer SoC löst keinen Stop aus,
       danach soll die Schätzung greifen und die Ladung stoppen.
    """
    soc_real = BATTERY_SAVING_SOC_LIMIT - 5.0
    soc_est  = BATTERY_SAVING_SOC_LIMIT + 3.0
    age_fresh = BATTERY_SAVING_MAX_AGE_SEC - 60.0

    app = _make_appstate_for_soc_test(
        raw_soc=soc_real,
        est_soc=soc_est,
        age_sec=age_fresh,
        valid=True,
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is True
    # In dieser Konstellation soll der Schätzwert die Entscheidung liefern
    assert soc_value == pytest.approx(soc_est)


def test_no_stop_when_real_soc_high_but_too_old_and_estimate_low():
    """
    Fall 3:
    - realer SoC > Limit, aber Daten zu alt
    - geschätzter SoC < Limit
    => realer SoC darf wegen Alter nicht stoppen,
       Schätzung liegt unter Limit -> kein Stop.
    """
    soc_real = BATTERY_SAVING_SOC_LIMIT + 5.0
    soc_est  = BATTERY_SAVING_SOC_LIMIT - 1.0
    age_old  = BATTERY_SAVING_MAX_AGE_SEC + 60.0  # 1 min älter als Max-Alter

    app = _make_appstate_for_soc_test(
        raw_soc=soc_real,
        est_soc=soc_est,
        age_sec=age_old,
        valid=True,
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is False
    # soc_value entspricht in diesem Fall dem realen SoC
    assert soc_value == pytest.approx(soc_real)


def test_stop_when_only_estimate_above_limit():
    """
    Fall 4:
    - kein realer SoC gesetzt
    - Schätzwert > Limit
    => Schätzwert löst Stop aus.
    """
    soc_est = BATTERY_SAVING_SOC_LIMIT + 4.0

    app = _make_appstate_for_soc_test(
        raw_soc=None,
        est_soc=soc_est,
        age_sec=None,
        valid=False,  # spielt hier keine Rolle, da kein raw_soc
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is True
    assert soc_value == pytest.approx(soc_est)


def test_no_stop_when_status_invalid_and_only_real_soc_present():
    """
    Fall 5:
    - realer SoC > Limit
    - car_status_valid=False
    - kein Schätzwert
    => kein Stop (Status ungültig), SoC-Wert wird trotzdem zurückgegeben.
    """
    soc_real = BATTERY_SAVING_SOC_LIMIT + 10.0
    age_fresh = BATTERY_SAVING_MAX_AGE_SEC / 2.0

    app = _make_appstate_for_soc_test(
        raw_soc=soc_real,
        est_soc=None,
        age_sec=age_fresh,
        valid=False,
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is False
    assert soc_value == pytest.approx(soc_real)


def test_priority_real_over_estimate_when_both_high_and_fresh():
    """
    Fall 6:
    - realer SoC > Limit, Daten frisch, valid=True
    - Schätzwert > Limit
    => realer SoC hat Vorrang, Stop basierend auf realem SoC.
    """
    soc_real = BATTERY_SAVING_SOC_LIMIT + 5.0
    soc_est  = BATTERY_SAVING_SOC_LIMIT + 8.0
    age_fresh = BATTERY_SAVING_MAX_AGE_SEC - 30.0

    app = _make_appstate_for_soc_test(
        raw_soc=soc_real,
        est_soc=soc_est,
        age_sec=age_fresh,
        valid=True,
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is True
    # Vorrang des realen SoC
    assert soc_value == pytest.approx(soc_real)


def test_no_stop_when_both_soc_values_below_limit():
    """
    Fall 7:
    - realer SoC < Limit
    - Schätzwert < Limit
    => kein Stop.
    """
    soc_real = BATTERY_SAVING_SOC_LIMIT - 5.0
    soc_est  = BATTERY_SAVING_SOC_LIMIT - 2.0
    age_fresh = BATTERY_SAVING_MAX_AGE_SEC / 2.0

    app = _make_appstate_for_soc_test(
        raw_soc=soc_real,
        est_soc=soc_est,
        age_sec=age_fresh,
        valid=True,
    )

    stop, soc_value = app.check_battery_saving_stop()
    assert stop is False
    # soc_value ist in diesem Fall der reale SoC
    assert soc_value == pytest.approx(soc_real)
