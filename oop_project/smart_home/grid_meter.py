import requests
from typing import Optional


class GridMeterError(Exception):
    """Fehler beim Auslesen des Stromzählers / Tasmota."""
    pass


class GridMeter:
    """
    Kapselt den Zugriff auf den optischen Lesekopf (Tasmota).
    
    Parameter:
        ip: IPv4-Adresse des Lesekopfes
        timeout: HTTP-Timeout in Sekunden
    """

    def __init__(self, ip: str, timeout: float = 3.0):
        self.ip = ip
        self.timeout = timeout
        self.base_url = f"http://{ip}/cm"

        # optional: spätere Optimierung
        self.session = requests.Session()


    # ------------------------------------------------------------
    #  Hilfsfunktion: komplettes JSON holen
    # ------------------------------------------------------------
    def read_raw(self) -> dict:
        """
        Holt das vollständige JSON vom Lesekopf.
        Wirft GridMeterError bei HTTP-/Parsingfehlern.
        """
        try:
            r = self.session.get(
                self.base_url,
                params={"cmnd": "status 10"},
                timeout=self.timeout
            )
            r.raise_for_status()
            data = r.json()
            return data

        except Exception as e:
            raise GridMeterError(f"Fehler beim Auslesen des GridMeters: {e}") from e


    # ------------------------------------------------------------
    #  Hauptfunktion: aktuelle Netzleistung (kW)
    # ------------------------------------------------------------
    def read_power_kw(self) -> float:
        """
        Liefert die aktuelle Wirkleistung in kW.
        
        Power_cur > 0  → Netzbezug
        Power_cur < 0  → Einspeisung
        
        Wirft GridMeterError bei Problemen.
        """
        data = self.read_raw()

        try:
            power_w = data["StatusSNS"]["MT631"]["Power_cur"]
        except KeyError as e:
            raise GridMeterError(
                f"Ungültiges JSON: Power_cur fehlt ({e}) — raw: {data}"
            ) from e

        # Plausibilitätscheck (optional)
        if not isinstance(power_w, (int, float)):
            raise GridMeterError(f"Power_cur hat ungültigen Typ: {power_w}")

        # Umrechnung Watt → kW
        return power_w / 1000.0
