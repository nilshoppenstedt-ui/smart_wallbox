# smart_home/car_client.py

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiohttp
from renault_api.renault_client import RenaultClient


class CarClientError(Exception):
    """Error while reading data from the car (Renault API)."""
    pass


@dataclass
class CarStatus:
    """Container for the car battery status."""
    soc: Optional[int]
    autonomy_km: Optional[int]
    plug_status: Optional[int]
    charging_status: Optional[float]
    timestamp: datetime


class CarClient:
    """
    Client for fetching car battery status from Renault MyRenault API.

    The client uses a blocking wrapper around an async API call. It is designed
    to be called infrequently (e.g. every few minutes) because the request may
    take several seconds and the remote service is not always reliable.
    """

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        locale: str = "de_DE",
        timeout_sec: float = 15.0,
    ) -> None:
        # Read from environment if not explicitly provided
        self.email = email or os.getenv("MYRENAULT_EMAIL")
        self.password = password or os.getenv("MYRENAULT_PASSWORD")
        self.locale = locale
        self.timeout_sec = timeout_sec

        if not self.email or not self.password:
            raise CarClientError(
                "Missing MyRenault credentials: "
                "set MYRENAULT_EMAIL / MYRENAULT_PASSWORD or pass email/password."
            )

    async def _fetch_status_async(self) -> CarStatus:
        """Internal async implementation talking to Renault API."""
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)

        async with aiohttp.ClientSession(timeout=timeout) as websession:
            client = RenaultClient(websession=websession, locale=self.locale)
            await client.session.login(self.email, self.password)

            person = await client.get_person()
            kamereon_account = next(
                acc for acc in person.accounts if acc.accountType == "MYRENAULT"
            )
            account = await client.get_api_account(kamereon_account.accountId)

            vehicles = await account.get_vehicles()
            vin = vehicles.vehicleLinks[0].vin

            vehicle = await account.get_api_vehicle(vin)
            battery = await vehicle.get_battery_status()

            now = datetime.now()
            return CarStatus(
                soc=battery.batteryLevel,
                autonomy_km=battery.batteryAutonomy,
                plug_status=battery.plugStatus,
                charging_status=battery.chargingStatus,
                timestamp=now,
            )

    def read_status(self) -> CarStatus:
        """
        Public blocking API.

        Returns:
            CarStatus object with battery information.

        Raises:
            CarClientError on any error (network, auth, API issues, etc.).
        """
        try:
            return asyncio.run(self._fetch_status_async())
        except Exception as e:
            raise CarClientError(f"Failed to fetch car status: {e}") from e
