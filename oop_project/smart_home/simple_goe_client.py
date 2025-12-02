# smart_home/simple_goe_client.py

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

import requests


@dataclass
class GoEStatus:
    """Minimal normalized status information from go-e Charger."""
    car_state: Optional[str]
    phase_mode: Optional[int]      # 1 or 3
    ampere_allowed: Optional[int]  # current limit in A


class SimpleGoEClientError(Exception):
    """Error during communication with go-e Charger."""
    pass


class SimpleGoEClient:
    """
    Minimal HTTP client for go-e Charger using the local HTTP API v2.

    Uses:
      - GET http://<ip>/api/status
      - GET http://<ip>/api/set?amp=X
      - GET http://<ip>/api/set?psm=1|2
      - GET http://<ip>/api/set?alw=0|1
    """

    def __init__(self, ip: str, timeout: float = 3.0):
        self.ip = ip
        self.base_url = f"http://{ip}"
        self.timeout = timeout

    # -------------------------
    # Low-level helper methods
    # -------------------------

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET request returning JSON."""
        url = f"{self.base_url}{path}"
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise SimpleGoEClientError(f"GET {url} failed: {e}") from e

    def _get_set(self, params: Dict[str, Any]) -> None:
        """
        Helper for /api/set?...

        Example:
          _get_set({"amp": 14})
          _get_set({"psm": 1})
          _get_set({"alw": 1})
        """
        url = f"{self.base_url}/api/set"
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:
            raise SimpleGoEClientError(f"GET {url} with params {params} failed: {e}") from e

    # -------------------------
    # High-level API
    # -------------------------

    def get_raw_status(self) -> Dict[str, Any]:
        """
        Return the raw status JSON from the charger.

        Uses /api/status of the HTTP API v2.
        """
        return self._get_json("/api/status")

    def get_status_min(self) -> GoEStatus:
        """
        Return a minimal, normalized view on the charger status.

        Mapping for your box (API v2):

        - car_state:
            car = 1 -> "Idle"          (ready, no vehicle)
            car = 2 -> "Charging"
            car = 3 -> "Waiting"
            car = 4 -> "Finished"
            sonst -> str(car)

        - phase_mode:
            psm = 1 -> 1  (1-phasig)
            psm = 2 -> 3  (3-phasig)   (vgl. Forenbeiträge zur V2-API)

        - ampere_allowed:
            aus Feld "amp" (int)
        """
        data = self.get_raw_status()

        # ----- car_state -----
        car_state: Optional[str] = None
        car_raw = data.get("car", None)
        try:
            if car_raw is not None:
                car_int = int(car_raw)
                if car_int == 1:
                    car_state = "Idle"
                elif car_int == 2:
                    car_state = "Charging"
                elif car_int == 3:
                    car_state = "Waiting"
                elif car_int == 4:
                    car_state = "Finished"
                else:
                    car_state = str(car_raw)
        except Exception:
            car_state = str(car_raw) if car_raw is not None else None

        # ----- phase_mode (1 oder 3) -----
        phase_mode: Optional[int] = None
        psm_raw = data.get("psm", None)
        try:
            if psm_raw is not None:
                psm_int = int(psm_raw)
                if psm_int == 1:
                    phase_mode = 1
                elif psm_int == 2:
                    phase_mode = 3
        except Exception:
            phase_mode = None

        # ----- ampere_allowed -----
        ampere_allowed: Optional[int] = None
        amp_raw = data.get("amp", None)
        try:
            if amp_raw is not None:
                ampere_allowed = int(amp_raw)
        except Exception:
            ampere_allowed = None

        return GoEStatus(
            car_state=car_state,
            phase_mode=phase_mode,
            ampere_allowed=ampere_allowed,
        )

    def get_energy_since_connected_wh(self) -> Optional[float]:
        """
        Return energy in Wh since car connected (field 'wh').

        Laut go-e-Doku:
            'wh' = energie in Wh seit das aktuelle Fahrzeug verbunden ist.

        Gibt:
            - float-Wert in Wh, wenn verfügbar und parsbar
            - None, wenn Feld fehlt oder nicht interpretiert werden kann

        Zusätzlich werden Debug-Informationen auf der Konsole ausgegeben.
        """
        data = self.get_raw_status()
        raw_wh = data.get("wh")

        if raw_wh is None:
            return None

        try:
            wh = float(raw_wh)
        except Exception as e:
            print(f"[Warn] Could not parse 'wh' value from wallbox: {raw_wh!r} ({e})")
            return None

        if wh < 0:
            print(f"[Warn] Negative 'wh' from wallbox: {wh}")

        return wh

    # -------------------------
    # Setters
    # -------------------------

    def set_phase_mode(self, phase: int) -> None:
        """
        Set number of phases (1 or 3) via API v2.

        Internally, go-e uses:
          psm=1  -> 1-phasig
          psm=2  -> 3-phasig
        """
        if phase not in (1, 3):
            raise ValueError("phase must be 1 or 3")

        if phase == 1:
            psm_value = 1
        else:
            psm_value = 2

        self._get_set({"psm": psm_value})

    def set_ampere(self, ampere: int) -> None:
        """
        Set maximum charging current in Ampere via API v2.

        Uses /api/set?amp=<A>.
        """
        if ampere < 0:
            raise ValueError("ampere must be non-negative")

        self._get_set({"amp": ampere})

    def set_charging_mode(self, on: bool) -> None:
        """
        Enable or disable charging via API v2 using 'frc':

        frc = 1  -> force OFF
        frc = 2  -> force ON
        frc = 0  -> normal (no force)
        """
        if on:
            # Laden erzwingen EIN
            self._get_set({"frc": 2})
        else:
            # Laden erzwingen AUS
            self._get_set({"frc": 1})


