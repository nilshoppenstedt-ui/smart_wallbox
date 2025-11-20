Smart Home PV Surplus Charging
==============================
Automated control of a go-e Charger based on PV production, grid flow, and live measurements

This project implements a modular, object-oriented control system for photovoltaic surplus charging of an electric vehicle. It provides:

- Live PV power measurement (Kostal inverter via Modbus TCP)
- Live grid import/export measurement (Hichi Tasmota SML optical probe)
- Live wallbox power measurement (go-e Charger via Modbus TCP or API)
- Automatic calculation of available PV surplus
- Phase switching (1-phase or 3-phase) and current control
- A local web dashboard for live monitoring and mode switching
- Object-oriented design with unit tests

The system runs continuously on a PC or a Raspberry Pi and provides a local web interface.

Features
=========
1. Live data updated every second:
- PV power
- Grid import/export
- Wallbox charging power
- Instantaneous available surplus
- Charging phase (1 or 3)
- Charging current
- Vehicle charging state

2. Control algorithm executed every 5 minutes:
- Calculates 5-minute averages of grid and wallbox power
- Determines appropriate charging phase and current
- Starts or stops charging depending on surplus thresholds

3. Two operating modes:
- pv_surplus: automatic charging control
- monitor_only: read-only mode without controlling the wallbox

4. Web dashboard:
- Accessible at http://device-ip:8080
- Shows all live measurements
- Displays latest controller decisions
- Allows switching between pv_surplus and monitor_only modes

Project Structure
=================
oop_project/
smart_home/
init.py
backend_app.py
grid_meter.py
pv_inverter.py
surplus_controller.py
wallbox.py
tests/
test_grid_meter_live.py
test_pv_inverter_live.py
test_surplus_controller.py
test_wallbox_live.py
conftest.py

Running the Backend
===================
Navigate into the project directory:

cd oop_project
Start the backend using:
python -m smart_home.backend_app
The dashboard becomes available at:
http://device-ip:8080/

Running Unit Tests
==================

Run all tests with:
pytest -v

Architecture Overview
=====================

1. Object-oriented device classes:

GridMeter:
Reads current grid import or export from the SML/Tasmota optical reader

PVInverter:
Reads PV output power via Modbus TCP from a Kostal inverter

Wallbox:
Reads wallbox power and sets charging phase and current using go-e API or Modbus

SurplusController:
Contains the main control logic for calculating surplus and charging settings

AppState:
Background process that reads live data, executes the controller, stores state, and exposes API endpoints

2. Background thread:
- Updates live values every second
- Stores grid samples for averaging
- Runs controller every 5 minutes
- Updates shared application state

3. Flask Web API:

Route /api/status:
Returns all live and averaged values as JSON

Route /api/mode:
Gets or sets the current operating mode

Route /:
Serves the dashboard web interface

Hardware Integration
====================
PV Inverter (Kostal):
Connected via Modbus TCP

Grid Meter:
Smart electricity meter with SML optical interface using Hichi Tasmota WiFi reader

Wallbox (go-e Charger):
Controlled using go-e Modbus or rest API through goecharger_api_lite

Raspberry Pi Deployment
=======================
1. Install Python environment
2. Clone the repository
3. Start backend using:
  python3 -m smart_home.backend_app
4. Configure Chromium in kiosk mode for the dashboard
5. Optional improvements:
  - systemd service for auto-start
  - kiosk browser auto-start
  - touchscreen UI optimization

Possible Extensions
===================
- Logging and historic data visualization
- MQTT or InfluxDB integration
- Home Assistant integration
- Docker deployment
- Enhanced mobile UI
- Predictive charging based on PV forecasts
