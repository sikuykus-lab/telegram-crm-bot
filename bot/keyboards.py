"""Reply keyboards for CRM bot."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_EVENTS = "📅 Мои мероприятия"
BTN_BOOK = "📝 Заявка на мероприятие"
BTN_SITE = "🌐 Наш сайт"
BTN_TARIFFS = "💰 Тарифы"
BTN_WRITE = "📩 Написать нам"
BTN_ADMIN = "🛠 Админ-панель"

MENU_TEXTS = frozenset({BTN_EVENTS, BTN_BOOK, BTN_SITE, BTN_TARIFFS, BTN_WRITE, BTN_ADMIN})


def main_keyboard(user_id: int, admin_ids: frozenset[int]) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_EVENTS), KeyboardButton(text=BTN_BOOK)],
        [KeyboardButton(text=BTN_SITE), KeyboardButton(text=BTN_TARIFFS)],
        [KeyboardButton(text=BTN_WRITE)],
    ]
    if user_id in admin_ids:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
