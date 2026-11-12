from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="help")


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Я помогаю следить за ценами на товары.\n\n"
        "<b>Как начать:</b>\n"
        "1. Отправь мне ссылку на товар Ozon или Wildberries\n"
        "2. Подтверди найденный товар\n"
        "3. Укажи желаемую цену\n"
        "4. Получай уведомления о снижении\n\n"
        "<b>Команды:</b>\n"
        "/add — добавить товар\n"
        "/list — показать все товары\n"
        "/delete — удалить товар\n"
        "/help — эта справка"
    )