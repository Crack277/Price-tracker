from typing import Annotated

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram3_di import Depends

from app.di.providers import get_tracking_service
from app.keyboards.reply import main_menu_kb
from app.services.tracking_service import TrackingServiceImpl

router = Router(name="delete_item")


class DeleteItemStates(StatesGroup):
    waiting_for_number = State()


@router.callback_query(F.data == "delete:start")
async def ask_item_number(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DeleteItemStates.waiting_for_number)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Напиши номер товара, который хочешь удалить (например: 1)."
        )
    await callback.answer()


@router.message(Command("delete"))
async def cmd_delete(message: Message, state: FSMContext) -> None:
    await state.set_state(DeleteItemStates.waiting_for_number)
    await message.answer("Напиши номер товара, который хочешь удалить.")


@router.message(DeleteItemStates.waiting_for_number)
async def process_delete(
    message: Message,
    state: FSMContext,
    tracking_service: Annotated[TrackingServiceImpl, Depends(get_tracking_service)],
) -> None:
    if message.from_user is None or message.text is None:
        return

    try:
        number = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно число. Например: 1")
        return

    if number < 1:
        await message.answer("Номер должен быть положительным.")
        return

    items = await tracking_service.list_items(message.from_user.id)
    if not items:
        await state.clear()
        await message.answer("У тебя нет товаров для удаления.", reply_markup=main_menu_kb())
        return

    if number > len(items):
        await message.answer(
            f"У тебя только {len(items)} товар(ов). Введи номер от 1 до {len(items)}."
        )
        return

    item = items[number - 1]
    deleted = await tracking_service.delete_item(message.from_user.id, item.id)
    await state.clear()

    if deleted:
        await message.answer(
            f"✅ Товар «{item.title}» удалён.",
            reply_markup=main_menu_kb(),
        )
    else:
        await message.answer(
            "Не удалось удалить товар. Попробуй ещё раз.",
            reply_markup=main_menu_kb(),
        )