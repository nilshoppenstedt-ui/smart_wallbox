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
        Modbus slave / device id (e.g. 71).
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

        The Kostal register layout:
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

    def read_string_powers_kw(self) -> dict[str, float]:
        """Read DC string powers (three inputs) from inverter in kW.

        Returns
        -------
        dict[str, float]
            Dictionary with keys ``pv1_kw``, ``pv2_kw`` and ``pv3_kw`` giving
            the instantaneous power of the three DC inputs in kW.
        """
        # Registers for DC1, DC2, DC3 power in Watt.
        # Each value is encoded as a 32-bit float over 2 registers, using the
        # same byte/word order as in :meth:`read_total_power_kw`.
        addresses = (260, 270, 280)
        powers_kw: list[float] = []

        for addr in addresses:
            regs = self._read_registers(address=addr, count=2)

            # wordorder = LITTLE, byteorder = BIG:
            # swap word order, keep big-endian within each word
            raw_bytes = struct.pack('>HH', regs[1], regs[0])
            value_w = struct.unpack('>f', raw_bytes)[0]
            powers_kw.append(value_w / 1000.0)

        return {
            "pv1_kw": powers_kw[0],
            "pv2_kw": powers_kw[1],
            "pv3_kw": powers_kw[2],
        }
