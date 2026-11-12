import logging
from decimal import Decimal, InvalidOperation
from typing import Annotated

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram3_di import Depends

from app.database.models import Marketplace
from app.di.providers import get_tracking_service
from app.keyboards.inline import cancel_kb, target_price_kb
from app.keyboards.reply import main_menu_kb
from app.parsers.registry import detect_marketplace, get_parser_by_url
from app.services.tracking_service import TrackingServiceImpl

logger = logging.getLogger(__name__)
router = Router(name="add_item")


class AddItemStates(StatesGroup):
    waiting_for_url = State()
    waiting_for_target_price = State()


@router.message(Command("add"))
@router.message(F.text == "➕ Добавить")
async def start_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddItemStates.waiting_for_url)
    await message.answer(
        "Отправь ссылку на товар с Ozon или Wildberries, цену которого хочешь отслеживать.",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(AddItemStates.waiting_for_url))
async def process_url(message: Message, state: FSMContext) -> None:
    if message.text is None:
        await message.answer("Пожалуйста, отправь ссылку текстом.")
        return

    url = message.text.strip()
    marketplace = detect_marketplace(url)
    if marketplace is None:
        await message.answer(
            "Не удалось распознать маркетплейс. Поддерживаются Ozon и Wildberries."
        )
        return

    try:
        parser = get_parser_by_url(url)
        parsed = await parser.parse(url)
    except Exception as exc:
        logger.error(f"Ошибка парсинга {url}: {exc}")
        await message.answer(
            "❌ Не удалось получить информацию о товаре.\n"
            "Возможно, ссылка неверна или маркетплейс временно блокирует запросы.",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(
        url=url,
        title=parsed.title,
        marketplace=parsed.marketplace.value,
        current_price=str(parsed.price),
    )
    await state.set_state(AddItemStates.waiting_for_target_price)

    await message.answer(
        f"Товар закреплён!\n"
        f"<b>{parsed.title}</b>\n"
        f"Текущая цена: <b>{parsed.price:.2f} ₽</b>\n\n"
        f"Какую цену ты ожидаешь? Отправь число или выбери «Любое снижение».",
        reply_markup=target_price_kb(),
    )


@router.callback_query(StateFilter(AddItemStates.waiting_for_target_price), F.data == "add:any_drop")
async def process_any_drop(
    callback: CallbackQuery,
    state: FSMContext,
    tracking_service: Annotated[TrackingServiceImpl, Depends(get_tracking_service)],
) -> None:
    data = await state.get_data()
    current_price = Decimal(data["current_price"])

    # ИСПРАВЛЕНИЕ: передаем callback.from_user.id и callback.message
    await _save_item(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        message=callback.message,
        data=data,
        target_price=current_price,
        tracking_service=tracking_service
    )
    await state.clear()

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()


@router.callback_query(StateFilter(AddItemStates.waiting_for_url, AddItemStates.waiting_for_target_price), F.data == "add:cancel")
async def cancel_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Добавление отменено.", reply_markup=main_menu_kb())
    await callback.answer()


@router.message(StateFilter(AddItemStates.waiting_for_target_price))
async def process_target_price(
    message: Message,
    state: FSMContext,
    tracking_service: Annotated[TrackingServiceImpl, Depends(get_tracking_service)],
) -> None:
    if message.text is None:
        await message.answer("Отправь цену числом.")
        return

    try:
        target_price = Decimal(message.text.strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        await message.answer("Не понял цену. Отправь число, например: 1999")
        return

    if target_price <= 0:
        await message.answer("Цена должна быть больше нуля.")
        return

    data = await state.get_data()
    
    # ИСПРАВЛЕНИЕ: передаем message.from_user.id и message
    await _save_item(
        user_id=message.from_user.id,
        username=message.from_user.username,
        message=message,
        data=data,
        target_price=target_price,
        tracking_service=tracking_service
    )
    await state.clear()


async def _save_item(
    user_id: int,
    username: str | None,
    message: Message,
    data: dict,
    target_price: Decimal,
    tracking_service: TrackingServiceImpl,
) -> None:
    if message is None:
        return
    try:
        await tracking_service.add_item(
            telegram_id=user_id,
            username=username,
            url=data["url"],
            marketplace=Marketplace(data["marketplace"]),
            title=data["title"],
            current_price=Decimal(data["current_price"]),
            target_price=target_price,
        )
    except Exception as exc:
        logger.error(f"Ошибка сохранения товара: {exc}")
        await message.answer("Не удалось сохранить товар. Попробуйте позже.")
        return

    await message.answer(
        f"✅ Целевая цена закреплена: <b>{target_price:.2f} ₽</b>.\n"
        f"Я уведомлю тебя, когда цена опустится до неё или ниже.",
        reply_markup=main_menu_kb(),
    )