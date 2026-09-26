"""
Application entry point.
Initializes database, seeds demo data, and starts the Telegram bot.
"""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.database import close_db, create_all_tables, init_db
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

    # Create tables (development only — use Alembic migrations in production)
    if settings.is_development:
        logger.info("Creating database tables (dev mode)...")
        await create_all_tables()

    # Seed demo data
    logger.info("Checking for seed data...")
    from app.seed import seed_demo_data
    await seed_demo_data()

    # Start the bot
    logger.info("Starting Telegram bot...")
    from app.bot import run_bot
    try:
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
