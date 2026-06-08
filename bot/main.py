"""
CRM-бот MetaFora VR (aiogram 3.x): приватный чат ↔ топики супергруппы.
Админ-панель — кнопка «🛠 Админ-панель» (только для ADMIN_IDS).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
)
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from dotenv import load_dotenv

from admin import admin_router, is_admin, register_admin_handlers
from db import (
    db_get_topic_id,
    db_get_user_id_by_topic,
    db_save_user_topic,
    format_user_events_text,
    get_setting,
    init_db,
)
from keyboards import BTN_ADMIN, MENU_TEXTS, main_keyboard
from requests import register_request_handlers, requests_router
from scheduler import job_expire_events, start_scheduler

load_dotenv(Path(__file__).resolve().parent / ".env")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GROUP_ID_RAW = int(os.environ.get("GROUP_ID", "0"))
GROUP_ID = GROUP_ID_RAW
TZ = os.environ.get("TZ", "Europe/Moscow").strip()

DEFAULT_SITE_URL = os.environ.get("SITE_URL", "http://YOUR_SERVER_HOST/").strip()
DEFAULT_TARIFFS_TEXT = os.environ.get(
    "TARIFFS_TEXT",
    """💰 <b>Пакеты услуг</b>

<b>SOLO</b> — 15 000 ₽/час
• Одна игровая VR зона
• Доставка/настройка
• Любые игры на ваш выбор

<b>DUO</b> — 28 000 ₽/час
• Две игровые VR зоны
• Доставка/настройка
• Любые совместные игры на ваш выбор
• <i>Выгода 7%</i>

<b>PARTY</b> — 36 000 ₽/час
• Четыре игровые VR зоны
• Доставка/настройка
• Любые совместные игры на ваш выбор
• <i>Выгода 20%</i>

