from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.services.timezone import RUSSIAN_TIMEZONES


def timezone_keyboard(prefix: str = "tz") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, tz in RUSSIAN_TIMEZONES:
        builder.button(text=label, callback_data=f"{prefix}:{tz}")
    builder.adjust(1)
    return builder.as_markup()


def restart_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Подключить другой календарь", callback_data="start:restart")
    return builder.as_markup()


def update_file_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📎 Загрузить новый .ics файл", callback_data="sync:upload_file")
    return builder.as_markup()


def refresh_today_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="refresh:today")
    return builder.as_markup()


REMINDER_OPTIONS = [
    (15,  "15 минут"),
    (30,  "30 минут"),
    (60,  "1 час"),
    (120, "2 часа"),
    (180, "3 часа"),
]


def reminder_offsets_keyboard(selected: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for minutes, label in REMINDER_OPTIONS:
        check = "✅" if minutes in selected else "◻️"
        builder.button(text=f"{check} {label}", callback_data=f"rem_toggle:{minutes}")
    builder.button(text="💾 Сохранить", callback_data="rem_save")
    builder.adjust(1)
    return builder.as_markup()


def week_navigation_keyboard(offset: int, total_days: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if offset > 0:
        builder.button(text="⬅️ Пред. день", callback_data=f"week_nav:{offset - 1}")
    if offset < total_days - 1:
        builder.button(text="След. день ➡️", callback_data=f"week_nav:{offset + 1}")
    builder.button(text="🔄 Обновить", callback_data=f"week_nav:{offset}")
    builder.adjust(2)
    return builder.as_markup()
