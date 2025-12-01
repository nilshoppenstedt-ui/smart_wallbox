import threading
import time
from datetime import datetime
import asyncio

from flask import Flask, jsonify, Response, request

#from goecharger_api_lite import GoeCharger
from .simple_goe_client import SimpleGoEClient, SimpleGoEClientError

from .grid_meter import GridMeter, GridMeterError
from .pv_inverter import PVInverter, PVInverterError
from .wallbox import Wallbox, WallboxError
from .surplus_controller import SurplusController, ControllerParams
from .car_client import CarClient, CarClientError



# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

GRID_IP = "192.168.178.191"   # Tasmota / Hichi
PV_IP   = "192.168.178.56"    # Kostal inverter
WB_IP   = "192.168.178.21"    # go-e wallbox

PV_PORT = 1502
PV_UNIT = 71

SAMPLE_INTERVAL_SEC = 2
GRID_SAMPLE_EVERY   = 1
CONTROL_PERIOD      = 180
MAX_GRID_SAMPLES    = CONTROL_PERIOD // GRID_SAMPLE_EVERY 
CAR_STATUS_PERIOD   = 180  

# Battery saving: stop charging when SoC is high and data is fresh
BATTERY_SAVING_SOC_LIMIT    = 90.0      # [%] threshold for battery-saving stop
BATTERY_SAVING_MAX_AGE_SEC  = 600       # [s] max age of car status for SoC-based stop
BATTERY_SAVING_CHECK_PERIOD = 180


# ---------------------------------------------------------------------------
# AppState: zentrale Zustandsklasse
# ---------------------------------------------------------------------------

