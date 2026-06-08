"""Telegram admin panel for CRM bot."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from db import (
    cancel_event,
    count_clients,
    count_pending_requests,
    count_upcoming_events,
    create_event,
    delete_event,
    format_event_date,
    get_client,
    get_event,
    get_event_request,
    get_setting,
    list_clients,
    list_pending_requests,
    list_upcoming_events,
    list_user_events,
    parse_event_date,
    set_setting,
    update_event,
)

log = logging.getLogger("crm_bot.admin")

admin_router = Router(name="admin")

from keyboards import BTN_ADMIN

CB_MAIN = "adm:main"
CB_EVENTS = "adm:events"
CB_EVENT_ADD = "adm:evt:add"
CB_EVENT_LIST = "adm:evt:list"
CB_EVENT_BY_USER = "adm:evt:byuser"
CB_EVENT_VIEW = "adm:evt:view:"
CB_EVENT_DEL = "adm:evt:del:"
CB_EVENT_DEL_OK = "adm:evt:delok:"
CB_EVENT_CANCEL = "adm:evt:cnl:"
CB_EVENT_CANCEL_OK = "adm:evt:cnlok:"
CB_EVENT_EDIT_TITLE = "adm:evt:edt:"
CB_EVENT_EDIT_DATE = "adm:evt:edd:"
CB_EVENT_EDIT_NOTES = "adm:evt:edn:"
CB_USER_EVENTS = "adm:usr:evact:"
CB_USER_EVENTS_ALL = "adm:usr:evall:"
CB_USER_ADD_EVT = "adm:usr:add:"
CB_CLIENTS = "adm:clients"
CB_STATS = "adm:stats"
CB_SETTINGS = "adm:settings"
CB_SET_TARIFFS = "adm:set:tariffs"
CB_SET_SITE = "adm:set:site"
CB_PICK_USER = "adm:pick:"
CB_CANCEL = "adm:cancel"
CB_REQUESTS = "adm:requests"
CB_REQUEST_VIEW = "adm:req:view:"


class AddEventStates(StatesGroup):
    user_id = State()
    title = State()
    event_date = State()
    notes = State()


class BrowseUserStates(StatesGroup):
    user_id = State()


class EditEventStates(StatesGroup):
    title = State()
    event_date = State()
    notes = State()


class EditSettingStates(StatesGroup):
    tariffs = State()
    site = State()


def is_admin(user_id: int | None, admin_ids: frozenset[int]) -> bool:
    return bool(user_id and user_id in admin_ids)


def admin_main_kb(pending: int = 0) -> InlineKeyboardMarkup:
    req_label = f"📥 Заявки ({pending})" if pending else "📥 Заявки"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=req_label, callback_data=CB_REQUESTS)],
            [InlineKeyboardButton(text="📅 Мероприятия", callback_data=CB_EVENTS)],
            [InlineKeyboardButton(text="👥 Клиенты", callback_data=CB_CLIENTS)],
            [InlineKeyboardButton(text="📊 Статистика", callback_data=CB_STATS)],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS)],
        ]
    )


def events_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data=CB_EVENT_ADD)],
            [InlineKeyboardButton(text="📋 Все предстоящие", callback_data=CB_EVENT_LIST)],
            [InlineKeyboardButton(text="👤 Мероприятия клиента", callback_data=CB_EVENT_BY_USER)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=CB_MAIN)],
        ]
    )


def back_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ В админ-панель", callback_data=CB_MAIN)]]
    )


def cancel_fsm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)]]
    )


def settings_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Тарифы", callback_data=CB_SET_TARIFFS)],
            [InlineKeyboardButton(text="🌐 URL сайта", callback_data=CB_SET_SITE)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=CB_MAIN)],
        ]
    )


async def open_admin_panel(message: Message, state: FSMContext, reply_kb=None) -> None:
    await state.clear()
    pending = await count_pending_requests()
    await message.answer(
        "🛠 <b>Админ-панель MetaFora VR</b>\n\nВыберите раздел:",
        reply_markup=admin_main_kb(pending),
        parse_mode="HTML",
    )
    if reply_kb and message.from_user:
        uid = message.from_user.id
        await message.answer("·", reply_markup=reply_kb(uid))


def event_view_kb(event_id: int, user_id: int, status: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"{CB_EVENT_EDIT_TITLE}{event_id}"),
            InlineKeyboardButton(text="📅 Дата", callback_data=f"{CB_EVENT_EDIT_DATE}{event_id}"),
        ],
        [InlineKeyboardButton(text="📝 Заметки", callback_data=f"{CB_EVENT_EDIT_NOTES}{event_id}")],
    ]
    if status == "scheduled":
        rows.append([
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"{CB_EVENT_CANCEL}{event_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_EVENT_DEL}{event_id}"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_EVENT_DEL}{event_id}")])
    rows.append([
        InlineKeyboardButton(text="➕ Добавить клиенту", callback_data=f"{CB_USER_ADD_EVT}{user_id}"),
        InlineKeyboardButton(text="◀️ К клиенту", callback_data=f"{CB_USER_EVENTS}{user_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_user_events_message(user_id: int, *, active_only: bool = True) -> tuple[str, InlineKeyboardMarkup]:
    client = await get_client(user_id)
    name = (client or {}).get("display_name") or "—"
    events = await list_user_events(user_id, active_only=active_only)
    mode = "активные" if active_only else "все"
    lines = [f"👤 <b>Клиент</b> {name}\n<code>{user_id}</code>\n\n📅 Мероприятия ({mode}):\n"]
    buttons: list[list[InlineKeyboardButton]] = []

    if not events:
        lines.append("— нет мероприятий —")
    else:
        for ev in events:
            d = format_event_date(ev["event_date"])
            st = ev["status"]
            lines.append(f"• #{ev['id']} {ev['title']} — {d} [{st}]")
            buttons.append([
                InlineKeyboardButton(
                    text=f"#{ev['id']} {ev['title'][:18]}",
                    callback_data=f"{CB_EVENT_VIEW}{ev['id']}",
                )
            ])

    toggle = CB_USER_EVENTS_ALL if active_only else CB_USER_EVENTS
    toggle_text = "📜 Показать все" if active_only else "📋 Только активные"
    buttons.append([InlineKeyboardButton(text="➕ Добавить", callback_data=f"{CB_USER_ADD_EVT}{user_id}")])
    buttons.append([InlineKeyboardButton(text=toggle_text, callback_data=f"{toggle}{user_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ К мероприятиям", callback_data=CB_EVENTS)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def register_admin_handlers(
    router: Router,
    admin_ids: frozenset[int],
    default_tariffs: str,
    default_site_url: str,
    reply_kb=None,
) -> None:

    @router.message(F.chat.type == ChatType.PRIVATE, F.text == BTN_ADMIN)
    async def btn_admin(message: Message, state: FSMContext) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not is_admin(uid, admin_ids):
            return
        await open_admin_panel(message, state, reply_kb=reply_kb)

    @router.callback_query(F.data == CB_MAIN)
    async def cb_main(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        await state.clear()
        pending = await count_pending_requests()
        await call.message.edit_text(
            "🛠 <b>Админ-панель MetaFora VR</b>\n\nВыберите раздел:",
            reply_markup=admin_main_kb(pending),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data == CB_CANCEL)
    async def cb_cancel_fsm(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        await state.clear()
        pending = await count_pending_requests()
        await call.message.edit_text(
            "🛠 <b>Админ-панель MetaFora VR</b>\n\nВыберите раздел:",
            reply_markup=admin_main_kb(pending),
            parse_mode="HTML",
        )
        await call.answer("Отменено")

    @router.callback_query(F.data == CB_REQUESTS)
    async def cb_requests_list(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        reqs = await list_pending_requests(25)
        if not reqs:
            await call.message.edit_text(
                "📥 Нет заявок на согласовании.",
                reply_markup=back_main_kb(),
            )
            await call.answer()
            return
        lines = ["📥 <b>Заявки на согласовании</b>\n"]
        buttons = []
        for r in reqs:
            d = format_event_date(r["event_date"])
            name = r.get("display_name") or r["user_id"]
            lines.append(f"• #{r['id']} {name} — {d}")
            buttons.append([
                InlineKeyboardButton(
                    text=f"#{r['id']} {r['description'][:22]}",
                    callback_data=f"{CB_REQUEST_VIEW}{r['id']}",
                )
            ])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=CB_MAIN)])
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data.startswith(CB_REQUEST_VIEW))
    async def cb_request_view(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        from requests import request_actions_kb

        rid = int(call.data.removeprefix(CB_REQUEST_VIEW))
        req = await get_event_request(rid)
        if not req:
            await call.answer("Не найдено", show_alert=True)
            return
        d = format_event_date(req["event_date"])
        text = (
            f"📥 <b>Заявка #{rid}</b> [{req['status']}]\n\n"
            f"Клиент: <code>{req['user_id']}</code>\n"
            f"Дата: {d}\n\n"
            f"{req['description']}"
        )
        kb = request_actions_kb(rid) if req["status"] == "pending" else back_main_kb()
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await call.answer()

    @router.callback_query(F.data == CB_EVENTS)
    async def cb_events(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        n = await count_upcoming_events()
        await call.message.edit_text(
            f"📅 <b>Мероприятия</b>\n\nПредстоящих: {n}",
            reply_markup=events_menu_kb(),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data == CB_EVENT_LIST)
    async def cb_event_list(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        events = await list_upcoming_events(25)
        if not events:
            await call.message.edit_text("📋 Нет предстоящих мероприятий.", reply_markup=events_menu_kb())
            await call.answer()
            return

        lines = ["📋 <b>Все предстоящие</b>\n"]
        buttons = []
        for ev in events:
            d = format_event_date(ev["event_date"])
            name = ev.get("display_name") or ev["user_id"]
            lines.append(f"• #{ev['id']} {ev['title']} — {d}\n  👤 {name}")
            buttons.append([
                InlineKeyboardButton(
                    text=f"#{ev['id']} {ev['title'][:20]}",
                    callback_data=f"{CB_EVENT_VIEW}{ev['id']}",
                )
            ])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=CB_EVENTS)])
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data == CB_EVENT_BY_USER)
    async def cb_event_by_user(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        clients = await list_clients(20)
        buttons = []
        for c in clients:
            label = (c.get("display_name") or str(c["user_id"]))[:28]
            buttons.append([
                InlineKeyboardButton(text=f"👤 {label}", callback_data=f"{CB_USER_EVENTS}{c['user_id']}")
            ])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=CB_EVENTS)])
        await state.set_state(BrowseUserStates.user_id)
        await call.message.edit_text(
            "👤 <b>Мероприятия клиента</b>\n\n"
            "Выберите клиента или отправьте <code>user_id</code> числом:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await call.answer()

    @router.message(BrowseUserStates.user_id)
    async def fsm_browse_user(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        text = (message.text or "").strip()
        if not text.isdigit():
            await message.answer("Введите числовой user_id.", reply_markup=cancel_fsm_kb())
            return
        uid = int(text)
        await state.clear()
        body, kb = await render_user_events_message(uid, active_only=True)
        await message.answer(body, reply_markup=kb, parse_mode="HTML")

    @router.callback_query(F.data.startswith(CB_USER_EVENTS_ALL))
    async def cb_user_events_all(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        uid = int(call.data.removeprefix(CB_USER_EVENTS_ALL))
        body, kb = await render_user_events_message(uid, active_only=False)
        await call.message.edit_text(body, reply_markup=kb, parse_mode="HTML")
        await call.answer()

    @router.callback_query(F.data.startswith(CB_USER_EVENTS))
    async def cb_user_events(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        await state.clear()
        uid = int(call.data.removeprefix(CB_USER_EVENTS))
        body, kb = await render_user_events_message(uid, active_only=True)
        await call.message.edit_text(body, reply_markup=kb, parse_mode="HTML")
        await call.answer()

    @router.callback_query(F.data.startswith(CB_USER_ADD_EVT))
    async def cb_user_add_event(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        uid = int(call.data.removeprefix(CB_USER_ADD_EVT))
        await state.update_data(user_id=uid)
        await state.set_state(AddEventStates.title)
        await call.message.edit_text(
            f"➕ Новое мероприятие для <code>{uid}</code>\n\nВведите название:",
            reply_markup=cancel_fsm_kb(),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data.startswith(CB_EVENT_VIEW))
    async def cb_event_view(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_VIEW))
        ev = await get_event(eid)
        if not ev:
            await call.answer("Не найдено", show_alert=True)
            return
        d = format_event_date(ev["event_date"])
        notes = ev.get("notes") or "—"
        text = (
            f"📅 <b>Мероприятие #{eid}</b>\n\n"
            f"<b>{ev['title']}</b>\n"
            f"Дата: {d}\n"
            f"Клиент: <code>{ev['user_id']}</code>\n"
            f"Статус: {ev['status']}\n"
            f"Заметки: {notes}"
        )
        await call.message.edit_text(
            text,
            reply_markup=event_view_kb(eid, int(ev["user_id"]), ev["status"]),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data.startswith(CB_EVENT_CANCEL_OK))
    async def cb_event_cancel_ok(call: CallbackQuery, bot: Bot) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_CANCEL_OK))
        ev = await get_event(eid)
        if not ev:
            await call.answer("Не найдено", show_alert=True)
            return
        await cancel_event(eid)
        await call.answer("Отменено")
        uid = int(ev["user_id"])
        try:
            await bot.send_message(
                uid,
                f"❌ Мероприятие отменено: <b>{ev['title']}</b> ({format_event_date(ev['event_date'])})",
                parse_mode="HTML",
            )
        except Exception:
            pass
        body, kb = await render_user_events_message(uid, active_only=True)
        await call.message.edit_text(body, reply_markup=kb, parse_mode="HTML")

    @router.callback_query(F.data.startswith(CB_EVENT_CANCEL))
    async def cb_event_cancel(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_CANCEL))
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"{CB_EVENT_CANCEL_OK}{eid}"),
                    InlineKeyboardButton(text="Назад", callback_data=f"{CB_EVENT_VIEW}{eid}"),
                ]
            ]
        )
        await call.message.edit_text(f"Отменить мероприятие #{eid}?", reply_markup=kb)
        await call.answer()

    @router.callback_query(F.data.startswith(CB_EVENT_DEL_OK))
    async def cb_event_del_ok(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_DEL_OK))
        ev = await get_event(eid)
        uid = int(ev["user_id"]) if ev else 0
        await delete_event(eid)
        await call.answer("Удалено")
        if uid:
            body, kb = await render_user_events_message(uid, active_only=True)
            await call.message.edit_text(body, reply_markup=kb, parse_mode="HTML")
        else:
            await cb_event_list(call)

    @router.callback_query(F.data.startswith(CB_EVENT_DEL))
    async def cb_event_del(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_DEL))
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"{CB_EVENT_DEL_OK}{eid}"),
                    InlineKeyboardButton(text="Отмена", callback_data=f"{CB_EVENT_VIEW}{eid}"),
                ]
            ]
        )
        await call.message.edit_text(f"Удалить мероприятие #{eid}?", reply_markup=kb)
        await call.answer()

    @router.callback_query(F.data.startswith(CB_EVENT_EDIT_TITLE))
    async def cb_edit_title(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_EDIT_TITLE))
        await state.update_data(event_id=eid)
        await state.set_state(EditEventStates.title)
        await call.message.edit_text(
            f"✏️ Новое название для #{eid}:",
            reply_markup=cancel_fsm_kb(),
        )
        await call.answer()

    @router.callback_query(F.data.startswith(CB_EVENT_EDIT_DATE))
    async def cb_edit_date(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_EDIT_DATE))
        await state.update_data(event_id=eid)
        await state.set_state(EditEventStates.event_date)
        await call.message.edit_text(
            f"📅 Новая дата для #{eid} (ДД.ММ.ГГГГ):",
            reply_markup=cancel_fsm_kb(),
        )
        await call.answer()

    @router.callback_query(F.data.startswith(CB_EVENT_EDIT_NOTES))
    async def cb_edit_notes(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        eid = int(call.data.removeprefix(CB_EVENT_EDIT_NOTES))
        await state.update_data(event_id=eid)
        await state.set_state(EditEventStates.notes)
        await call.message.edit_text(
            f"📝 Новые заметки для #{eid} (или «-» очистить):",
            reply_markup=cancel_fsm_kb(),
        )
        await call.answer()

    @router.message(EditEventStates.title)
    async def fsm_edit_title(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        title = (message.text or "").strip()
        if len(title) < 2:
            await message.answer("Слишком коротко.", reply_markup=cancel_fsm_kb())
            return
        data = await state.get_data()
        eid = int(data["event_id"])
        await update_event(eid, title=title)
        await state.clear()
        await message.answer(f"✅ Название обновлено (#{eid})", reply_markup=admin_main_kb())

    @router.message(EditEventStates.event_date)
    async def fsm_edit_date(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        iso = parse_event_date(message.text or "")
        if not iso:
            await message.answer("Неверный формат. Пример: 15.06.2026", reply_markup=cancel_fsm_kb())
            return
        data = await state.get_data()
        eid = int(data["event_id"])
        await update_event(eid, event_date=iso)
        await state.clear()
        await message.answer(f"✅ Дата обновлена (#{eid})", reply_markup=admin_main_kb())

    @router.message(EditEventStates.notes)
    async def fsm_edit_notes(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        notes = (message.text or "").strip()
        if notes == "-":
            notes = ""
        data = await state.get_data()
        eid = int(data["event_id"])
        await update_event(eid, notes=notes)
        await state.clear()
        await message.answer(f"✅ Заметки обновлены (#{eid})", reply_markup=admin_main_kb())

    @router.callback_query(F.data == CB_EVENT_ADD)
    async def cb_event_add(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        clients = await list_clients(15)
        buttons = []
        for c in clients:
            label = (c.get("display_name") or str(c["user_id"]))[:28]
            buttons.append([
                InlineKeyboardButton(text=f"👤 {label}", callback_data=f"{CB_PICK_USER}{c['user_id']}")
            ])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)])
        await state.set_state(AddEventStates.user_id)
        await call.message.edit_text(
            "➕ <b>Новое мероприятие</b>\n\n"
            "Выберите клиента или отправьте <code>user_id</code> числом:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data.startswith(CB_PICK_USER))
    async def cb_pick_user(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        uid = int(call.data.removeprefix(CB_PICK_USER))
        await state.update_data(user_id=uid)
        await state.set_state(AddEventStates.title)
        await call.message.edit_text(
            f"Клиент: <code>{uid}</code>\n\nВведите название мероприятия:",
            reply_markup=cancel_fsm_kb(),
            parse_mode="HTML",
        )
        await call.answer()

    @router.message(AddEventStates.user_id)
    async def fsm_event_user(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        text = (message.text or "").strip()
        if not text.isdigit():
            await message.answer("Введите числовой user_id.", reply_markup=cancel_fsm_kb())
            return
        await state.update_data(user_id=int(text))
        await state.set_state(AddEventStates.title)
        await message.answer(
            f"Клиент: <code>{text}</code>\n\nВведите название:",
            reply_markup=cancel_fsm_kb(),
            parse_mode="HTML",
        )

    @router.message(AddEventStates.title)
    async def fsm_event_title(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        title = (message.text or "").strip()
        if len(title) < 2:
            await message.answer("Слишком короткое название.", reply_markup=cancel_fsm_kb())
            return
        await state.update_data(title=title)
        await state.set_state(AddEventStates.event_date)
        await message.answer("Дата (ДД.ММ.ГГГГ):", reply_markup=cancel_fsm_kb())

    @router.message(AddEventStates.event_date)
    async def fsm_event_date(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        iso = parse_event_date(message.text or "")
        if not iso:
            await message.answer("Неверный формат. Пример: 15.06.2026", reply_markup=cancel_fsm_kb())
            return
        await state.update_data(event_date=iso)
        await state.set_state(AddEventStates.notes)
        await message.answer("Заметки (или «-» пропустить):", reply_markup=cancel_fsm_kb())

    @router.message(AddEventStates.notes)
    async def fsm_event_notes(message: Message, state: FSMContext, bot: Bot) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        data = await state.get_data()
        notes = (message.text or "").strip()
        if notes == "-":
            notes = ""
        eid = await create_event(
            user_id=int(data["user_id"]),
            title=data["title"],
            event_date=data["event_date"],
            notes=notes,
        )
        await state.clear()
        d = format_event_date(data["event_date"])
        await message.answer(
            f"✅ Мероприятие #{eid} создано:\n<b>{data['title']}</b> — {d}\n"
            f"Клиент: <code>{data['user_id']}</code>",
            reply_markup=admin_main_kb(),
            parse_mode="HTML",
        )
        try:
            await bot.send_message(
                int(data["user_id"]),
                f"📅 Вам назначено мероприятие:\n<b>{data['title']}</b> — {d}"
                + (f"\n{notes}" if notes else ""),
                parse_mode="HTML",
            )
        except Exception as e:
            log.info("Could not notify user %s: %s", data["user_id"], e)

    @router.callback_query(F.data == CB_CLIENTS)
    async def cb_clients(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        clients = await list_clients(30)
        if not clients:
            await call.message.edit_text("👥 Клиентов пока нет.", reply_markup=back_main_kb())
            await call.answer()
            return
        lines = ["👥 <b>Клиенты</b>\n\nНажмите — открыть мероприятия:\n"]
        buttons = []
        for c in clients:
            name = c.get("display_name") or "—"
            lines.append(f"• {name} — <code>{c['user_id']}</code>")
            buttons.append([
                InlineKeyboardButton(
                    text=f"👤 {name[:24]}",
                    callback_data=f"{CB_USER_EVENTS}{c['user_id']}",
                )
            ])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=CB_MAIN)])
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data == CB_STATS)
    async def cb_stats(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        await call.message.edit_text(
            f"📊 <b>Статистика</b>\n\n👥 Клиентов: {await count_clients()}\n"
            f"📅 Предстоящих: {await count_upcoming_events()}",
            reply_markup=back_main_kb(),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data == CB_SETTINGS)
    async def cb_settings(call: CallbackQuery) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        site = await get_setting("site_url") or default_site_url
        tariffs = await get_setting("tariffs_text") or default_tariffs
        preview = tariffs[:120].replace("<", "").replace(">", "")
        await call.message.edit_text(
            f"⚙️ <b>Настройки</b>\n\n🌐 {site}\n\n💰 {preview}…",
            reply_markup=settings_menu_kb(),
            parse_mode="HTML",
        )
        await call.answer()

    @router.callback_query(F.data == CB_SET_TARIFFS)
    async def cb_set_tariffs(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        await state.set_state(EditSettingStates.tariffs)
        await call.message.edit_text(
            "Новый текст тарифов (HTML). «-» — сброс к .env",
            reply_markup=cancel_fsm_kb(),
        )
        await call.answer()

    @router.callback_query(F.data == CB_SET_SITE)
    async def cb_set_site(call: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(call.from_user.id, admin_ids):
            await call.answer("Нет доступа", show_alert=True)
            return
        await state.set_state(EditSettingStates.site)
        await call.message.edit_text(
            "Новый URL сайта. «-» — сброс к .env",
            reply_markup=cancel_fsm_kb(),
        )
        await call.answer()

    @router.message(EditSettingStates.tariffs)
    async def fsm_set_tariffs(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        text = (message.text or "").strip()
        await set_setting("tariffs_text", "" if text == "-" else text)
        await state.clear()
        await message.answer("✅ Тарифы обновлены.", reply_markup=admin_main_kb())

    @router.message(EditSettingStates.site)
    async def fsm_set_site(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else 0, admin_ids):
            return
        text = (message.text or "").strip()
        await set_setting("site_url", "" if text == "-" else text)
        await state.clear()
        await message.answer("✅ URL обновлён.", reply_markup=admin_main_kb())
