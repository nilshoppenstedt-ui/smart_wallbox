#%%

import os
import asyncio
import aiohttp
from datetime import datetime
from renault_api.renault_client import RenaultClient

EMAIL = os.getenv("MYRENAULT_EMAIL")
PASSWORD = os.getenv("MYRENAULT_PASSWORD")
LOCALE = "de_DE"

if not EMAIL or not PASSWORD:
    raise RuntimeError("Bitte MYRENAULT_EMAIL und MYRENAULT_PASSWORD setzen.")


async def main():
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as websession:
        client = RenaultClient(websession=websession, locale=LOCALE)
        await client.session.login(EMAIL, PASSWORD)

        print("\n=== Logged in ===")

        person = await client.get_person()
        account_link = next(acc for acc in person.accounts if acc.accountType == "MYRENAULT")
        account = await client.get_api_account(account_link.accountId)

        vehicles = await account.get_vehicles()
        vin = vehicles.vehicleLinks[0].vin

        print(f"Using VIN: {vin}")

        vehicle = await account.get_api_vehicle(vin)
        battery = await vehicle.get_battery_status()

        print("\n=== Battery raw object ===")
        print(battery)

        print("\n=== battery.__dict__ ===")
        print(getattr(battery, "__dict__", None))

        # Falls ein raw/json verfügbar ist
        raw = getattr(battery, "raw", None)
        if raw:
            print("\n=== battery.raw ===")
            print(raw)

        # Zusätzlich alle Attribute schön ausgeben
        print("\n=== All attributes of battery ===")
        for attr in dir(battery):
            if not attr.startswith("_"):
                try:
                    value = getattr(battery, attr)
                except Exception:
                    value = "<error>"
                print(f"{attr}: {value}")


if __name__ == "__main__":
    asyncio.run(main())

# %%
