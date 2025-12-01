import pytest
from smart_home.backend_app import AppState

def test_appstate_has_all_required_methods():
    """
    Testet, ob alle erwarteten Methoden in AppState existieren.
    Verhindert, dass Methoden versehentlich gelöscht oder umbenannt werden.
    """

    REQUIRED_METHODS = [
        "run_loop",
        "update_instant_snapshot",
        "update_car_status",
        "apply_charger_decision",
        "check_battery_saving_stop",
        "get_mode",
        "set_mode",
    ]

    app = AppState()

    for method_name in REQUIRED_METHODS:
        assert hasattr(app, method_name), f"Missing method: AppState.{method_name}"
        method = getattr(app, method_name)
        assert callable(method), f"AppState.{method_name} exists but is not callable"
