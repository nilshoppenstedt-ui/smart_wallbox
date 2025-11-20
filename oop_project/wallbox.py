from typing import Optional

from pymodbus.client import ModbusTcpClient


class WallboxError(Exception):
    """Error while communicating with the wallbox."""
    pass


class Wallbox:
    """
    Encapsulates access to the go-e wallbox via Modbus TCP.

    Parameters
    ----------
    ip : str
        IP address of the wallbox.
    port : int
        Modbus TCP port (default 502).
    device_id : int
        Modbus unit id / device id (usually 1 for go-e).
    timeout : float
        Modbus TCP timeout in seconds.
    """

    def __init__(self, ip: str, port: int = 502, device_id: int = 1, timeout: float = 3.0):
        self.ip = ip
        self.port = port
        self.device_id = device_id
        self.timeout = timeout

    def _read_input_registers(self, address: int, count: int) -> list[int]:
        """
        Low-level helper to read input registers.
        Raises WallboxError on any communication problem.
        """
        client = ModbusTcpClient(self.ip, port=self.port, timeout=self.timeout)
        client.connect()

        try:
            rr = client.read_input_registers(address, count=count, device_id=self.device_id)
        finally:
            client.close()

        if rr.isError():
            raise WallboxError(f"Modbus error reading address {address}: {rr}")

        return rr.registers

    # ------------------------------------------------------------------
    #  Current charging power
    # ------------------------------------------------------------------
    def read_power_kw(self) -> float:
        """
        Read total charging power from POWER_TOTAL (wire address 120, 2 registers).
        Return power in kW.

        Scaling: value in 0.01 W → divide by 100000 to get kW.
        Unrealistic values (<0 or >11 kW) are treated as 0.0 kW.
        """
        regs = self._read_input_registers(address=120, count=2)
        raw = (regs[0] << 16) | regs[1]
        wb_kw = raw / 100000.0

        # Simple plausibility filter
        if wb_kw < 0 or wb_kw > 11.0:
            # For your setup, more than ~11 kW is not realistic
            return 0.0

        return wb_kw

    # ------------------------------------------------------------------
    #  Car connection state
    # ------------------------------------------------------------------
    def read_car_state_raw(self) -> int:
        """
        Read CAR_STATE (wire address 100, 1 register).

        Returns the raw uint16 value:
        0: unknown/defect
        1: station ready, no vehicle
        2: vehicle charging
        3: waiting for vehicle
        4: charging finished, vehicle still connected
        """
        regs = self._read_input_registers(address=100, count=1)
        return int(regs[0])

    def is_vehicle_connected(self) -> bool:
        """
        Returns True if a car is considered to be connected.

        We treat the following states as "vehicle present":
        - 2: vehicle charging
        - 3: waiting for vehicle
        - 4: charging finished, vehicle still connected
        """
        state = self.read_car_state_raw()
        return state in (2, 3, 4)
