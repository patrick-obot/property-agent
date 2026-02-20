"""
Entry point for the Property Agent bot.

Starts the Telegram bot and the APScheduler in the same asyncio event loop.
On startup it runs an immediate scrape so users don't have to wait for the
first scheduled interval.
"""

import asyncio
import logging
import sys

from database import db as database
from bot.telegram_bot import build_application
from scheduler.job_scheduler import build_scheduler, scrape_and_notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def post_init(application):
    """Called after the Telegram application is initialised."""
    bot = application.bot
    scheduler = build_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started. Scraping every Thursday and Friday at 08:00.")
    # Run an immediate scrape on startup
    logger.info("Running initial scrape on startup...")
    asyncio.create_task(scrape_and_notify(bot))


def main():
    # Initialise database schema
    database.init_db()
    logger.info("Database initialised.")

    # Build and configure Telegram application
    application = build_application()
    application.post_init = post_init

    logger.info("Starting Property Agent bot...")
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
