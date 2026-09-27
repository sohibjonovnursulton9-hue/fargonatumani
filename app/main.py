"""
Application entry point.
Initializes database, seeds demo data, and starts the Telegram bot.
"""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.database import close_db, init_db
from app.logging_config import setup_logging, get_logger


async def main() -> None:
    """Main application startup."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)

    logger.info("Starting FTMT — Farg'ona Tuman Murojaatlar Tizimi")
    logger.info("Environment", app_env=settings.app_env)

    # Initialize database
    logger.info("Initializing database...")
    await init_db(settings.database_url)

    # Demo catalogs are provisional and must be opted into explicitly in development.
    if settings.seed_demo_data and settings.is_development:
        logger.info("Seeding explicitly enabled development demo data...")
        from app.seed import seed_demo_data
        await seed_demo_data()
    elif settings.seed_demo_data:
        logger.error("Demo seeding is disabled outside development environments")

    try:
        logger.info("Starting Telegram bot...")
        from app.bot import run_bot
        await run_bot()
    finally:
        await close_db()


def run() -> None:
    """Entry point for running from command line."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.")
        sys.exit(0)


if __name__ == "__main__":
    run()
