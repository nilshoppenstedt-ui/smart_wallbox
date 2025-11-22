# %%

import asyncio
import aiohttp
from renault_api.renault_client import RenaultClient
import os

EMAIL = os.getenv("MYRENAULT_EMAIL")
PASSWORD = os.getenv("MYRENAULT_PASSWORD")

async def main():
    async with aiohttp.ClientSession() as websession:
        client = RenaultClient(websession=websession, locale="de_DE")
        await client.session.login(EMAIL, PASSWORD)

        person = await client.get_person()

        kamereon_account = next(
            acc for acc in person.accounts
            if acc.accountType == "MYRENAULT"
        )
        account = await client.get_api_account(kamereon_account.accountId)

        vehicles = await account.get_vehicles()
        vin = vehicles.vehicleLinks[0].vin
        print("Verwendete VIN:", vin)

        vehicle = await account.get_api_vehicle(vin)
        battery = await vehicle.get_battery_status()
        print("Battery status raw:", battery)

        # <<< HIER die Attribute verwenden >>>
        soc = battery.batteryLevel
        autonomy = battery.batteryAutonomy
        plug_status = battery.plugStatus
        charging_status = battery.chargingStatus

        print(f"SoC: {soc} %")
        print(f"Reichweite: {autonomy} km")
        print(f"Plug-Status: {plug_status}")
        print(f"Ladestatus: {charging_status}")

if __name__ == "__main__":
    asyncio.run(main())