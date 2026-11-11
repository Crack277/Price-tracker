from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню с основными действиями."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить")],
            [KeyboardButton(text="📋 Мои товары")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )