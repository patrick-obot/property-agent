"""Print full event data from the calendar API."""
import asyncio
import json
import sys
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")


async def inspect():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        calendar_data = None

        async def on_response(resp):
            nonlocal calendar_data
            if "inffuse.eventscalendar.co/js/v0.1/calendar/data" in resp.url:
                try:
                    calendar_data = await resp.json()
                except Exception as e:
                    print(f"Failed to parse JSON: {e}")

        page.on("response", on_response)

        await page.goto(
            "https://www.sheroot.co.za/fixed-property-sales.html",
            timeout=60000,
            wait_until="networkidle",
        )
        await asyncio.sleep(5)

        if calendar_data:
            events = calendar_data.get("project", {}).get("data", {}).get("events", [])
            print(f"Found {len(events)} event(s):\n")
            print(json.dumps(events, indent=2, ensure_ascii=False))
        else:
            print("No calendar data captured.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(inspect())