class AppState:
    """
    Hält den globalen Zustand:
    - Instanzen der Geräte
    - Controller
    - Live-Daten und Mittelwerte
    - aktiven Modus (pv_surplus / monitor_only)
    """

    def __init__(self):

        # ensure asyncio event loop for aiohttp / GoeCharger (Python 3.11+)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Geräte
        self.grid_meter = GridMeter(GRID_IP)
        self.pv_inv     = PVInverter(PV_IP, port=PV_PORT, unit_id=PV_UNIT)
        self.wb         = Wallbox(WB_IP)
        self.controller = SurplusController(ControllerParams())
        # self.charger    = GoeCharger(WB_IP)
        try:
            self.charger = SimpleGoEClient(WB_IP)
        except SimpleGoEClientError as e:
            print(f"[Warn] Could not initialize SimpleGoEClient: {e}")
            self.charger = None

        # CarClient (Renault)
        self.car_client = None
        try:
            self.car_client = CarClient()
        except CarClientError as e:
            print(f"[Warn] CarClient not initialized: {e}")

        # Zustand für Mittelung 
        self.grid_samples: list[float] = []

        # Loop counters (loop ticks)
        self.grid_counter: int       = 0      # for grid sampling
        self.control_counter: int    = 0      # for PV-surplus control period
        self.soc_counter: int        = 0      # for SoC protection checks
        self.car_status_counter: int = 0      # for Renault car status polling

        # Flag: gerade von monitor_only → pv_surplus gewechselt
        self.just_switched_to_pv = False

        # Gemeinsamer Status
        self.status = {
            "timestamp": None,
            "pv_kw": None,
            "pv1_kw": None,
            "pv2_kw": None,
            "pv3_kw": None,
            "grid_kw": None,
            "wb_kw": None,
            "grid_kw_avg": None,
            "wb_kw_avg": None,
            "p_available_kw": None,     # 5-Minuten-Regelwert
            "p_available_now": None,    # Live-Wert für Anzeige
            "phase": None,              # Live-Phase der WB
            "current": None,            # Live-Strom der WB
            "mode": "pv_surplus",       # oder "monitor_only"
            "car_state": None,
            "soc_protection": True,     # stoppe Ladung, wenn SoC ausreichend hoch

            # Fahrzeugstatus (Renault)
            "car_soc": None,
            "car_autonomy_km": None,
            "car_plug_status": None,
            "car_charging_status": None,
            "car_status_timestamp": None,
            "car_status_valid": False,
            "car_status_last_attempt": None,
        }

        self.lock = threading.Lock()

    # ------------------------------------------------------------------
    # Mode-Handling
    # ------------------------------------------------------------------
    def get_mode(self) -> str:
        with self.lock:
            return self.status.get("mode", "pv_surplus")

    def set_mode(self, mode: str) -> None:
        if mode not in ("pv_surplus", "monitor_only"):
            raise ValueError(f"Unsupported mode: {mode}")

        with self.lock:
            old_mode = self.status.get("mode", "pv_surplus")
            self.status["mode"] = mode

            # Wenn von monitor_only → pv_surplus gewechselt wird:
            if old_mode == "monitor_only" and mode == "pv_surplus":
                # Flag setzen: beim nächsten Loop-Durchlauf sofort regeln
                self.just_switched_to_pv = True

    # ------------------------------------------------------------------
    # Live-Snapshot (wird jede Sekunde aufgerufen)
    # ------------------------------------------------------------------
    def update_instant_snapshot(self) -> None:
        """
        Lies PV, Grid, WB sowie Phase/Strom der Wallbox und aktualisiere status.
        """
        pv_kw = None
        pv1_kw = None
        pv2_kw = None
        pv3_kw = None
        grid_kw = None
        wb_kw = None

        # PV
        try:
            pv_kw = self.pv_inv.read_total_power_kw()
            string_powers = self.pv_inv.read_string_powers_kw()
            pv1_kw = string_powers.get("pv1_kw")
            pv2_kw = string_powers.get("pv2_kw")
            pv3_kw = string_powers.get("pv3_kw")
        except PVInverterError as e:
            print(f"[Debug] PV read error: {e}")

        # Grid
        try:
            grid_kw = self.grid_meter.read_power_kw()
        except GridMeterError as e:
            print(f"[Debug] Grid read error: {e}")

        # Wallbox-Leistung
        try:
            wb_kw = self.wb.read_power_kw()
        except WallboxError as e:
            print(f"[Debug] WB read error: {e}")
            wb_kw = None

        # Live p_available_now berechnen
        p_available_now = None
        try:
            if grid_kw is not None and wb_kw is not None:
                # gleiche Definition wie im Controller, aber auf Momentanwerten:
                # P_pv ≈ P_wb - P_grid; P_available = max(0, P_pv - deltaP)
                p_raw = wb_kw - grid_kw
                p_available_now = max(0.0, p_raw - self.controller.params.deltaP)
        except Exception as e:
            print(f"[Debug] p_available_now calc error: {e}")

        # Live Phase / Strom / Fahrzeugstatus
        phase_live = None
        current_live = None
        car_state = None

        if self.charger is not None:
            try:
                # minimale, normalisierte Sicht auf den Status holen
                st = self.charger.get_status_min()
                car_state = st.car_state
                phase_live = st.phase_mode      # 1 oder 3
                current_live = st.ampere_allowed
            except SimpleGoEClientError as e:
                print(f"[Debug] Charger status error: {e}")
        else:
            # z.B. auf dem Pi, falls SimpleGoEClient nicht initialisiert werden konnte
            print("[Debug] Charger object is None – no live phase/current read")

        # Status aktualisieren
        with self.lock:
            self.status["timestamp"] = datetime.now().isoformat(timespec="seconds")
            self.status["pv_kw"] = pv_kw
            self.status["pv1_kw"] = pv1_kw
            self.status["pv2_kw"] = pv2_kw
            self.status["pv3_kw"] = pv3_kw
            self.status["grid_kw"] = grid_kw
            self.status["wb_kw"] = wb_kw
            self.status["car_state"] = car_state
            self.status["p_available_now"] = p_available_now
            self.status["phase"] = phase_live
            self.status["current"] = current_live
            # grid_kw_avg, wb_kw_avg, p_available_kw werden im Control-Step gesetzt

    def update_car_status(self) -> None:
        """Fetch car battery status via CarClient and update status dict.

        Wird im Hintergrundthread alle CAR_STATUS_PERIOD Sekunden aufgerufen.
        """
        # Wenn kein CarClient existiert (z.B. keine Credentials): nichts tun
        if self.car_client is None:
            return

        try:
            car_status = self.car_client.read_status()
        except CarClientError as e:
            print(f"[Warn] Car status read error: {e}")
            # nur Zeitstempel aktualisieren, damit man sieht, dass es versucht wurde
            with self.lock:
                self.status["car_status_valid"] = False
                self.status["car_status_last_attempt"] = datetime.now().isoformat(timespec="seconds")
            return

        with self.lock:
            self.status["car_soc"]                  = car_status.soc
            self.status["car_autonomy_km"]          = car_status.autonomy_km
            self.status["car_plug_status"]          = car_status.plug_status
            self.status["car_charging_status"]      = car_status.charging_status
            self.status["car_status_timestamp"]     = car_status.timestamp.isoformat(timespec="seconds")
            self.status["car_status_valid"]         = True
            self.status["car_status_last_attempt"]  = car_status.timestamp.isoformat(timespec="seconds")


    def check_battery_saving_stop(self) -> tuple[bool, float | None]:
        """Return (battery_saving_stop, soc_value).

        battery_saving_stop ist nur dann True, wenn:
        - car_status_valid == True
        - car_soc in [0, 100]
        - car_status_timestamp existiert und jünger als BATTERY_SAVING_MAX_AGE_SEC ist
        - car_soc >= BATTERY_SAVING_SOC_LIMIT

        Bei Fehlern oder fehlenden/alten Daten immer (False, None).
        """
        battery_saving_stop = False
        soc_value = None
        try:
            with self.lock:
                soc_value = self.status.get("car_soc")
                ts_str = self.status.get("car_status_timestamp")
                valid = self.status.get("car_status_valid", False)

            if valid and soc_value is not None and isinstance(soc_value, (int, float)):
                ts = None
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except Exception:
                        ts = None

                if ts is not None:
                    if ts.tzinfo is not None:
                        now = datetime.now(ts.tzinfo)
                    else:
                        now = datetime.now()
                    age_sec = (now - ts).total_seconds()

                    if (
                        0.0 <= soc_value <= 100.0 and
                        0.0 <= age_sec <= BATTERY_SAVING_MAX_AGE_SEC and
                        soc_value >= BATTERY_SAVING_SOC_LIMIT
                    ):
                        battery_saving_stop = True

        except Exception as e:
            print(f"[Debug] battery_saving_stop evaluation error: {e}")
            battery_saving_stop = False

        return battery_saving_stop, soc_value


    # ------------------------------------------------------------------
    # Hauptschleife (Background-Thread)
    # ------------------------------------------------------------------
    def run_loop(self):
        while True:
            try:
                # Live snapshot for dashboard
                self.update_instant_snapshot()

                # ----------------------------------------------------------
                # Grid samples for averaging (based on grid_counter)
                # ----------------------------------------------------------
                if self.grid_counter == 0:
                    try:
                        g = self.grid_meter.read_power_kw()
                        self.grid_samples.append(g)
                    except GridMeterError as e:
                        print(f"[Warn] GridMeter error (avg): {e}")

                    if len(self.grid_samples) > MAX_GRID_SAMPLES:
                        self.grid_samples = self.grid_samples[-MAX_GRID_SAMPLES:]

                # ----------------------------------------------------------
                # Update car status periodically (Renault API)
                # based on car_status_counter
                # ----------------------------------------------------------
                if self.car_status_counter == 0:
                    self.update_car_status()

                # Query mode
                mode = self.get_mode()

                # Check flag for fresh mode switch (under lock)
                with self.lock:
                    just_switched = self.just_switched_to_pv
                    soc_protection = self.status.get("soc_protection", True)
                    current_phase = self.status.get("phase")

                # Condition for PV surplus controller activation
                # CONTROL_PERIOD is interpreted as number of loop ticks
                trigger_control = (
                    mode == "pv_surplus"
                    and self.grid_samples
                    and (self.control_counter == CONTROL_PERIOD - 1 or just_switched)
                )

                # Condition for SoC-check in monitor_only mode
                # BATTERY_SAVING_CHECK_PERIOD is in loop ticks, not seconds
                soc_control = (
                    soc_protection
                    and mode == "monitor_only"
                    and (self.soc_counter == 0)
                )

                # ----------------------------------------------------------
                # Unified SoC-check (only once per loop if relevant)
                # ----------------------------------------------------------
                battery_saving_stop = False
                soc_value = None

                if soc_protection and (trigger_control or soc_control):
                    battery_saving_stop, soc_value = self.check_battery_saving_stop()

                # ----------------------------------------------------------
                # PV Surplus Controller
                # ----------------------------------------------------------
                if trigger_control:
                    grid_avg_kw = sum(self.grid_samples) / len(self.grid_samples)

                    try:
                        wb_kw_avg = self.wb.read_power_kw()
                    except WallboxError as e:
                        wb_kw_avg = 0.0
                        print(f"[Warn] Wallbox power read error (avg): {e}")

                    result = self.controller.step(grid_kw=grid_avg_kw, wb_kw=wb_kw_avg)

                    # Apply battery saving inside surplus mode
                    if battery_saving_stop:
                        print(
                            f"[Control] Battery-saving stop active "
                            f"(SoC={soc_value:.1f} %) – forcing current to 0 A."
                        )
                        result["current"] = 0

                    print(
                        f"[5min] Grid_avg: {grid_avg_kw:6.2f} kW | "
                        f"WB_avg: {wb_kw_avg:6.2f} kW | "
                        f"P_avail: {result['p_available_kw']:6.2f} kW | "
                        f"phase={result['phase']} | current={result['current']} A"
                    )

                    # Update status
                    with self.lock:
                        self.status["grid_kw_avg"] = grid_avg_kw
                        self.status["wb_kw_avg"] = wb_kw_avg
                        self.status["p_available_kw"] = result["p_available_kw"]
                        self.just_switched_to_pv = False

                    # Apply decision to wallbox
                    self.apply_charger_decision(
                        phase_new=result["phase"],
                        current_new=result["current"]
                    )

                    # Reset averaging buffer
                    self.grid_samples = []

                # ----------------------------------------------------------
                # SoC protection in monitor_only mode (no PV control)
                # ----------------------------------------------------------
                if soc_control and battery_saving_stop:
                    print(
                        f"[Control] Battery-saving stop (monitor_only, "
                        f"SoC={soc_value:.1f} %) – forcing current to 0 A."
                    )

                    # Phase fallback: use current phase if known
                    if isinstance(current_phase, int) and current_phase in (1, 3):
                        phase_new = current_phase
                    else:
                        phase_new = 1

                    self.apply_charger_decision(
                        phase_new=phase_new,
                        current_new=0
                    )

                # ----------------------------------------------------------
                # Advance all loop counters (in ticks, not seconds)
                # ----------------------------------------------------------
                self.grid_counter = (self.grid_counter + 1) % GRID_SAMPLE_EVERY
                self.control_counter = (self.control_counter + 1) % CONTROL_PERIOD
                self.soc_counter = (self.soc_counter + 1) % BATTERY_SAVING_CHECK_PERIOD
                self.car_status_counter = (self.car_status_counter + 1) % CAR_STATUS_PERIOD

            except Exception as e:
                print(f"[Error] main loop: {e}")

            time.sleep(SAMPLE_INTERVAL_SEC)



