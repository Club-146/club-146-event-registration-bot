from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from botspot.core.bot_manager import BotManager
from calmlib.logging import setup_logger
from dotenv import load_dotenv
from loguru import logger

from src.app import App
from src.user_interactions import setup_dispatcher as setup_user_interactions

from .router import router as main_router
from .routers.events import events_router
from .routers.feedback import router as feedback_router
from .routers.payment import router as payment_router
from .routers.stats import router as admin_router

# Initialize bot and dispatcher


# @heartbeat_for_sync(src.name)
def main(debug=False) -> None:
    # Load environment variables
    load_dotenv(Path(__file__).parent.parent / ".env")

    dp = Dispatcher()
    dp.include_router(events_router)
    dp.include_router(admin_router)
    dp.include_router(payment_router)
    dp.include_router(feedback_router)
    dp.include_router(main_router)

    setup_logger(logger, level="DEBUG" if debug else "INFO")  # type: ignore[arg-type]

    app = App()
    dp["app"] = app
    # Initialize Bot instance with a default parse mode
    bot = Bot(
        token=app.settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Initialize bot manager
    bm = BotManager(bot=bot)
    bm.settings.ask_user.enabled = False

    async def on_startup() -> None:
        await app.startup()
        try:
            from src.reminder_scheduler import start_reminder_scheduler

            start_reminder_scheduler(app, bot)
        except Exception:
            logger.exception("Failed to start payment reminder scheduler")

    dp.startup.register(on_startup)

    # Setup dispatcher with our components
    bm.setup_dispatcher(dp)
    setup_user_interactions(dp)

    # Start polling
    dp.run_polling(bot)


if __name__ == "__main__":
    main()
