import pytest

from surplus_controller import SurplusController, ControllerParams


def test_surplus_controller_no_surplus():
    """
    Scenario: grid import, no PV surplus.
    Expectation: available power = 0, current = 0, phase = 1.
    """
    params = ControllerParams()
    ctrl = SurplusController(params)

    # Example: 1 kW grid import, WB = 0 kW
    result = ctrl.step(grid_kw=1.0, wb_kw=0.0)

    assert result["p_available_kw"] == pytest.approx(0.0, abs=1e-6)
    assert result["current"] == 0
    assert result["phase"] == 1


def test_surplus_controller_with_surplus():
    """
    Scenario: clear PV surplus.
    Example:
        grid_kw = -3.0 kW  (3 kW export)
        wb_kw   =  0.0 kW  (WB currently off)
    Then:
        raw available = 0 - (-3) = 3 kW
        effective >= 3 kW (deltaP = 0)
        -> should start 1-phase charging with a reasonable current.
    """
    params = ControllerParams()
    ctrl = SurplusController(params)

    result = ctrl.step(grid_kw=-3.0, wb_kw=0.0)

    # available power ~3 kW, clipped and without deltaP
    assert result["p_available_kw"] == pytest.approx(3.0, abs=1e-6)

    # At startup and 3 kW, we expect 1-phase and a current > 0
    assert result["phase"] == 1
    assert result["current"] > 0
    assert params.min_current <= result["current"] <= params.max_current


def test_surplus_controller_high_surplus():
    """
    Scenario: high PV surplus.
    Example:
        grid_kw = -5.0 kW    (5 kW export)
        wb_kw   =  3.0 kW    (wallbox currently using 3 kW)

    Raw available = 3 - (-5) = 8 kW
    -> expected:
        p_available_kw = 8 kW
        phase = 3
        current > 0
    """
    params = ControllerParams()
    ctrl = SurplusController(params)

    result = ctrl.step(grid_kw=-5.0, wb_kw=3.0)

    # Check available power
    assert result["p_available_kw"] == pytest.approx(8.0, abs=1e-6)

    # At startup, 8 kW is clearly above threshold -> must switch to 3-phase
    assert result["phase"] == 3

    # Current must be > 0 and within allowed limits
    assert result["current"] > 0
    assert params.min_current <= result["current"] <= params.max_current