# ---------------------------------------------------------------------------
# Flask-App und Routen
# ---------------------------------------------------------------------------

app_state = AppState()
app = Flask(__name__)


@app.route("/api/status", methods=["GET"])
def api_status():
    with app_state.lock:
        return jsonify(app_state.status)


@app.route("/api/mode", methods=["GET", "POST"])
def api_mode():
    if request.method == "GET":
        return jsonify({"mode": app_state.get_mode()})

    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if not mode:
        return jsonify({"error": "mode is required"}), 400

    try:
        app_state.set_mode(mode)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"status": "ok", "mode": mode})


@app.route("/api/soc_protection", methods=["POST"])
def api_soc_protection():
    """Toggle or set SoC protection flag in AppState.status.

    Expects JSON body: { "value": true/false }
    """
    data = request.get_json(silent=True) or {}
    value = data.get("value", None)

    if not isinstance(value, bool):
        return jsonify({"error": "value must be a boolean"}), 400

    with app_state.lock:
        app_state.status["soc_protection"] = value

    return jsonify({"status": "ok", "soc_protection": value})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# HTML-Dashboard (inline)
# ---------------------------------------------------------------------------

HTML_PAGE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <title>PV & Wallbox Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        /* Standard: Scrollen auf mobilen Geräten erlauben */
        html, body {
            margin: 0;
            padding: 0;
            overflow-y: auto;     /* Handy kann scrollen */
            overflow-x: hidden;
            height: 100%;
        }

        /* Kiosk-Modus für das 5"-Display (Landscape, geringe Höhe) */
        @media (min-width: 700px) and (max-height: 600px) {
            html, body {
                overflow-y: hidden;   /* Kein Scrollen auf dem Kiosk */
            }

            /* Grid auf 4 Spalten festsetzen → 2 Zeilen (4 + 3 Kacheln) */
            .cards {
                grid-template-columns: repeat(4, 1fr);
                gap: 0.75rem;         /* Optional: etwas kompakter */
            }

            /* Optional: etwas weniger Padding, um Höhe zu sparen */
            main {
                padding: 0.75rem;
            }
        }


        body {
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #111827;
            color: #e5e7eb;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }
        header {
            padding: 1rem 1.5rem;
            background: #1f2937;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        header h1 {
            font-size: 1.3rem;
            margin: 0;
        }
        header .timestamp {
            font-size: 0.9rem;
            color: #9ca3af;
        }
        main {
            flex: 1;
            padding: 1rem;
        }
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }
        .card {
            background: #111827;
            border-radius: 0.75rem;
            padding: 1rem;
            border: 1px solid #374151;
        }
        .card h2 {
            font-size: 0.9rem;
            margin: 0 0 0.5rem 0;
            color: #9ca3af;
        }
        .value {
            font-size: 1.7rem;
            font-weight: 600;
        }
        .value.small {
            font-size: 1.2rem;
        }
        .label {
            font-size: 0.8rem;
            color: #9ca3af;
        }
        .grid-positive {
            color: #f97373;
        }
        .grid-negative {
            color: #34d399;
        }
        .mode-pill {
            display: inline-flex;
            align-items: center;
            padding: 0.2rem 0.6rem;
            border-radius: 999px;
            font-size: 0.75rem;
            border: 1px solid #4b5563;
        }
        .mode-pill span.dot {
            width: 8px;
            height: 8px;
            border-radius: 999px;
            margin-right: 0.4rem;
        }
        .mode-pv span.dot {
            background: #22c55e;
        }
        .mode-off span.dot {
            background: #ef4444;
        }
        .car-state {
            font-size: 0.85rem;
            color: #e5e7eb;
        }
        .car-state span {
            font-weight: 600;
        }

        /* große Buttons für die Modus-Kachel */
        .mode-btn {
            background: #111827;
            border: 1px solid #4b5563;
            color: #e5e7eb;
            padding: 0.75rem 1rem;
            border-radius: 999px;
            font-size: 0.95rem;
            cursor: pointer;
            width: 100%;
            text-align: center;
        }
        .mode-btn.active {
            background: #2563eb;
            border-color: #2563eb;
        }
        .mode-btn:active {
            transform: scale(0.97);
        }

        /* Layout für die Modus-Kachel */
        .card-mode .mode-card-body {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            margin-top: 0.75rem;
        }
        .card-mode .mode-indicator-row {
            margin-top: 0.25rem;
        }

        footer {
            padding: 0.5rem 1.5rem;
            font-size: 0.75rem;
            color: #6b7280;
            border-top: 1px solid #374151;
        }
        @media (max-width: 480px) {
            header h1 {
                font-size: 1.1rem;
            }
            .value {
                font-size: 1.4rem;
            }
        }
    </style>
