# %% Hauptskript
import time
import math
import requests
from goecharger_api_lite import GoeCharger
from pymodbus.client import ModbusTcpClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Wechselrichter
KOSTAL_IP   = "192.168.178.56"  # anpassen
KOSTAL_PORT = 1502
KOSTAL_UNIT = 71                # Modbus slave/device id

# Tasmota smart meter reader (Hichi) – IP address
METER_IP = "192.168.178.191"   # <-- anpassen

# Go-e charger IP
CHARGER_IP = "192.168.178.21"  # <-- anpassen

# Global switch: enable/disable PV surplus charging logic
PV_SURPLUS_MODE = True  # True = surplus charging active, False = do not touch charger

# Control parameters (kW thresholds and amp limits)
params = {
    "thres_1to3_start": 7.0,  # kW, threshold for 3-phase charging right after startup
    "thres_1to3":       5.8,  # kW, threshold for phase switch 1 -> 3
    "thres_3to1":       3.5,  # kW, threshold for phase switch 3 -> 1
    "thres_start":      2.0,  # kW, threshold for charge start
    "thres_stopp":      1.0,  # kW, threshold for charge stop
    "min_current":      10,   # A, minimum charging current
    "max_current":      16,   # A, maximum charging current
    "deltaP":           0.0   # kW, safety margin between available PV power and charging power
}

# Internal state
is_startup = True
phase = 1        # 1 or 3 phases currently used
current = 0      # A, currently set charging current


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
import struct
from pymodbus.client import ModbusTcpClient



def read_pv_power_kw():
    """
    Read 'Total power' from Kostal inverter via Modbus and return kW.
    Equivalent zu deinem alten read_kostal_inverter_data()['Total power'].

    Registeradresse laut altem Skript: 172 (2 Register, 32-bit float).
    In deinem alten Code:
      BinaryPayloadDecoder.fromRegisters(..., byteorder=BIG, wordorder=LITTLE)
      -> decode_32bit_float()
    Das wird hier über struct nachgebildet.
    """
    client = ModbusTcpClient(KOSTAL_IP, port=KOSTAL_PORT, timeout=3)
    client.connect()

    rr = client.read_holding_registers(172, count=2, device_id=KOSTAL_UNIT)
    if rr.isError():
        client.close()
        raise RuntimeError(f"Error reading Kostal Total power: {rr}")

    regs = rr.registers  # [reg0, reg1], 16-bit each

    # Entspricht byteorder=BIG, wordorder=LITTLE:
    # Wörter vertauscht, innerhalb des Wortes Big-Endian.
    raw_bytes = struct.pack('>HH', regs[1], regs[0])
    value_kw = struct.unpack('>f', raw_bytes)[0] / 1000.0  # wie in deinem alten Skript (/1000,1)

    client.close()
    return value_kw


def read_grid_power_kw():
    """
    Read current grid power from Tasmota smart meter (Power_cur) and
    return it in kW.

    Power_cur > 0  -> grid import (Bezug)
    Power_cur < 0  -> grid export (Einspeisung)
    """
    url = f"http://{METER_IP}/cm?cmnd=Status%2010"
    r = requests.get(url, timeout=3)
    data = r.json()

    sns = data.get("StatusSNS", {})
    mt = sns.get("MT631", {})
    p_w = mt.get("Power_cur")  # W

    if p_w is None:
        raise RuntimeError("Power_cur not found in Tasmota response")

    return float(p_w) / 1000.0  # kW


def read_wb_power_kw(ip):
    """
    Read go-e POWER_TOTAL via Modbus (Input Register 120, length 2)
    and return current charging power in kW.

    Value is encoded in 0.01 W (360000 -> 3.6 kW).
    Unrealistic register spikes (e.g. 42949 kW from 0xFFFFFFFF) are filtered.
    Fallback is always 0.0 kW.
    """
    client = ModbusTcpClient(ip, port=502, timeout=3)
    client.connect()

    rr = client.read_input_registers(120, count=2, device_id=1)

    if rr.isError():
        client.close()
        print(f"Warning: error reading POWER_TOTAL: {rr}")
        return 0.0

    regs = rr.registers  # list of two uint16
    raw = (regs[0] << 16) | regs[1]   # combine into uint32

    client.close()

    wb_kw = raw / 100000.0  # kW

    # sanity check — ignore nonsense from uninitialized register
    if wb_kw < 0 or wb_kw > 11.0:
        print(f"Warning: ignoring unrealistic WB power value: {wb_kw:.2f} kW "
              f"(raw={raw}, regs={regs})")
        return 0.0

    return wb_kw


def power2current(power_kw, phase_local):
    """
    Convert desired charging power (kW) and phase count (1 or 3) into a charging current (A).
    Uses the same linear mappings as in the original script.
    """
    if phase_local == 1:
        current_local = 4.4444 * power_kw + 1.1111
    else:
        current_local = 1.2345 * power_kw + 4.0100
    return current_local


def update_phase_and_current(available_power_kw):
    """
    Decide on new phase count (1/3) and new charging current (A)
    based on 'available' PV power (PV - house load),
    using the same logic as in your original script.

    Uses global variables:
      - is_startup
      - phase
      - current
      - params
    """
    global is_startup, phase, current

    # --- Phase selection ---------------------------------------------------
    if is_startup:
        # First run: use dedicated threshold
        if available_power_kw > params["thres_1to3_start"]:
            phase_new = 3
        else:
            phase_new = 1
    else:
        # Subsequent runs: hysteresis between 1 and 3 phases
        if phase == 1 and available_power_kw > params["thres_1to3"]:
            phase_new = 3
        elif phase == 3 and available_power_kw < params["thres_3to1"]:
            phase_new = 1
        else:
            phase_new = phase

    # --- Current selection -------------------------------------------------
    if ((current > 0 and available_power_kw > params["thres_stopp"]) or
        (current == 0 and available_power_kw > params["thres_start"])):
        current_new = math.floor(power2current(available_power_kw, phase_new))
        current_new = max(params["min_current"], min(current_new, params["max_current"]))
    else:
        current_new = 0

    return phase_new, current_new


