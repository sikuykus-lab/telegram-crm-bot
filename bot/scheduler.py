"""Background jobs: expire past events, week-before reminders."""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db import (
    events_needing_week_reminder,
    expire_past_events,
    format_event_date,
    mark_week_reminder_sent,
)

log = logging.getLogger("crm_bot.scheduler")


async def job_expire_events() -> None:
    n = await expire_past_events()
    if n:
        log.info("Просрочено мероприятий: %s", n)


async def job_week_reminders(bot: Bot, group_id: int) -> None:
    events = await events_needing_week_reminder()
    if not events:
        return

    for ev in events:
        title = ev["title"]
        d = format_event_date(ev["event_date"])
        uid = ev["user_id"]
        name = ev.get("display_name") or str(uid)
        notes = (ev.get("notes") or "").strip()
        notes_line = f"\n📝 {notes}" if notes else ""

        text = (
            f"⏰ <b>Напоминание за неделю</b>\n\n"
            f"Мероприятие: <b>{title}</b>\n"
            f"Дата: <b>{d}</b>\n"
            f"Клиент: {name}\n"
            f"<code>user_id={uid}</code>{notes_line}"
        )

        topic_id = ev.get("topic_id")
        sent = False
        if topic_id:
            try:
                await bot.send_message(
                    chat_id=group_id,
                    message_thread_id=topic_id,
                    text=text,
                    parse_mode="HTML",
                )
                sent = True
            except Exception as e:
                log.warning("Reminder to topic %s failed: %s", topic_id, e)

        if not sent:
            try:
                await bot.send_message(
                    chat_id=group_id,
                    text=text + "\n\n<i>Топик клиента не найден — создайте диалог с клиентом.</i>",
                    parse_mode="HTML",
                )
                sent = True
            except Exception as e:
                log.error("Reminder to group failed for event %s: %s", ev["id"], e)

        if sent:
            await mark_week_reminder_sent(int(ev["id"]))
            log.info("Week reminder sent for event %s (user %s)", ev["id"], uid)

            try:
                await bot.send_message(
                    chat_id=uid,
                    text=(
                        f"⏰ Напоминание: через неделю — <b>{title}</b> ({d}).\n"
                        f"Если нужно что-то уточнить — напишите нам здесь."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass


def start_scheduler(bot: Bot, group_id: int, tz_name: str = "Europe/Moscow") -> AsyncIOScheduler:
    tz = ZoneInfo(tz_name)
    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(job_expire_events, CronTrigger(hour=0, minute=5, timezone=tz), id="expire_events")
    scheduler.add_job(
        job_week_reminders,
        CronTrigger(hour=9, minute=0, timezone=tz),
        args=[bot, group_id],
        id="week_reminders",
    )
    scheduler.start()
    log.info("Scheduler started (tz=%s)", tz_name)
    return scheduler
