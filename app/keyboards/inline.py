from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def target_price_kb() -> InlineKeyboardMarkup:
    """Клавиатура на шаге ввода целевой цены."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Любое снижение", callback_data="add:any_drop")],
            [InlineKeyboardButton(text="Отмена", callback_data="add:cancel")],
        ]
    )


def delete_prompt_kb() -> InlineKeyboardMarkup:
    """Клавиатура на экране «Мои товары»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить товар", callback_data="delete:start")],
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="add:cancel")],
        ]
    )