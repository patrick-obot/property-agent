import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "6"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "property_agent.db")
SHEROOT_URL = "https://www.sheroot.co.za/fixed-property-sales.html"

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set in environment or .env file")
