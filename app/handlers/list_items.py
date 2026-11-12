from typing import Annotated

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram3_di import Depends

from app.di.providers import get_tracking_service
from app.keyboards.inline import delete_prompt_kb
from app.services.tracking_service import TrackingServiceImpl

router = Router(name="list_items")


@router.message(Command("list"))
@router.message(F.text == "📋 Мои товары")
async def cmd_list(
    message: Message,
    tracking_service: Annotated[TrackingServiceImpl, Depends(get_tracking_service)],
) -> None:
    if message.from_user is None:
        return

    items = await tracking_service.list_items(message.from_user.id)
    if not items:
        await message.answer(
            "У тебя пока нет отслеживаемых товаров.\n"
            "Нажми «Добавить», чтобы добавить первый."
        )
        return

    lines = ["📋 <b>Твои отслеживаемые товары:</b>\n"]
    for idx, item in enumerate(items, start=1):
        marker = ""
        if item.current_price <= item.target_price:
            marker = " 🎯"
        lines.append(
            f"{idx}. <b>{item.title}</b>\n"
            f"   Текущая: {item.current_price:.2f} ₽"
            f" | Целевая: {item.target_price:.2f} ₽{marker}"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=delete_prompt_kb(),
    )