Напишите нам в чат — подберём пакет.""",
).strip()

ADMIN_IDS = frozenset(
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("crm_bot")

user_router = Router(name="user")
group_router = Router(name="group")

_flood_lock = asyncio.Lock()
_flood: dict[tuple[int, str], deque[float]] = defaultdict(deque)
_last_flood_warn: dict[int, float] = {}

RL_START = (5, 120.0)
RL_MENU = (24, 60.0)
RL_PRIVATE_MSG = (14, 60.0)
RL_GROUP_TO_USER = (22, 60.0)


def kb_for(user_id: int):
    return main_keyboard(user_id, ADMIN_IDS)


async def get_site_url() -> str:
    db_val = await get_setting("site_url")
    return db_val if db_val else DEFAULT_SITE_URL


async def get_tariffs_text() -> str:
    db_val = await get_setting("tariffs_text")
    return db_val if db_val else DEFAULT_TARIFFS_TEXT


async def rate_allow(user_id: int, bucket: str, max_n: int, window_sec: float) -> bool:
    key = (user_id, bucket)
    now = time.monotonic()
    async with _flood_lock:
        dq = _flood[key]
        while dq and dq[0] < now - window_sec:
            dq.popleft()
        if len(dq) >= max_n:
            return False
        dq.append(now)
    return True


async def maybe_warn_flood(message: Message, uid: int) -> None:
    now = time.monotonic()
    prev = _last_flood_warn.get(uid, 0.0)
    if now - prev < 12.0:
        return
    _last_flood_warn[uid] = now
    try:
        await message.answer("Слишком много сообщений подряд. Подождите минуту и попробуйте снова.")
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


async def ensure_forum_topic(bot: Bot, user_id: int, display_name: str) -> int:
    existing = await db_get_topic_id(user_id)
    if existing is not None:
        await db_save_user_topic(user_id, existing, display_name)
        return existing

    topic_name = display_name[:120] if display_name else str(user_id)
    if len(topic_name) < 1:
        topic_name = str(user_id)

    global GROUP_ID
    gid = GROUP_ID
    try:
        forum = await bot.create_forum_topic(chat_id=gid, name=topic_name)
    except TelegramMigrateToChat as e:
        gid = e.migrate_to_chat_id
        GROUP_ID = gid
        log.warning("Чат мигрировал в супергруппу: обновите GROUP_ID в .env на %s", gid)
        forum = await bot.create_forum_topic(chat_id=gid, name=topic_name)
    topic_id = forum.message_thread_id
    await db_save_user_topic(user_id, topic_id, display_name)

    try:
        await bot.send_message(
            chat_id=gid,
            message_thread_id=topic_id,
            text=(
                f"🆕 <b>Новый диалог</b>\n"
                f"Пользователь: {display_name}\n"
                f"<code>user_id={user_id}</code>"
            ),
            parse_mode="HTML",
        )
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        log.warning("Не удалось отправить уведомление в топик: %s", e)

    return topic_id


async def send_user_message_to_topic(bot: Bot, message: Message, topic_id: int) -> None:
    await bot.copy_message(
        chat_id=GROUP_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        message_thread_id=topic_id,
    )


async def safe_copy_to_user(bot: Bot, user_id: int, from_chat_id: int, message_id: int) -> None:
    try:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
    except TelegramForbiddenError:
        log.info("Пользователь %s заблокировал бота — ответ не доставлен", user_id)
    except TelegramBadRequest as e:
        log.warning("Не удалось доставить сообщение пользователю %s: %s", user_id, e)


@user_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    mx, win = RL_START
    if uid and not await rate_allow(uid, "start", mx, win):
        await maybe_warn_flood(message, uid)
        return
    await message.answer(
        "Здравствуйте! Выберите пункт меню или напишите сообщение — "
        "менеджер ответит вам здесь.",
        reply_markup=kb_for(uid),
    )


@user_router.message(F.chat.type == ChatType.PRIVATE, F.text == "📅 Мои мероприятия")
async def on_events(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    mx, win = RL_MENU
    if uid and not await rate_allow(uid, "menu", mx, win):
        await maybe_warn_flood(message, uid)
        return
    text = await format_user_events_text(uid)
    await message.answer(text, parse_mode="HTML", reply_markup=kb_for(uid))


@user_router.message(F.chat.type == ChatType.PRIVATE, F.text == "🌐 Наш сайт")
async def on_site(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    mx, win = RL_MENU
    if uid and not await rate_allow(uid, "menu", mx, win):
        await maybe_warn_flood(message, uid)
        return
    url = await get_site_url()
    await message.answer(
        f'Наш сайт: <a href="{url}">{url}</a>',
        parse_mode="HTML",
        reply_markup=kb_for(uid),
    )


@user_router.message(F.chat.type == ChatType.PRIVATE, F.text == "💰 Тарифы")
async def on_tariffs(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    mx, win = RL_MENU
    if uid and not await rate_allow(uid, "menu", mx, win):
        await maybe_warn_flood(message, uid)
        return
    await message.answer(
        await get_tariffs_text(),
        parse_mode="HTML",
        reply_markup=kb_for(uid),
    )


@user_router.message(F.chat.type == ChatType.PRIVATE, F.text == "📩 Написать нам")
async def on_write_us(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    mx, win = RL_MENU
    if uid and not await rate_allow(uid, "menu", mx, win):
        await maybe_warn_flood(message, uid)
        return
    await message.answer(
        "Просто напишите ваш вопрос в этот чат, и наш менеджер ответит вам здесь же.",
        reply_markup=kb_for(uid),
    )


@user_router.message(F.chat.type == ChatType.PRIVATE, StateFilter(None))
async def on_private_feedback(message: Message, bot: Bot) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    uid = message.from_user.id
    if message.text:
        if message.text == BTN_ADMIN:
            return
        if message.text.startswith("/"):
            return
        if message.text in MENU_TEXTS:
            return

    mx, win = RL_PRIVATE_MSG
    if not await rate_allow(uid, "pmsg", mx, win):
        await maybe_warn_flood(message, uid)
        return

    name = message.from_user.full_name or str(uid)
    try:
        topic_id = await ensure_forum_topic(bot, uid, name)
        await send_user_message_to_topic(bot, message, topic_id)
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        log.exception("Ошибка при отправке в группу: %s", e)
        await message.answer("Сейчас не удаётся связаться с менеджером. Попробуйте позже.")


@group_router.message(F.chat.id == GROUP_ID, F.message_thread_id)
async def on_group_thread_message(message: Message, bot: Bot) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    if message.sender_chat:
        return

    thread_id = message.message_thread_id
    if thread_id is None:
        return

    if message.forum_topic_created or message.forum_topic_closed or message.forum_topic_reopened:
        return

    user_id = await db_get_user_id_by_topic(thread_id)
    if user_id is None:
        return

    mx, win = RL_GROUP_TO_USER
    if not await rate_allow(user_id, "gout", mx, win):
        try:
            await message.reply(
                "Слишком частые сообщения пользователю. Подождите немного.",
                disable_notification=True,
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        return

    await safe_copy_to_user(bot, user_id, message.chat.id, message.message_id)


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Задайте BOT_TOKEN в .env.")
    if not GROUP_ID:
        raise SystemExit("Задайте GROUP_ID в .env.")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS не задан.")

    await init_db()

    register_admin_handlers(
        admin_router,
        ADMIN_IDS,
        DEFAULT_TARIFFS_TEXT,
        DEFAULT_SITE_URL,
        reply_kb=kb_for,
    )
    register_request_handlers(
        requests_router,
        ADMIN_IDS,
        is_admin,
        ensure_forum_topic,
        GROUP_ID,
        kb_for,
        TZ,
    )

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(requests_router)
    dp.include_router(user_router)
    dp.include_router(group_router)

    await job_expire_events()
    start_scheduler(bot, GROUP_ID, TZ)

    log.info("Бот запущен, GROUP_ID=%s, admins=%s", GROUP_ID, len(ADMIN_IDS))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