</head>
<body>
<header>
    <div>
        <h1>PV &amp; Wallbox Monitor</h1>
        <div class="car-state">
            Fahrzeug: <span id="car_state">–</span>
        </div>
    </div>
    <div class="timestamp" id="timestamp">–</div>
</header>
<main>
    <div class="cards">
        <div class="card">
            <h2>PV-Leistung</h2>
            <div class="value" id="pv_kw">– kW</div>
            <div class="label">Aktuelle PV-Leistung</div>
            <div class="label" id="pv_strings">–</div>
        </div>
        <div class="card">
            <h2>Netzleistung</h2>
            <div class="value" id="grid_kw">– kW</div>
            <div class="label">&gt;0: Bezug, &lt;0: Einspeisung</div>
        </div>
        <div class="card">
            <h2>Wallbox-Leistung</h2>
            <div class="value" id="wb_kw">– kW</div>
            <div class="label">Momentane Ladeleistung</div>
        </div>
        <div class="card">
            <h2>Verfügbare Leistung</h2>
            <div class="value" id="p_available_kw">– kW</div>
            <div class="label">Live berechnete PV-Überschussleistung</div>
        </div>
        <div class="card">
            <h2>Ladeeinstellungen</h2>
            <div class="value small">
                Phase: <span id="phase">–</span><br>
                Strom: <span id="current">–</span> A
            </div>
            <div class="label">Ist-Phase und Ist-Strom der Wallbox</div>
        </div>
        <div class="card">
            <div class="card-title">Fahrzeug</div>
            <div class="value">
                <span id="car_soc">–</span>
                <span class="unit">%</span>
            </div>
            <div class="sub">
                Reichweite: <span id="car_autonomy">–</span> km
            </div>
            <div class="sub small">
                Plug: <span id="car_plug_status">–</span> |
                Charging: <span id="car_charging_status">–</span>
            </div>
            <div class="sub small">
                Stand: <span id="car_status_timestamp">–</span>
            </div>
        </div>

        <!-- Kachel für den Betriebsmodus -->
        <div class="card card-mode">
            <h2>Betriebsmodus</h2>
            <div class="mode-card-body">
                <button class="mode-btn" id="btn_pv" onclick="setMode('pv_surplus')">
                    PV-Überschuss
                </button>
                <button class="mode-btn" id="btn_monitor" onclick="setMode('monitor_only')">
                    Nur Anzeige
                </button>
                <button class="mode-btn" id="btn_soc" onclick="toggleSocProtection()">
                    SoC-Schutz: –
                </button>
            </div>
        </div>

    </div>
