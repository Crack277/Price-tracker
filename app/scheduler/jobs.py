import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.database.repositories.item_repo import ItemRepository
from app.database.repositories.user_repo import UserRepository
from app.database.session import async_session_factory
from app.services.notification_service import NotificationServiceImpl
from app.services.tracking_service import TrackingServiceImpl

logger = logging.getLogger(__name__)


async def check_all_prices(bot_token: str) -> None:
    """Фоновая задача: проверяет цены и уведомляет при достижении целевой.

    В Redis-хранение APScheduler задача попадает через pickle, поэтому в неё
    можно передавать только сериализуемые значения — токен, а не объект Bot.
    """
    logger.info("Starting background price check...")

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await _check_all_prices(bot)
    finally:
        await bot.session.close()


async def _check_all_prices(bot: Bot) -> None:
    async with async_session_factory() as session:
        user_repo = UserRepository(session)
        item_repo = ItemRepository(session)
        tracking_service = TrackingServiceImpl(session, user_repo, item_repo)
        notification_service = NotificationServiceImpl(bot)

        items = await item_repo.get_all_active()
        logger.info(f"Found {len(items)} items to check")

        for item in items:
            try:
                old_price = item.current_price
                changed = await tracking_service.refresh_price(item)

                if changed:
                    logger.info(
                        f"Price changed for item '{item.title}' (ID {item.id}): "
                        f"old {old_price} -> new {item.current_price} "
                        f"(target: {item.target_price})"
                    )

                    # Если новая цена достигла целевой — отправляем уведомление
                    if item.current_price <= item.target_price:
                        user = await user_repo.get_by_id(item.user_id)
                        if user is not None:
                            await notification_service.notify_price_drop(
                                user=user,
                                item=item,
                                old_price=old_price,
                                new_price=item.current_price,
                            )
                            logger.info(f"Notification sent to user {user.telegram_id}")
            except Exception as exc:
                logger.error(f"Error checking price for item {item.id}: {exc}")

    logger.info("Background price check finished.")