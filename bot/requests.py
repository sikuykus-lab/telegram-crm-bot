"""User event request flow + admin approve/reject."""
from __future__ import annotations

import logging
from typing import Callable

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)

from db import (
    create_event,
    create_event_request,
    format_event_date,
    get_event_request,
    has_pending_request,
    parse_event_date,
    update_event_request,
    validate_user_event_date,
)
from keyboards import BTN_BOOK, MENU_TEXTS

log = logging.getLogger("crm_bot.requests")

requests_router = Router(name="requests")

CB_REQ_APPROVE = "req:ok:"
CB_REQ_REJECT = "req:no:"


class BookEventStates(StatesGroup):
    description = State()
    event_date = State()


class RejectRequestStates(StatesGroup):
    reason = State()


def request_actions_kb(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"{CB_REQ_APPROVE}{request_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"{CB_REQ_REJECT}{request_id}"),
            ]
        ]
    )


def register_request_handlers(
    router: Router,
    admin_ids: frozenset[int],
    admin_ids_check,
    ensure_topic: Callable,
    group_id: int,
    reply_kb: Callable[[int], ReplyKeyboardMarkup],
    tz: str = "Europe/Moscow",
) -> None:

    @router.message(F.chat.type == ChatType.PRIVATE, F.text == BTN_BOOK)
    async def btn_book_event(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id if message.from_user else 0
        if await has_pending_request(uid):
            await message.answer(
                "У вас уже есть заявка на согласовании.\n"
                "Дождитесь ответа менеджера или проверьте «📅 Мои мероприятия».",
                reply_markup=reply_kb(uid),
            )
            return
        await state.set_state(BookEventStates.description)
        await message.answer(
            "📝 <b>Заявка на мероприятие</b>\n\n"
            "Опишите мероприятие: формат, количество гостей, пожелания.",
            parse_mode="HTML",
            reply_markup=reply_kb(uid),
        )

    @router.message(BookEventStates.description)
    async def fsm_book_description(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id if message.from_user else 0
        text = (message.text or "").strip()
        if text in MENU_TEXTS or text.startswith("/"):
            await state.clear()
            await message.answer("Заявка отменена.", reply_markup=reply_kb(uid))
            return
        if len(text) < 5:
            await message.answer("Опишите подробнее (минимум 5 символов).")
            return
        await state.update_data(description=text)
        await state.set_state(BookEventStates.event_date)
        await message.answer(
            "Укажите желаемую дату (ДД.ММ.ГГГГ).\n"
            "Не раньше сегодня и не более чем на 6 месяцев вперёд.",
            reply_markup=reply_kb(uid),
        )

    @router.message(BookEventStates.event_date)
    async def fsm_book_date(message: Message, state: FSMContext, bot: Bot) -> None:
        uid = message.from_user.id if message.from_user else 0
        if (message.text or "").strip() in MENU_TEXTS:
            await state.clear()
            await message.answer("Заявка отменена.", reply_markup=reply_kb(uid))
            return
        iso = parse_event_date(message.text or "")
        if not iso:
            await message.answer("Неверный формат. Пример: 15.06.2026")
            return
        err = validate_user_event_date(iso, tz)
        if err:
            await message.answer(err)
            return

        data = await state.get_data()
        description = data["description"]
        await state.clear()

        rid = await create_event_request(uid, description, iso)
        name = message.from_user.full_name or str(uid) if message.from_user else str(uid)
        topic_id = await ensure_topic(bot, uid, name)
        d = format_event_date(iso)

        group_text = (
            f"📝 <b>Заявка на мероприятие #{rid}</b>\n\n"
            f"Клиент: {name}\n"
            f"<code>user_id={uid}</code>\n\n"
            f"<b>Описание:</b>\n{description}\n\n"
            f"<b>Дата:</b> {d}"
        )
        sent = await bot.send_message(
            chat_id=group_id,
            message_thread_id=topic_id,
            text=group_text,
            parse_mode="HTML",
            reply_markup=request_actions_kb(rid),
        )
        await update_event_request(rid, group_message_id=sent.message_id, topic_id=topic_id)

        await message.answer(
            f"✅ Заявка #{rid} отправлена менеджеру.\n"
            f"Дата: {d}\n\n"
            "Мы сообщим, когда заявку подтвердят или отклонят.",
            reply_markup=reply_kb(uid),
        )

    @router.callback_query(F.data.startswith(CB_REQ_APPROVE))
    async def cb_approve_request(call: CallbackQuery, bot: Bot) -> None:
        if not admin_ids_check(call.from_user.id, admin_ids):
            await call.answer("Только для администратора", show_alert=True)
            return
        rid = int(call.data.removeprefix(CB_REQ_APPROVE))
        req = await get_event_request(rid)
        if not req or req["status"] != "pending":
            await call.answer("Заявка уже обработана", show_alert=True)
            return

        uid = int(req["user_id"])
        d = format_event_date(req["event_date"])
        title = req["description"][:120]
        eid = await create_event(uid, title, req["event_date"], notes=req["description"])
        await update_event_request(rid, status="approved", event_id=eid)

        try:
            await bot.send_message(
                uid,
                f"✅ Заявка #{rid} <b>подтверждена</b>!\n\n"
                f"<b>{title}</b>\nДата: {d}",
                parse_mode="HTML",
            )
        except Exception as e:
            log.info("Notify user %s approve: %s", uid, e)

        new_text = (
            f"✅ <b>Заявка #{rid} подтверждена</b>\n"
            f"Мероприятие #{eid}\n\n"
            f"{req['description']}\n\nДата: {d}"
        )
        try:
            await call.message.edit_text(new_text, parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await call.answer("Подтверждено")

    @router.callback_query(F.data.startswith(CB_REQ_REJECT))
    async def cb_reject_request(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not admin_ids_check(call.from_user.id, admin_ids):
            await call.answer("Только для администратора", show_alert=True)
            return
        rid = int(call.data.removeprefix(CB_REQ_REJECT))
        req = await get_event_request(rid)
        if not req or req["status"] != "pending":
            await call.answer("Заявка уже обработана", show_alert=True)
            return

        await state.update_data(reject_request_id=rid)
        await state.set_state(RejectRequestStates.reason)
        await call.answer()
        await bot.send_message(
            call.from_user.id,
            f"❌ Заявка #{rid}\n\n"
            f"<b>Опишите причину отказа</b> — она будет отправлена клиенту:",
            parse_mode="HTML",
        )

    @router.message(RejectRequestStates.reason)
    async def fsm_reject_reason(message: Message, state: FSMContext, bot: Bot) -> None:
        if not admin_ids_check(message.from_user.id if message.from_user else 0, admin_ids):
            return
        reason = (message.text or "").strip()
        if len(reason) < 3:
            await message.answer("Укажите причину отказа (минимум 3 символа).")
            return

        data = await state.get_data()
        rid = int(data["reject_request_id"])
        req = await get_event_request(rid)
        if not req or req["status"] != "pending":
            await state.clear()
            await message.answer("Заявка уже обработана.")
            return

        uid = int(req["user_id"])
        d = format_event_date(req["event_date"])
        await update_event_request(rid, status="rejected", reject_reason=reason)
        await state.clear()

        try:
            await bot.send_message(
                uid,
                f"❌ Заявка #{rid} <b>отклонена</b>\n\n"
                f"{req['description']}\nДата: {d}\n\n"
                f"<b>Причина:</b> {reason}",
                parse_mode="HTML",
            )
        except Exception as e:
            log.info("Notify user %s reject: %s", uid, e)

        if req.get("group_message_id") and req.get("topic_id"):
            try:
                await bot.edit_message_text(
                    chat_id=group_id,
                    message_id=int(req["group_message_id"]),
                    message_thread_id=int(req["topic_id"]),
                    text=(
                        f"❌ <b>Заявка #{rid} отклонена</b>\n\n"
                        f"{req['description']}\nДата: {d}\n\n"
                        f"<b>Причина:</b> {reason}"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                log.warning("Edit group message reject: %s", e)

        await message.answer(f"✅ Заявка #{rid} отклонена, клиент уведомлён.")