</main>


<script>
    function formatKw(value) {
        if (value === null || value === undefined || isNaN(value)) {
            return "– kW";
        }
        return value.toFixed(2) + " kW";
    }

    // Track current SoC protection state in frontend
    let socProtection = true;

    function updateDashboard(data) {
        // Timestamp
        const tsElem = document.getElementById("timestamp");
        tsElem.textContent = data.timestamp || "–";

        // Mode buttons
        const mode = data.mode || "unknown";
        const btnPv = document.getElementById("btn_pv");
        const btnMonitor = document.getElementById("btn_monitor");
        const btnSoc = document.getElementById("btn_soc");

        if (mode === "pv_surplus") {
            if (btnPv && btnMonitor) {
                btnPv.classList.add("active");
                btnMonitor.classList.remove("active");
            }
        } else if (mode === "monitor_only") {
            if (btnPv && btnMonitor) {
                btnPv.classList.remove("active");
                btnMonitor.classList.add("active");
            }
        } else {
            if (btnPv && btnMonitor) {
                btnPv.classList.remove("active");
                btnMonitor.classList.remove("active");
            }
        }

        // SoC protection button (text + highlighting)
        if (typeof data.soc_protection === "boolean") {
            socProtection = data.soc_protection;
        }
        if (btnSoc) {
            if (socProtection) {
                btnSoc.textContent = "SoC-Schutz: aktiv";
                btnSoc.classList.add("active");
            } else {
                btnSoc.textContent = "SoC-Schutz: inaktiv";
                btnSoc.classList.remove("active");
            }
        }


        // Car state
        const carStateElem = document.getElementById("car_state");
        carStateElem.textContent = data.car_state || "unbekannt";

        // Fahrzeugstatus
        if (data.car_soc != null) {
            document.getElementById("car_soc").textContent = data.car_soc.toFixed
                ? data.car_soc.toFixed(0)
                : data.car_soc;
        } else {
            document.getElementById("car_soc").textContent = "–";
        }

        if (data.car_autonomy_km != null) {
            document.getElementById("car_autonomy").textContent = data.car_autonomy_km;
        } else {
            document.getElementById("car_autonomy").textContent = "–";
        }

        document.getElementById("car_plug_status").textContent =
            data.car_plug_status != null ? data.car_plug_status : "–";

        document.getElementById("car_charging_status").textContent =
            data.car_charging_status != null ? data.car_charging_status : "–";

        document.getElementById("car_status_timestamp").textContent =
            data.car_status_timestamp != null ? data.car_status_timestamp : "–";


        // PV, Grid, WB, P_available_now
        const pvElem = document.getElementById("pv_kw");
        const gridElem = document.getElementById("grid_kw");
        const wbElem = document.getElementById("wb_kw");
        const pavElem = document.getElementById("p_available_kw");
        const pvStringsElem = document.getElementById("pv_strings");

        pvElem.textContent = formatKw(data.pv_kw);
        gridElem.textContent = formatKw(data.grid_kw);
        wbElem.textContent = formatKw(data.wb_kw);
        pavElem.textContent = formatKw(data.p_available_now);

         // PV strings: short summary "1.2 / 0.3 / 1.5"
        if (pvStringsElem) {
            const v1 = data.pv1_kw;
            const v2 = data.pv2_kw;
            const v3 = data.pv3_kw;

            function fmtStringKw(val) {
                if (val === null || val === undefined || typeof val !== "number" || isNaN(val)) {
                    return "-";
                }
                return val.toFixed(2);
            }

            const s1 = fmtStringKw(v1);
            const s2 = fmtStringKw(v2);
            const s3 = fmtStringKw(v3);

            if (s1 === "-" && s2 === "-" && s3 === "-") {
                pvStringsElem.textContent = "–";
            } else {
                pvStringsElem.textContent = `${s1} / ${s2} / ${s3}`;
            }
        }

        // Grid color coding
        gridElem.classList.remove("grid-positive", "grid-negative");
        if (typeof data.grid_kw === "number") {
            if (data.grid_kw > 0.05) {
                gridElem.classList.add("grid-positive");  // Bezug
            } else if (data.grid_kw < -0.05) {
                gridElem.classList.add("grid-negative");  // Einspeisung
            }
        }

        // Phase & current (live)
        const phaseElem = document.getElementById("phase");
        const currentElem = document.getElementById("current");
        phaseElem.textContent = (data.phase === null || data.phase === undefined) ? "–" : data.phase;
        currentElem.textContent = (data.current === null || data.current === undefined) ? "–" : data.current;
    }

    async function fetchStatus() {
        try {
            const response = await fetch("/api/status");
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            const data = await response.json();
            updateDashboard(data);
        } catch (err) {
            console.error("Fehler beim Abrufen des Status:", err);
        }
    }

    async function setMode(mode) {
        try {
            const response = await fetch("/api/mode", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode: mode })
            });
            if (!response.ok) {
                console.error("Fehler beim Setzen des Modus:", response.status);
                return;
            }
            const data = await response.json();
            console.log("Mode set to:", data.mode);
            // Optional sofort neu laden:
            // fetchStatus();
        } catch (err) {
            console.error("Fehler beim Setzen des Modus:", err);
        }
    }

    async function toggleSocProtection() {
        // Locally toggle and send desired new state to backend
        const newValue = !socProtection;
        try {
            const response = await fetch("/api/soc_protection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ value: newValue })
            });
            if (!response.ok) {
                console.error("Fehler beim Setzen von soc_protection:", response.status);
                return;
            }
            const data = await response.json();
            // Backend is authoritative: use returned value
            if (typeof data.soc_protection === "boolean") {
                socProtection = data.soc_protection;
            }
            // Refresh UI from backend state
            fetchStatus();
        } catch (err) {
            console.error("Fehler beim Setzen von soc_protection:", err);
        }
    }


    // Initial fetch and periodic polling
    fetchStatus();
    setInterval(fetchStatus, 2000);
</script>
</body>
</html>
"""



@app.route("/", methods=["GET"])
def index():
    return Response(HTML_PAGE, mimetype="text/html")


# ---------------------------------------------------------------------------
# Hintergrund-Thread starten
# ---------------------------------------------------------------------------

def start_background_loop():
    t = threading.Thread(target=app_state.run_loop, daemon=True)
    t.start()


start_background_loop()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
