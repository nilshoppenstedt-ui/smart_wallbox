# tests/test_mode_switch_triggers_control.py

import math
from typing import List, Optional


CONTROL_PERIOD_SEC = 300  # wie im produktiven Code


class DummyController:
    """Minimaler Controller mit gleicher Schnittstelle wie SurplusController."""
    def __init__(self):
        self.calls = []  # speichert (grid_kw, wb_kw)

    def step(self, grid_kw: float, wb_kw: float) -> dict:
        """Merkt sich die Aufrufe und gibt feste Dummy-Werte zurück."""
        self.calls.append((grid_kw, wb_kw))
        return {
            "p_available_kw": 1.23,
            "phase": 3,
            "current": 10,
        }


class FakeAppState:
    """
    Vereinfachte AppState-Variante, die nur die Regel-Trigger-Logik testet.
    Keine Hardware, kein Flask, keine Threads.
    """

    def __init__(self) -> None:
        self.controller = DummyController()
        self.grid_samples: List[float] = []
        self.counter: int = 0
        self.just_switched_to_pv: bool = False
        self.status = {
            "mode": "monitor_only",   # Start im Anzeige-Modus
            "grid_kw_avg": None,
            "wb_kw_avg": None,
            "p_available_kw": None,
        }
        # Merker für "Wallbox-Aktion"
        self.last_phase_set: Optional[int] = None
        self.last_current_set: Optional[int] = None

    def get_mode(self) -> str:
        return self.status.get("mode", "pv_surplus")

    def apply_charger_decision(self, phase_new: int, current_new: int) -> None:
        """Statt echter go-e API nur Werte merken."""
        self.last_phase_set = phase_new
        self.last_current_set = current_new

    def control_step_if_due(self, wb_kw_avg: float) -> None:
        """
        Entspricht dem Regelteil aus run_loop():
        - läuft nur in pv_surplus
        - triggert bei 5-Minuten-Ende ODER frischem Moduswechsel
        - benötigt mindestens einen grid_sample
        """
        mode = self.get_mode()
        just_switched = self.just_switched_to_pv

        trigger_control = (
            mode == "pv_surplus"
            and bool(self.grid_samples)
            and (self.counter == CONTROL_PERIOD_SEC - 1 or just_switched)
        )

        if not trigger_control:
            return

        grid_avg_kw = sum(self.grid_samples) / len(self.grid_samples)
        result = self.controller.step(grid_kw=grid_avg_kw, wb_kw=wb_kw_avg)

        # Status aktualisieren
        self.status["grid_kw_avg"] = grid_avg_kw
        self.status["wb_kw_avg"] = wb_kw_avg
        self.status["p_available_kw"] = result["p_available_kw"]

        # Flag zurücksetzen, Samples leeren
        self.just_switched_to_pv = False
        self.grid_samples = []

        # "Wallbox" setzen
        self.apply_charger_decision(
            phase_new=result["phase"],
            current_new=result["current"],
        )


def test_mode_switch_triggers_immediate_control_cycle():
    """
    Szenario:
    - System läuft im monitor_only-Modus und sammelt Grid-Samples.
    - Es wird auf pv_surplus umgeschaltet.
    Erwartung:
    - Noch im monitor_only-Modus passiert nichts.
    - Nach dem Umschalten wird sofort ein Regelzyklus ausgeführt
      (ohne 5 Minuten zu warten), sofern grid_samples nicht leer ist.
    """

    app = FakeAppState()

    # 1) System hat bereits Grid-Samples gesammelt, ist aber noch im monitor_only-Modus
    app.grid_samples = [4.0, 6.0]  # Mittelwert = 5.0 kW
    app.counter = 100  # irgendwo mitten im 5-Minuten-Fenster
    wb_kw_avg = 3.0

    # Noch im monitor_only-Modus: es darf nichts passieren
    app.control_step_if_due(wb_kw_avg=wb_kw_avg)
    assert app.controller.calls == []
    assert app.last_phase_set is None
    assert app.last_current_set is None
    assert app.status["grid_kw_avg"] is None
    assert app.just_switched_to_pv is False

    # 2) Moduswechsel: monitor_only -> pv_surplus
    app.status["mode"] = "pv_surplus"
    app.just_switched_to_pv = True

    # 3) Jetzt soll unmittelbar ein Regelzyklus angestoßen werden
    app.control_step_if_due(wb_kw_avg=wb_kw_avg)

    # Controller muss genau einmal aufgerufen worden sein
    assert len(app.controller.calls) == 1
    grid_called, wb_called = app.controller.calls[0]

    # Mittelwert aus [4.0, 6.0] = 5.0 kW
    assert math.isclose(grid_called, 5.0, rel_tol=1e-6)
    assert wb_called == wb_kw_avg

    # Die "Wallbox-Einstellung" sollte gesetzt worden sein
    assert app.last_phase_set == 3
    assert app.last_current_set == 10

    # grid_samples sollten geleert sein
    assert app.grid_samples == []

    # Flag muss zurückgesetzt sein
    assert app.just_switched_to_pv is False

    # Status sollte sinnvolle Werte enthalten
    assert app.status["grid_kw_avg"] == 5.0
    assert app.status["wb_kw_avg"] == wb_kw_avg
    assert app.status["p_available_kw"] == 1.23
