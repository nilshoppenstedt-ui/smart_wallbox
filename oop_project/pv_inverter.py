import struct
from typing import Optional

from pymodbus.client import ModbusTcpClient


class PVInverterError(Exception):
    """Error while reading data from the PV inverter."""
    pass


class PVInverter:
    """
    Encapsulates Modbus access to the PV inverter (e.g. Kostal).

    Parameters
    ----------
    ip : str
        IP address of the inverter.
    port : int
        Modbus TCP port (default 1502 for your Kostal setup).
    unit_id : int
        Modbus slave / device id (71 in your existing script).
    timeout : float
        Modbus TCP timeout in seconds.
    """

    def __init__(self, ip: str, port: int = 1502, unit_id: int = 71, timeout: float = 3.0):
        self.ip = ip
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout

    def _read_registers(self, address: int, count: int) -> list[int]:
        """
        Low-level helper to read holding registers.
        Raises PVInverterError on any communication problem.
        """
        client = ModbusTcpClient(self.ip, port=self.port, timeout=self.timeout)
        client.connect()

        try:
            rr = client.read_holding_registers(address, count=count, device_id=self.unit_id)
        finally:
            client.close()

        if rr.isError():
            raise PVInverterError(f"Modbus error reading address {address}: {rr}")

        return rr.registers

    def read_total_power_kw(self) -> float:
        """
        Read total AC power from inverter in kW.

        This mirrors your previous logic:
        - 'Total power' at register 172, 2 registers, 32-bit float
        - old code used BinaryPayloadDecoder with byteorder=BIG, wordorder=LITTLE
        - here we reproduce that using struct.
        """
        regs = self._read_registers(address=172, count=2)

        # wordorder = LITTLE, byteorder = BIG:
        # swap word order, keep big-endian within each word
        raw_bytes = struct.pack('>HH', regs[1], regs[0])
        value_kw = struct.unpack('>f', raw_bytes)[0] / 1000.0

        return value_kw
