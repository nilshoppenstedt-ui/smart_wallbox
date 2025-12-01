# tests/test_appstate_apply_charger_decision.py

from smart_home.backend_app import AppState


class DummyStatus:
    def __init__(self, car_state="Idle", phase_mode=1, ampere_allowed=0):
        self.car_state = car_state
        self.phase_mode = phase_mode
        self.ampere_allowed = ampere_allowed


class DummyCharger:
    """
    Minimal dummy implementation of the go-e client interface
    used by apply_charger_decision().
    """

    def __init__(self, car_state="Idle", phase_mode=1, ampere_allowed=0):
        self._status = DummyStatus(car_state, phase_mode, ampere_allowed)
        self.mode_calls = []
        self.phase_calls = []
        self.amp_calls = []

    def get_status_min(self):
        return self._status

    def set_charging_mode(self, value: bool):
        self.mode_calls.append(value)

    def set_phase_mode(self, value: int):
        self.phase_calls.append(value)

    def set_ampere(self, amp: int):
        self.amp_calls.append(amp)


def test_apply_charger_decision_does_not_raise_for_various_states():
    """
    Call apply_charger_decision() with a dummy charger in several
    typical state combinations and ensure no exception is raised.

    This guards against missing methods or structural errors in
    the control logic.
    """

    app = AppState()

    # Case 1: car idle, current_new = 0 -> should be a no-op
    app.charger = DummyCharger(car_state="Idle", phase_mode=1, ampere_allowed=0)
    app.apply_charger_decision(phase_new=1, current_new=0)

    # Case 2: car charging, current_new = 0 -> should stop charging
    app.charger = DummyCharger(car_state="Charging", phase_mode=1, ampere_allowed=16)
    app.apply_charger_decision(phase_new=1, current_new=0)

    # Case 3: car in strange state, current_new > 0 -> should attempt start
    app.charger = DummyCharger(car_state="Waiting", phase_mode=1, ampere_allowed=0)
    app.apply_charger_decision(phase_new=3, current_new=10)

    # Case 4: car charging, current_new > 0 -> adjust parameters
    app.charger = DummyCharger(car_state="Charging", phase_mode=1, ampere_allowed=10)
    app.apply_charger_decision(phase_new=3, current_new=16)

    # If we reach here without exception, the test passes.
    # Optional: we could add assertions on dummy.charger.*_calls,
    # but für dein Ziel (keine stillen Strukturfehler) reicht
    # das reine "no exception".
