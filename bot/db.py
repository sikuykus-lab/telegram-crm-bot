"""SQLite persistence for CRM bot."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

DB_PATH = Path(__file__).resolve().parent / "bot_data.db"


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_topics (
                user_id INTEGER PRIMARY KEY NOT NULL,
                topic_id INTEGER NOT NULL UNIQUE,
                display_name TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                notes TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'scheduled',
                week_reminder_sent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_events_user_date
                ON events(user_id, event_date);

            CREATE INDEX IF NOT EXISTS idx_events_status_date
                ON events(status, event_date);

            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS event_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                event_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reject_reason TEXT DEFAULT '',
                group_message_id INTEGER,
                topic_id INTEGER,
                event_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_event_requests_status
                ON event_requests(status, user_id);
            """
        )
        for stmt in (
            "ALTER TABLE user_topics ADD COLUMN display_name TEXT DEFAULT ''",
        ):
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError:
                pass
        await db.commit()


async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value),
        )
        await db.commit()


async def db_get_topic_id(user_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT topic_id FROM user_topics WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        return int(row["topic_id"]) if row else None


async def db_get_user_id_by_topic(topic_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id FROM user_topics WHERE topic_id = ?",
            (topic_id,),
        )
        row = await cur.fetchone()
        return int(row["user_id"]) if row else None


async def db_save_user_topic(user_id: int, topic_id: int, display_name: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_topics (user_id, topic_id, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                topic_id = excluded.topic_id,
                display_name = CASE
                    WHEN excluded.display_name != '' THEN excluded.display_name
                    ELSE user_topics.display_name
                END;
            """,
            (user_id, topic_id, display_name),
        )
        await db.commit()


async def list_clients(limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT user_id, topic_id, display_name, created_at
            FROM user_topics
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def count_clients() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM user_topics")
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def count_upcoming_events() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE status = 'scheduled' AND event_date >= date('now')
            """
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0


def format_event_date(iso_date: str) -> str:
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def parse_event_date(text: str) -> str | None:
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _shift_months(d: date, months: int) -> date:
    import calendar

    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def validate_user_event_date(iso: str, tz: str = "Europe/Moscow") -> str | None:
    """Проверка даты заявки пользователя. None — ок, иначе текст ошибки."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return "Неверная дата."
    today = datetime.now(ZoneInfo(tz)).date()
    if d < today:
        return "Дата не может быть в прошлом. Укажите сегодня или позже."
    max_d = _shift_months(today, 6)
    if d > max_d:
        return (
            f"Можно запланировать не более чем на 6 месяцев вперёд "
            f"(до {max_d.strftime('%d.%m.%Y')})."
        )
    return None


async def create_event(user_id: int, title: str, event_date: str, notes: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO events (user_id, title, event_date, notes)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, title.strip(), event_date, notes.strip()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_event(event_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def delete_event(event_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await db.commit()
        return cur.rowcount > 0


async def cancel_event(event_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE events SET status = 'cancelled', updated_at = datetime('now')
            WHERE id = ?
            """,
            (event_id,),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_event(
    event_id: int,
    *,
    title: str | None = None,
    event_date: str | None = None,
    notes: str | None = None,
    status: str | None = None,
) -> bool:
    fields: list[str] = []
    values: list[Any] = []
    if title is not None:
        fields.append("title = ?")
        values.append(title.strip())
    if event_date is not None:
        fields.append("event_date = ?")
        values.append(event_date)
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes.strip())
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if not fields:
        return False
    fields.append("updated_at = datetime('now')")
    values.append(event_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE events SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await db.commit()
        return cur.rowcount > 0


async def get_client(user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT user_id, topic_id, display_name, created_at FROM user_topics WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_upcoming_events(limit: int = 30) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT e.*, ut.display_name, ut.topic_id
            FROM events e
            LEFT JOIN user_topics ut ON ut.user_id = e.user_id
            WHERE e.status = 'scheduled' AND e.event_date >= date('now')
            ORDER BY e.event_date ASC, e.id ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_user_events(user_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
    """Admin: all events for a user (optionally only scheduled upcoming)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if active_only:
            cur = await db.execute(
                """
                SELECT * FROM events
                WHERE user_id = ? AND status = 'scheduled' AND event_date >= date('now')
                ORDER BY event_date ASC, id ASC
                """,
                (user_id,),
            )
        else:
            cur = await db.execute(
                """
                SELECT * FROM events
                WHERE user_id = ?
                ORDER BY event_date DESC, id DESC
                LIMIT 50
                """,
                (user_id,),
            )
        return [dict(r) for r in await cur.fetchall()]


async def list_user_upcoming_events(user_id: int) -> list[dict[str, Any]]:
    return await list_user_events(user_id, active_only=True)


async def expire_past_events() -> int:
    """Mark past scheduled events as expired so they disappear from lists."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE events
            SET status = 'expired', updated_at = datetime('now')
            WHERE status = 'scheduled' AND event_date < date('now')
            """
        )
        await db.commit()
        return cur.rowcount


async def events_needing_week_reminder() -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT e.*, ut.topic_id, ut.display_name
            FROM events e
            LEFT JOIN user_topics ut ON ut.user_id = e.user_id
            WHERE e.status = 'scheduled'
              AND e.week_reminder_sent = 0
              AND e.event_date = date('now', '+7 days')
            ORDER BY e.event_date ASC
            """
        )
        return [dict(r) for r in await cur.fetchall()]


async def mark_week_reminder_sent(event_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE events
            SET week_reminder_sent = 1, updated_at = datetime('now')
            WHERE id = ?
            """,
            (event_id,),
        )
        await db.commit()


async def format_user_events_text(user_id: int) -> str:
    pending = await list_user_pending_requests(user_id)
    events = await list_user_upcoming_events(user_id)
    lines: list[str] = []

    if pending:
        lines.append("⏳ <b>Заявки на согласовании:</b>\n")
        for r in pending:
            d = format_event_date(r["event_date"])
            lines.append(f"• #{r['id']} {r['description']} ({d})")
        lines.append("")

    if events:
        lines.append(f"📅 <b>Запланировано {len(events)} мероприятий:</b>\n")
        for i, ev in enumerate(events, 1):
            d = format_event_date(ev["event_date"])
            line = f"{i}. {ev['title']} ({d})"
            if ev.get("notes"):
                line += f"\n   {ev['notes']}"
            lines.append(line)
    elif not pending:
        return (
            "У вас нет запланированных мероприятий.\n\n"
            "Нажмите «📝 Заявка на мероприятие» — опишите мероприятие и укажите дату."
        )

    return "\n".join(lines)


async def create_event_request(user_id: int, description: str, event_date: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO event_requests (user_id, description, event_date)
            VALUES (?, ?, ?)
            """,
            (user_id, description.strip(), event_date),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_event_request(request_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM event_requests WHERE id = ?", (request_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_event_request(
    request_id: int,
    *,
    status: str | None = None,
    reject_reason: str | None = None,
    event_id: int | None = None,
    group_message_id: int | None = None,
    topic_id: int | None = None,
) -> bool:
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if reject_reason is not None:
        fields.append("reject_reason = ?")
        values.append(reject_reason)
    if event_id is not None:
        fields.append("event_id = ?")
        values.append(event_id)
    if group_message_id is not None:
        fields.append("group_message_id = ?")
        values.append(group_message_id)
    if topic_id is not None:
        fields.append("topic_id = ?")
        values.append(topic_id)
    if not fields:
        return False
    fields.append("updated_at = datetime('now')")
    values.append(request_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE event_requests SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await db.commit()
        return cur.rowcount > 0


async def has_pending_request(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM event_requests WHERE user_id = ? AND status = 'pending' LIMIT 1",
            (user_id,),
        )
        return await cur.fetchone() is not None


async def list_user_pending_requests(user_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM event_requests
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at ASC
            """,
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_pending_requests(limit: int = 30) -> list[dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT r.*, ut.display_name
            FROM event_requests r
            LEFT JOIN user_topics ut ON ut.user_id = r.user_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def count_pending_requests() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM event_requests WHERE status = 'pending'")
        row = await cur.fetchone()
        return int(row[0]) if row else 0
