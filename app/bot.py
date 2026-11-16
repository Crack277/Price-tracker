import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram3_di import setup_di

from app.config import get_settings
from app.database.session import engine
from app.handlers import add_item, delete_item, help, list_items, start
from app.scheduler.jobs import check_all_prices
from app.scheduler.scheduler import scheduler


def create_bot() -> Bot:
    settings = get_settings()
    session = AiohttpSession(proxy=settings.proxy_url_or_none)
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


def create_dispatcher() -> Dispatcher:
    settings = get_settings()
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    dp.include_router(start.router)
    dp.include_router(add_item.router)
    dp.include_router(list_items.router)
    dp.include_router(delete_item.router)
    dp.include_router(help.router)

    setup_di(dp)

    return dp


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = create_bot()
    dp = create_dispatcher()

    scheduler.add_job(
        check_all_prices,
        trigger="interval",
        minutes=settings.price_check_interval_minutes,
        args=[settings.bot_token.get_secret_value()],
        id="check_all_prices",
        replace_existing=True,
    )
    scheduler.start()
    logging.info(
        f"Scheduler started. Price check interval: {settings.price_check_interval_minutes} min."
    )

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()
        logging.info("Bot stopped, scheduler shut down.")


if __name__ == "__main__":
    asyncio.run(main())