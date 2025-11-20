from dataclasses import dataclass
import math
from typing import Dict


@dataclass
class ControllerParams:
    """Container for controller thresholds and limits."""
    thres_1to3_start: float = 7.0   # kW, threshold for 3-phase charging at startup
    thres_1to3: float = 5.8         # kW, threshold for phase switch 1 -> 3
    thres_3to1: float = 3.5         # kW, threshold for phase switch 3 -> 1
    thres_start: float = 2.0        # kW, threshold for charge start
    thres_stopp: float = 1.0        # kW, threshold for charge stop
    min_current: int = 10           # A, minimum charging current
    max_current: int = 16           # A, maximum charging current
    deltaP: float = 0.0             # kW, safety margin between available PV and charging power


class SurplusController:
    """
    Encapsulates the surplus charging control logic.

    Input per step:
        grid_kw : float
            Average grid power in kW ( >0 import, <0 export ).
        wb_kw   : float
            Average wallbox power in kW.

    Internally computes:
        P_available_raw = P_WB - P_grid
        P_available     = max(0, P_available_raw - deltaP)

    And updates:
        self.phase  (1 or 3)
        self.current (A)
        self.p_available_kw
    """

    def __init__(self, params: ControllerParams | None = None):
        self.params = params if params is not None else ControllerParams()

        # Internal state
        self.is_startup: bool = True
        self.phase: int = 1       # 1 or 3
        self.current: int = 0     # A

        # Publicly accessible effective available power (after deltaP and clipping)
        self.p_available_kw: float = 0.0

    # ------------------------------------------------------------------
    # helper: map power (kW) + phase -> current (A)
    # ------------------------------------------------------------------
    def _power_to_current(self, power_kw: float, phase: int) -> float:
        """Convert desired charging power (kW) to current (A)."""
        if phase == 1:
            return 4.4444 * power_kw + 1.1111
        else:
            return 1.2345 * power_kw + 4.0100

    # ------------------------------------------------------------------
    # helper: core decision logic (phase + current) for given available power
    # ------------------------------------------------------------------
    def _update_phase_and_current(self, available_kw: float) -> tuple[int, int]:
        """
        Decide on new phase (1/3) and current (A) given the available PV power.

        Uses internal state:
            self.is_startup
            self.phase
            self.current
        and thresholds in self.params.
        """
        p = self.params

        # --- Phase selection ---
        if self.is_startup:
            if available_kw > p.thres_1to3_start:
                phase_new = 3
            else:
                phase_new = 1
        else:
            if self.phase == 1 and available_kw > p.thres_1to3:
                phase_new = 3
            elif self.phase == 3 and available_kw < p.thres_3to1:
                phase_new = 1
            else:
                phase_new = self.phase

        # --- Current selection ---
        if ((self.current > 0 and available_kw > p.thres_stopp) or
            (self.current == 0 and available_kw > p.thres_start)):
            current_new = math.floor(self._power_to_current(available_kw, phase_new))
            current_new = max(p.min_current, min(current_new, p.max_current))
        else:
            current_new = 0

        return phase_new, current_new

    # ------------------------------------------------------------------
    # public API: one control step
    # ------------------------------------------------------------------
    def step(self, grid_kw: float, wb_kw: float) -> Dict[str, float | int]:
        """
        Perform one control step.

        Parameters
        ----------
        grid_kw : float
            Grid power (kW), >0 import, <0 export.
        wb_kw : float
            Wallbox power (kW).

        Returns
        -------
        dict with keys:
            'phase'           : int (1 or 3)
            'current'         : int (A)
            'p_available_kw'  : float (effective available power)
        """

        # raw available PV power (PV - house load) from power balance:
        # P_grid = P_house + P_WB - P_PV  =>  P_PV = P_WB - P_grid
        p_available_raw = wb_kw - grid_kw

        # apply safety margin and clip at zero
        effective_kw = max(0.0, p_available_raw - self.params.deltaP)
        self.p_available_kw = effective_kw

        phase_new, current_new = self._update_phase_and_current(effective_kw)

        # update internal state
        self.phase = phase_new
        self.current = current_new
        self.is_startup = False

        return {
            "phase": self.phase,
            "current": self.current,
            "p_available_kw": self.p_available_kw,
        }
