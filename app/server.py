"""
Combined entry point for Railway / single-process deployments.
Runs the Telegram bot (polling) AND the admin panel (uvicorn) in one process.
Railway exposes $PORT for the web service; the bot runs alongside it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from app.config import get_settings
from app.database import init_db, create_all_tables, close_db
from app.logging_config import setup_logging, get_logger


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)

    logger.info("Starting FTMT combined server (bot + admin)")
    logger.info("Environment", app_env=settings.app_env)

    # Initialize database
    await init_db(settings.database_url)

    if settings.is_development:
        await create_all_tables()

    if settings.seed_demo_data and settings.is_development:
        from app.seed import seed_demo_data
        await seed_demo_data()

    # Start Telegram bot in background
    from app.bot import run_bot
    bot_task = asyncio.create_task(run_bot())
    logger.info("Telegram bot started in background")

    # Start admin panel
    port = int(os.environ.get("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    config = uvicorn.Config(
        "app.admin.app:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    logger.info("Admin panel starting", port=port)

    server_task = asyncio.create_task(server.serve())
    try:
        completed, _ = await asyncio.wait(
            {server_task, bot_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        if bot_task in completed:
            failure = bot_task.exception()
            server.should_exit = True
            await server_task
            raise RuntimeError("Telegram bot stopped while admin server was running") from failure
        await server_task
        if not server.started or not server.should_exit:
            raise RuntimeError("Admin server stopped unexpectedly")
    finally:
        bot_task.cancel()
        await asyncio.gather(bot_task, return_exceptions=True)
        await close_db()


if __name__ == "__main__":
    try:
        # Railway's startCommand overrides Docker CMD. Run migrations from the
        # actual entry point so either launch path prepares the database.
        if not get_settings().is_development:
            command.upgrade(
                Config(str(Path(__file__).resolve().parent.parent / "alembic.ini")),
                "head",
            )
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
