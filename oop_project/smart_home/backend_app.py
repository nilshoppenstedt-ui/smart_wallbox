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

SAMPLE_INTERVAL_SEC = 1
GRID_SAMPLE_EVERY   = 10
CONTROL_PERIOD_SEC  = 300
MAX_GRID_SAMPLES    = CONTROL_PERIOD_SEC // GRID_SAMPLE_EVERY  # ~30
CAR_STATUS_PERIOD_SEC = 300  # alle 5 Min Fahrzeugstatus holen



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
        self.counter: int = 0

        # Flag: gerade von monitor_only → pv_surplus gewechselt
        self.just_switched_to_pv = False

        # Gemeinsamer Status
        self.status = {
            "timestamp": None,
            "pv_kw": None,
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

            # Fahrzeugstatus (Renault)
            "car_soc": None,
            "car_autonomy_km": None,
            "car_plug_status": None,
            "car_charging_status": None,
            "car_status_timestamp": None,
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
        grid_kw = None
        wb_kw = None

        # PV
        try:
            pv_kw = self.pv_inv.read_total_power_kw()
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
            self.status["grid_kw"] = grid_kw
            self.status["wb_kw"] = wb_kw
            self.status["car_state"] = car_state
            self.status["p_available_now"] = p_available_now
            self.status["phase"] = phase_live
            self.status["current"] = current_live
            # grid_kw_avg, wb_kw_avg, p_available_kw werden im Control-Step gesetzt

    def update_car_status(self) -> None:
        """Fetch car battery status via CarClient and update status dict.

        Wird im Hintergrundthread alle CAR_STATUS_PERIOD_SEC Sekunden aufgerufen.
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
                self.status["car_status_timestamp"] = datetime.now().isoformat(timespec="seconds")
            return

        with self.lock:
            self.status["car_soc"]             = car_status.soc
            self.status["car_autonomy_km"]     = car_status.autonomy_km
            self.status["car_plug_status"]     = car_status.plug_status
            self.status["car_charging_status"] = car_status.charging_status
            self.status["car_status_timestamp"] = car_status.timestamp.isoformat(timespec="seconds")


    # ------------------------------------------------------------------
    # Hauptschleife (Background-Thread)
    # ------------------------------------------------------------------
    def run_loop(self):
        while True:
            try:
                # Live-Daten für Anzeige
                self.update_instant_snapshot()

                # Grid-Samples für Mittelung sammeln
                if self.counter % GRID_SAMPLE_EVERY == 0:
                    try:
                        g = self.grid_meter.read_power_kw()
                        self.grid_samples.append(g)
                    except GridMeterError as e:
                        print(f"[Warn] GridMeter error (avg): {e}")

                    if len(self.grid_samples) > MAX_GRID_SAMPLES:
                        self.grid_samples = self.grid_samples[-MAX_GRID_SAMPLES:]

                # Fahrzeugstatus alle CAR_STATUS_PERIOD_SEC Sekunden aktualisieren
                if self.counter % CAR_STATUS_PERIOD_SEC == 0:
                    self.update_car_status()

                # Modus abfragen
                mode = self.get_mode()

                # Flag für Modus-Wechsel lokal abfragen (unter Lock)
                with self.lock:
                    just_switched = self.just_switched_to_pv

                # Bedingung: entweder normales 5-Minuten-Ende ODER frischer Moduswechsel
                trigger_control = (
                    mode == "pv_surplus"
                    and self.grid_samples
                    and (self.counter == CONTROL_PERIOD_SEC - 1 or just_switched)
                )

                # Alle 5 min: Controller ausführen (nur im pv_surplus-Modus)
                if trigger_control:
                    grid_avg_kw = sum(self.grid_samples) / len(self.grid_samples)

                    try:
                        wb_kw_avg = self.wb.read_power_kw()
                    except WallboxError as e:
                        wb_kw_avg = 0.0
                        print(f"[Warn] Wallbox power read error (avg): {e}")

                    result = self.controller.step(grid_kw=grid_avg_kw, wb_kw=wb_kw_avg)

                    print(
                        f"[5min] Grid_avg: {grid_avg_kw:6.2f} kW | "
                        f"WB_avg: {wb_kw_avg:6.2f} kW | "
                        f"P_avail: {result['p_available_kw']:6.2f} kW | "
                        f"phase={result['phase']} | current={result['current']} A"
                    )

                    with self.lock:
                        self.status["grid_kw_avg"] = grid_avg_kw
                        self.status["wb_kw_avg"] = wb_kw_avg
                        self.status["p_available_kw"] = result["p_available_kw"]
                        self.just_switched_to_pv = False   # Moduswechsel-Trigger zurücksetzen

                    # Entscheidung an die Wallbox übergeben
                    self.apply_charger_decision(
                        phase_new=result["phase"],
                        current_new=result["current"]
                    )

                    # Fenster zurücksetzen
                    self.grid_samples = []

                # Sekunden-Zähler
                self.counter = (self.counter + 1) % CONTROL_PERIOD_SEC

            except Exception as e:
                print(f"[Error] main loop: {e}")

            time.sleep(SAMPLE_INTERVAL_SEC)

    # ------------------------------------------------------------------
    # Entscheidung an die go-e-Charger-API weitergeben
    # ------------------------------------------------------------------
    def apply_charger_decision(self, phase_new: int, current_new: int) -> None:
        """
        Wendet die vom Controller berechneten Einstellungen (Phase, Strom)
        auf die go-e Wallbox an (HTTP-API via SimpleGoEClient).
        """

        # Falls kein Charger verfügbar ist (z.B. auf einem System ohne Steuerung)
        if self.charger is None:
            print("[Warn] No charger client available – skipping apply_charger_decision.")
            return

        # Aktuellen Zustand lesen
        try:
            st = self.charger.get_status_min()
            car_state = st.car_state or "unknown"
            phase_current = st.phase_mode          # 1 oder 3 (oder None)
            current_current = st.ampere_allowed    # int oder None
        except SimpleGoEClientError as e:
            print(f"[Warn] Error reading charger state: {e}")
            return

        print(
            f"[Control] car_state={car_state}, "
            f"phase_current={phase_current}, current_current={current_current}, "
            f"phase_new={phase_new}, current_new={current_new}"
        )

        # Keine sinnvollen Istwerte gefunden → lieber nichts tun
        if phase_current is None or current_current is None:
            print("[Warn] Incomplete charger status (phase/current None) – skipping control action.")
            return

        # 1) Ausgeschaltet lassen (keine Ladung, kein neuer Strom)
        if car_state != "Charging" and current_new == 0:
            return

        # 2) Ladung stoppen
        if car_state == "Charging" and current_new == 0:
            try:
                # hart stoppen
                self.charger.set_charging_mode(False)  # → /api/set?frc=1
            except SimpleGoEClientError as e:
                print(f"[Warn] Error stopping charge: {e}")
            return

        # 3) Ladung starten
        if car_state not in ("Idle", "Charging") and current_new > 0:
            try:
                # Phase einstellen
                if phase_new == 1:
                    self.charger.set_phase_mode(1)    # → /api/set?psm=1
                else:
                    self.charger.set_phase_mode(3)    # → /api/set?psm=2

                # Strom einstellen
                self.charger.set_ampere(current_new)  # → /api/set?amp=...

                # Freigeben
                self.charger.set_charging_mode(True)  # → /api/set?frc=0
            except SimpleGoEClientError as e:
                print(f"[Warn] Error starting charge: {e}")
            return

        # 4) Ladung läuft, Parameter anpassen
        if car_state == "Charging" and current_new > 0:
            try:
                # Phasenwechsel 1 -> 3
                if phase_current == 1 and phase_new == 3:
                    self.charger.set_phase_mode(3)
                    self.charger.set_ampere(current_new)

                # Phasenwechsel 3 -> 1
                elif phase_current == 3 and phase_new == 1:
                    self.charger.set_phase_mode(1)
                    self.charger.set_ampere(current_new)

                # Phase gleich, nur Strom anpassen
                else:
                    self.charger.set_ampere(current_new)
            except SimpleGoEClientError as e:
                print(f"[Warn] Error adjusting charge parameters: {e}")




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
        html, body {
            margin: 0;
            padding: 0;
            overflow-y: hidden;   /* vertikale Scrollbar unterdrücken */
            overflow-x: hidden;   /* horizontale Scrollbar ebenfalls */
            height: 100%;         /* wichtig, verhindert Überhang */
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
        .mode-btn {
            background: #111827;
            border: 1px solid #4b5563;
            color: #e5e7eb;
            padding: 0.2rem 0.6rem;
            border-radius: 999px;
            font-size: 0.75rem;
            cursor: pointer;
            margin-left: 0.25rem;
        }
        .mode-btn.active {
            background: #2563eb;
            border-color: #2563eb;
        }
        .mode-btn:active {
            transform: scale(0.97);
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
    <div style="text-align: right;">
        <div class="mode-pill mode-pv" id="mode_indicator">
            <span class="dot"></span>
            <span id="mode_text">PV-Überschuss</span>
        </div>
        <div style="margin-top: 0.3rem;">
            <button class="mode-btn" id="btn_pv" onclick="setMode('pv_surplus')">
                PV-Überschuss
            </button>
            <button class="mode-btn" id="btn_monitor" onclick="setMode('monitor_only')">
                Nur Anzeige
            </button>
        </div>
        <div class="timestamp" id="timestamp">–</div>
    </div>
</header>
<main>
    <div class="cards">
        <div class="card">
            <h2>PV-Leistung</h2>
            <div class="value" id="pv_kw">– kW</div>
            <div class="label">Aktuelle PV-Leistung</div>
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
    </div>
</main>


<script>
    function formatKw(value) {
        if (value === null || value === undefined || isNaN(value)) {
            return "– kW";
        }
        return value.toFixed(2) + " kW";
    }

    function updateDashboard(data) {
        // Timestamp
        const tsElem = document.getElementById("timestamp");
        tsElem.textContent = data.timestamp || "–";

        // Mode
        const mode = data.mode || "unknown";
        const modeTextElem = document.getElementById("mode_text");
        const modeIndicator = document.getElementById("mode_indicator");
        const btnPv = document.getElementById("btn_pv");
        const btnMonitor = document.getElementById("btn_monitor");

        if (mode === "pv_surplus") {
            modeTextElem.textContent = "PV-Überschuss";
            modeIndicator.classList.add("mode-pv");
            modeIndicator.classList.remove("mode-off");
            if (btnPv && btnMonitor) {
                btnPv.classList.add("active");
                btnMonitor.classList.remove("active");
            }
        } else if (mode === "monitor_only") {
            modeTextElem.textContent = "Nur Anzeige";
            modeIndicator.classList.remove("mode-pv");
            modeIndicator.classList.add("mode-off");
            if (btnPv && btnMonitor) {
                btnPv.classList.remove("active");
                btnMonitor.classList.add("active");
            }
        } else {
            modeTextElem.textContent = "Modus: " + mode;
            modeIndicator.classList.remove("mode-pv");
            modeIndicator.classList.add("mode-off");
            if (btnPv && btnMonitor) {
                btnPv.classList.remove("active");
                btnMonitor.classList.remove("active");
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

        pvElem.textContent = formatKw(data.pv_kw);
        gridElem.textContent = formatKw(data.grid_kw);
        wbElem.textContent = formatKw(data.wb_kw);
        pavElem.textContent = formatKw(data.p_available_now);

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