# ---------------------------------------------------------------------------
# Main control loop
# ---------------------------------------------------------------------------

def main():
    global is_startup, phase, current

    charger = GoeCharger(CHARGER_IP)
    setValues = charger.SettableValueEnum()

    grid_list = []   # last grid power samples (kW)
    counter = 0      # 0..299, one step per second

    while True:
        try:
            if PV_SURPLUS_MODE:
                # Sample grid power every 10 seconds for averaging
                if counter % 10 == 0:
                    try:
                        grid_kw = read_grid_power_kw()
                        grid_list.append(grid_kw)
                    except Exception as e:
                        print(f"Warning: could not read grid power for avg: {e}")

                # Every 5 minutes or at startup: compute new settings
                if grid_list and (counter == 299 or is_startup):
                    # Read current charger state (phase, ampere, car state)
                    status_phase = charger.get_phase_mode()
                    phase = 1 if status_phase["phase_mode"] == "one" else 3

                    status_amp = charger.get_ampere()
                    current = status_amp["ampere_allowed"]

                    status_min = charger.get_status(status_type=charger.STATUS_MINIMUM)
                    car_state = status_min["car_state"]

                    # Average grid power over last 5 minutes
                    grid_avg_kw = sum(grid_list) / len(grid_list)

                    # Read actual wallbox power via Modbus
                    wb_power_kw = 0.0
                    try:
                        wb_power_kw = read_wb_power_kw(CHARGER_IP)
                    except Exception as e:
                        print(f"Warning: could not read WB power via Modbus: {e}")

                    # Available PV power for the system: P_PV_available ≈ P_WB - P_grid
                    available_kw = wb_power_kw - grid_avg_kw

                    # Apply safety offset and clamp at zero
                    effective_kw = max(0.0, available_kw - params["deltaP"])

                    print(
                        f"[5min] Grid_avg: {grid_avg_kw:6.2f} kW | "
                        f"WB_avg: {wb_power_kw:6.2f} kW | "
                        f"Available_eff: {effective_kw:6.2f} kW"
                    )

                    phase_new, current_new = update_phase_and_current(effective_kw)

                    # Charger control logic (analog zu deinem alten Skript)
                    if car_state != "Charging" and current_new == 0:
                        # keep charger off
                        pass

                    elif car_state == "Charging" and current_new == 0:
                        # stop charging
                        charger.set_charging_mode(setValues.ChargingMode.off)

                    elif car_state not in ("Idle", "Charging") and current_new > 0:
                        # start charging
                        if phase_new == 1:
                            charger.set_phase_mode(setValues.PhaseMode.one)
                        else:
                            charger.set_phase_mode(setValues.PhaseMode.three)
                        charger.set_ampere(current_new)
                        charger.set_charging_mode(setValues.ChargingMode.on)

                    elif car_state == "Charging" and current_new > 0:
                        # adjust ongoing charging
                        if phase == 1 and phase_new == 3:
                            charger.set_phase_mode(setValues.PhaseMode.three)
                            charger.set_ampere(current_new)
                        elif phase == 3 and phase_new == 1:
                            charger.set_phase_mode(setValues.PhaseMode.one)
                            charger.set_ampere(current_new)
                        else:
                            charger.set_ampere(current_new)

                    # Update state
                    is_startup = False
                    grid_list = []

                # Keep only last 30 samples (~5 minutes at 10 s step)
                if len(grid_list) > 30:
                    grid_list = grid_list[-30:]

            else:
                # PV_SURPLUS_MODE is False -> do not touch charger, reset state
                is_startup = True
                grid_list = []

            # ---- Debug output every loop: PV_now, Grid_now, WB_now ----
            try:
                # PV-Leistung direkt vom Wechselrichter (kW)
                try:
                    pv_now_kw = read_pv_power_kw()
                except Exception as e:
                    pv_now_kw = float("nan")
                    print(f"Debug: could not read PV power: {e}")

                # Momentane Grid-Leistung (kW) vom Zähler
                try:
                    grid_now_kw = read_grid_power_kw()
                except Exception as e:
                    grid_now_kw = float("nan")
                    print(f"Debug: could not read grid power (instant): {e}")

                # Momentane Wallbox-Leistung (kW) via Modbus
                try:
                    wb_now_kw = read_wb_power_kw(CHARGER_IP)
                except Exception as e:
                    wb_now_kw = float("nan")
                    print(f"Debug: could not read WB power (instant): {e}")

                # Available PV power for the system: P_PV_available ≈ P_WB - P_grid
                available_kw = wb_now_kw - grid_now_kw

                print(
                    f"PV_now: {pv_now_kw:6.2f} kW | "
                    f"Grid_now: {grid_now_kw:6.2f} kW | "
                    f"WB_now: {wb_now_kw:6.2f} kW | "
                    f"available_now: {available_kw:6.2f} kW"
                )
            except Exception as e:
                print(f"Debug error: {e}")

        except Exception as e:
            print(f"Error in main loop: {e}")

        counter = (counter + 1) % 300
        time.sleep(1)


if __name__ == "__main__":
    main()


# %%
