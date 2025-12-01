from smart_home.backend_app import (
    AppState,
    CONTROL_PERIOD,
    CAR_STATUS_PERIOD,
    BATTERY_SAVING_CHECK_PERIOD,
    GRID_SAMPLE_EVERY,
)


def test_period_counters_trigger_at_expected_ticks():
    """
    Simuliert mehrere Loop-Ticks und prüft, ob die Counter an den
    erwarteten Stellen triggern (basierend auf der Modulo-Logik).
    """

    app = AppState()

    # Startwerte wie im echten Code
    app.grid_counter = 0
    app.control_counter = 0
    app.soc_counter = 0
    app.car_status_counter = 0

    grid_sample_ticks = []
    car_status_ticks = []
    control_ticks = []
    soc_ticks = []

    # Anzahl Ticks so wählen, dass mehrere Perioden durchlaufen werden
    n_ticks = max(CONTROL_PERIOD, CAR_STATUS_PERIOD, BATTERY_SAVING_CHECK_PERIOD) * 3

    for t in range(n_ticks):
        # Bedingungen wie in run_loop
        if app.grid_counter == 0:
            grid_sample_ticks.append(t)

        if app.car_status_counter == 0:
            car_status_ticks.append(t)

        if app.control_counter == CONTROL_PERIOD - 1:
            control_ticks.append(t)

        if app.soc_counter == 0:
            soc_ticks.append(t)

        # Counter-Update wie im run_loop
        app.grid_counter = (app.grid_counter + 1) % GRID_SAMPLE_EVERY
        app.control_counter = (app.control_counter + 1) % CONTROL_PERIOD
        app.soc_counter = (app.soc_counter + 1) % BATTERY_SAVING_CHECK_PERIOD
        app.car_status_counter = (app.car_status_counter + 1) % CAR_STATUS_PERIOD

    # Erwartete Sequenzen konstruieren

    # grid_counter: alle GRID_SAMPLE_EVERY Ticks, beginnend bei 0
    expected_grid_ticks = list(range(0, n_ticks, GRID_SAMPLE_EVERY))
    assert grid_sample_ticks == expected_grid_ticks

    # car_status_counter: alle CAR_STATUS_PERIOD Ticks, beginnend bei 0
    expected_car_status_ticks = list(range(0, n_ticks, CAR_STATUS_PERIOD))
    assert car_status_ticks == expected_car_status_ticks

    # control_counter: CONTROL_PERIOD - 1, dann + CONTROL_PERIOD usw.
    expected_control_ticks = list(range(CONTROL_PERIOD - 1, n_ticks, CONTROL_PERIOD))
    assert control_ticks == expected_control_ticks

    # soc_counter: alle BATTERY_SAVING_CHECK_PERIOD Ticks, beginnend bei 0
    expected_soc_ticks = list(range(0, n_ticks, BATTERY_SAVING_CHECK_PERIOD))
    assert soc_ticks == expected_soc_ticks
