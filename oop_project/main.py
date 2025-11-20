# main_oop.py

import time
from typing import Optional

from goecharger_api_lite import GoeCharger

from grid_meter import GridMeter, GridMeterError
from pv_inverter import PVInverter, PVInverterError
from wallbox import Wallbox, WallboxError
from surplus_controller import SurplusController, ControllerParams


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# IP addresses
GRID_IP = "192.168.178.191"   # Tasmota / Hichi
PV_IP   = "192.168.178.56"    # Kostal inverter
WB_IP   = "192.168.178.21"    # go-e wallbox

# Modbus settings for PV inverter
PV_PORT = 1502
PV_UNIT = 71

# PV surplus mode: if False, controller does not touch the charger
PV_SURPLUS_MODE = True

# Sampling and control timing
SAMPLE_INTERVAL_SEC = 1       # main loop step
GRID_SAMPLE_EVERY   = 10      # grid samples every 10 s
CONTROL_PERIOD_SEC  = 300     # 5 min control interval
MAX_GRID_SAMPLES    = CONTROL_PERIOD_SEC // GRID_SAMPLE_EVERY  # ~30


# ---------------------------------------------------------------------------
# Helper: read snapshot for debug output
# ---------------------------------------------------------------------------

def read_snapshot_for_debug(
    grid_meter: GridMeter,
    pv_inv: PVInverter,
    wb: Wallbox
) -> None:
    """
    Read instantaneous values for debugging and print them.
    Does not affect controller state.
    """
    try:
        pv_kw = pv_inv.read_total_power_kw()
    except PVInverterError as e:
        pv_kw = float("nan")
        print(f"[Debug] PV read error: {e}")

    try:
        grid_kw = grid_meter.read_power_kw()
    except GridMeterError as e:
        grid_kw = float("nan")
        print(f"[Debug] Grid read error: {e}")

    try:
        wb_kw = wb.read_power_kw()
    except WallboxError as e:
        wb_kw = float("nan")
        print(f"[Debug] WB read error: {e}")

    print(
        f"[Now] PV: {pv_kw:6.2f} kW | "
        f"Grid: {grid_kw:6.2f} kW | "
        f"WB: {wb_kw:6.2f} kW"
    )


# ---------------------------------------------------------------------------
# Helper: apply controller decision to go-e charger
# ---------------------------------------------------------------------------

def apply_charger_decision(
    charger: GoeCharger,
    phase_new: int,
    current_new: int
) -> None:
    """
    Apply the controller's decision (phase + current) to the go-e charger
    using the HTTP API (goecharger_api_lite).
    """
    setValues = charger.SettableValueEnum()

    # Read current charger state
    status_min = charger.get_status(status_type=charger.STATUS_MINIMUM)
    car_state = status_min["car_state"]   # e.g. "Idle", "Charging", ...

    phase_status = charger.get_phase_mode()
    phase_current = 1 if phase_status["phase_mode"] == "one" else 3

    amp_status = charger.get_ampere()
    current_current = amp_status["ampere_allowed"]

    # Debug info
    print(
        f"[Control] car_state={car_state}, "
        f"phase_current={phase_current}, current_current={current_current}, "
        f"phase_new={phase_new}, current_new={current_new}"
    )

    # Case 1: keep charger off
    if car_state != "Charging" and current_new == 0:
        # do nothing
        return

    # Case 2: stop charging
    if car_state == "Charging" and current_new == 0:
        charger.set_charging_mode(setValues.ChargingMode.off)
        return

    # Case 3: start charging
    if car_state not in ("Idle", "Charging") and current_new > 0:
        if phase_new == 1:
            charger.set_phase_mode(setValues.PhaseMode.one)
        else:
            charger.set_phase_mode(setValues.PhaseMode.three)
        charger.set_ampere(current_new)
        charger.set_charging_mode(setValues.ChargingMode.on)
        return

    # Case 4: adjust ongoing charging
    if car_state == "Charging" and current_new > 0:
        # phase change 1 -> 3
        if phase_current == 1 and phase_new == 3:
            charger.set_phase_mode(setValues.PhaseMode.three)
            charger.set_ampere(current_new)
        # phase change 3 -> 1
        elif phase_current == 3 and phase_new == 1:
            charger.set_phase_mode(setValues.PhaseMode.one)
            charger.set_ampere(current_new)
        # same phase, adjust current only
        else:
            charger.set_ampere(current_new)


# ---------------------------------------------------------------------------
# Main loop (OOP orchestration)
# ---------------------------------------------------------------------------

def main():
    # Instantiate hardware objects
    grid_meter = GridMeter(GRID_IP)
    pv_inv     = PVInverter(PV_IP, port=PV_PORT, unit_id=PV_UNIT)
    wb         = Wallbox(WB_IP)

    # Instantiate controller with default parameters
    params = ControllerParams()
    controller = SurplusController(params)

    # Instantiate go-e HTTP API client
    charger = GoeCharger(WB_IP)

    # State for averaging
    grid_samples: list[float] = []
    counter = 0  # 0..CONTROL_PERIOD_SEC-1

    while True:
        try:
            # --- periodic grid sampling for averaging ---
            if counter % GRID_SAMPLE_EVERY == 0:
                try:
                    grid_kw = grid_meter.read_power_kw()
                    grid_samples.append(grid_kw)
                except GridMeterError as e:
                    print(f"[Warn] GridMeter error (avg): {e}")

                # keep only the last MAX_GRID_SAMPLES entries
                if len(grid_samples) > MAX_GRID_SAMPLES:
                    grid_samples = grid_samples[-MAX_GRID_SAMPLES:]

            # --- periodic control step (every CONTROL_PERIOD_SEC) ---
            if PV_SURPLUS_MODE and grid_samples and (counter == CONTROL_PERIOD_SEC - 1):
                # average grid power over last period
                grid_avg_kw = sum(grid_samples) / len(grid_samples)

                # read wallbox average power (instant value as approximation)
                try:
                    wb_kw = wb.read_power_kw()
                except WallboxError as e:
                    wb_kw = 0.0
                    print(f"[Warn] Wallbox power read error (avg): {e}")

                # controller step: compute phase, current, p_available
                result = controller.step(grid_kw=grid_avg_kw, wb_kw=wb_kw)

                print(
                    f"[5min] Grid_avg: {grid_avg_kw:6.2f} kW | "
                    f"WB_avg: {wb_kw:6.2f} kW | "
                    f"P_avail: {result['p_available_kw']:6.2f} kW | "
                    f"phase={result['phase']} | current={result['current']} A"
                )

                # apply controller decision to charger
                apply_charger_decision(
                    charger=charger,
                    phase_new=result["phase"],
                    current_new=result["current"]
                )

                # reset averaging window
                grid_samples = []

            # --- debug output each loop (instant snapshot) ---
            try:
                read_snapshot_for_debug(grid_meter, pv_inv, wb)
            except Exception as e:
                print(f"[Debug] snapshot error: {e}")

        except Exception as e:
            print(f"[Error] main loop: {e}")

        # update counter and sleep
        counter = (counter + 1) % CONTROL_PERIOD_SEC
        time.sleep(SAMPLE_INTERVAL_SEC)


if __name__ == "__main__":
    main()
