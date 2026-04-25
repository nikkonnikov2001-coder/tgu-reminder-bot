from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from bot.database.models import Assignment, User, DEFAULT_REMINDER_OFFSETS
from bot.services.timezone import utc_to_tz, format_time, format_date

logger = logging.getLogger(__name__)


def build_deadline_text(assignment: Assignment, user: User, minutes_before: int) -> str:
    if minutes_before < 60:
        header = f"📝 <b>Через {minutes_before} минут дедлайн!</b>"
    elif minutes_before == 60:
        header = "📝 <b>Через час дедлайн!</b>"
    elif minutes_before < 1440:
        hours = minutes_before // 60
        header = f"📝 <b>Через {hours} {'час' if hours == 1 else 'часа' if hours < 5 else 'часов'} дедлайн!</b>"
    else:
        days = minutes_before // 1440
        header = f"📝 <b>Через {days} {'день' if days == 1 else 'дня' if days < 5 else 'дней'} дедлайн!</b>"

    lines = [header, "", f"📚 <b>Предмет:</b> {assignment.subject}"]

    if assignment.description:
        lines.append(f"📋 <b>Задание:</b> {assignment.description}")

    if assignment.deadline_utc:
        local_dt = utc_to_tz(assignment.deadline_utc, user.timezone)
        msk_dt = utc_to_tz(assignment.deadline_utc, "Europe/Moscow")
        lines.append(f"⏰ <b>Дедлайн:</b> {format_date(local_dt)} {format_time(local_dt)}")
        if user.timezone != "Europe/Moscow":
            lines.append(f"⏰ <b>МСК:</b> {format_date(msk_dt)} {format_time(msk_dt)}")

    return "\n".join(lines)


def schedule_assignments(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    session_factory,
    assignments: list[Assignment],
    user: User,
) -> None:
    now = datetime.utcnow()
    offsets = user.reminder_offsets or DEFAULT_REMINDER_OFFSETS
    # Для заданий добавляем также напоминание за 1 день (1440 мин) если его нет в настройках
    task_offsets = sorted(set(offsets + [1440]), reverse=True)
    last_offset = min(task_offsets)

    for assignment in assignments:
        if not assignment.deadline_utc or assignment.is_done:
            continue

        for minutes in task_offsets:
            remind_at = assignment.deadline_utc - timedelta(minutes=minutes)
            if remind_at <= now:
                continue

            job_id = f"deadline_{minutes}m_{assignment.id}_{user.telegram_id}"
            scheduler.add_job(
                _deadline_job,
                trigger=DateTrigger(run_date=remind_at),
                id=job_id,
                kwargs={
                    "bot": bot,
                    "session_factory": session_factory,
                    "assignment_id": assignment.id,
                    "user_telegram_id": user.telegram_id,
                    "minutes_before": minutes,
                    "is_last": minutes == last_offset,
                },
                replace_existing=True,
                misfire_grace_time=min(minutes * 60, 600),
            )


async def _deadline_job(
    bot: Bot,
    session_factory,
    assignment_id: int,
    user_telegram_id: int,
    minutes_before: int,
    is_last: bool = False,
) -> None:
    async with session_factory() as session:
        result = await session.execute(select(Assignment).where(Assignment.id == assignment_id))
        assignment = result.scalar_one_or_none()
        if not assignment or assignment.is_done:
            return

        result = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        text = build_deadline_text(assignment, user, minutes_before)
        try:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            logger.warning("User %s blocked the bot", user_telegram_id)
        except Exception as e:
            logger.error("Failed to send deadline reminder %s: %s", assignment_id, e)
