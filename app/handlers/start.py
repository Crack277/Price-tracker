from typing import Annotated

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram3_di import Depends

from app.database.repositories.user_repo import UserRepository
from app.di.providers import get_user_repository
from app.keyboards.reply import main_menu_kb

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> None:
    if message.from_user is None:
        return

    await user_repo.get_or_create(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )

    await message.answer(
        "Привет! 👋\n"
        "Я — PriceTracker, бот для отслеживания цен на маркетплейсах.\n\n"
        "С моей помощью ты можешь:\n"
        "• Отслеживать изменение цен\n"
        "• Устанавливать желаемую стоимость товара\n"
        "• Получать уведомления о снижении цены\n\n"
        "Нажми «Добавить», чтобы начать.",
        reply_markup=main_menu_kb(),
    )