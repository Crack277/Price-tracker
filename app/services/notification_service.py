from decimal import Decimal
from typing import Protocol

from aiogram import Bot

from app.database.models import TrackedItem, User


class NotificationService(Protocol):
    """Контракт сервиса уведомлений."""

    async def notify_price_drop(
        self,
        user: User,
        item: TrackedItem,
        old_price: Decimal,
        new_price: Decimal,
    ) -> None:
        ...


class NotificationServiceImpl:
    """Отправляет уведомления в Telegram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def notify_price_drop(
        self,
        user: User,
        item: TrackedItem,
        old_price: Decimal,
        new_price: Decimal,
    ) -> None:
        text = (
            f"🎉 <b>Цена снизилась!</b>\n\n"
            f"Товар: {item.title}\n"
            f"Было: <s>{old_price:.2f} ₽</s>\n"
            f"Стало: <b>{new_price:.2f} ₽</b>\n"
            f"Твоя целевая цена: {item.target_price:.2f} ₽\n\n"
            f"<a href='{item.url}'>Открыть товар</a>"
        )
        await self._bot.send_message(
            chat_id=user.telegram_id,
            text=text,
            disable_web_page_preview=True,
        )