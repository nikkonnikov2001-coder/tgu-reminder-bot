from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Lesson, User
from bot.database.repo import LessonRepo, TeacherRepo, UserRepo
from bot.services.timezone import utc_to_moscow, utc_to_tz, format_time

logger = logging.getLogger(__name__)


def build_reminder_text(lesson: Lesson, user: User, minutes_before: int = 60) -> str:
    msk_time = format_time(utc_to_moscow(lesson.start_dt_utc))
    local_time = format_time(utc_to_tz(lesson.start_dt_utc, user.timezone))

    if minutes_before < 60:
        header = f"⚡️ <b>Через {minutes_before} минут пара!</b>"
    elif minutes_before == 60:
        header = "🔔 <b>Через час пара!</b>"
    else:
        hours = minutes_before // 60
        header = f"🔔 <b>Через {hours} {'час' if hours == 1 else 'часа' if hours < 5 else 'часов'} пара!</b>"

    lines = [
        header,
        "",
        f"📚 <b>Предмет:</b> {lesson.subject}",
    ]
    if lesson.teacher_name:
        lines.append(f"👤 <b>Преподаватель:</b> {lesson.teacher_name}")
    lines.append(f"🕐 <b>Начало (МСК):</b> {msk_time}")
    if user.timezone != "Europe/Moscow":
        lines.append(f"🕐 <b>Начало (ваше время):</b> {local_time}")
    if lesson.room:
        lines.append(f"📍 <b>Аудитория:</b> {lesson.room}")
    if lesson.conference_url:
        lines.append(f'🔗 <a href="{lesson.conference_url}">Подключиться к конференции</a>')

    return "\n".join(lines)


async def _send_message(
    bot: Bot,
    session: AsyncSession,
    lesson: Lesson,
    user: User,
    minutes_before: int,
) -> None:
    teacher_repo = TeacherRepo(session)
    teacher = await teacher_repo.search_by_name(lesson.teacher_name or "") if lesson.teacher_name else None
    text = build_reminder_text(lesson, user, minutes_before)

    try:
        if teacher and teacher.photo_file_id:
            await bot.send_photo(
                chat_id=user.telegram_id,
                photo=teacher.photo_file_id,
                caption=text,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except TelegramForbiddenError:
        logger.warning("User %s blocked the bot", user.telegram_id)
    except Exception as e:
        logger.error("Failed to send reminder for lesson %s: %s", lesson.id, e)


async def send_reminder(
    bot: Bot,
    session: AsyncSession,
    lesson_id: int,
    user_telegram_id: int,
    minutes_before: int = 60,
    is_last: bool = False,
) -> None:
    result = await session.execute(select(Lesson).where(Lesson.id == lesson_id))
    lesson = result.scalar_one_or_none()
    if not lesson:
        return

    result = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        return

    await _send_message(bot, session, lesson, user, minutes_before)

    if is_last:
        lesson_repo = LessonRepo(session)
        await lesson_repo.mark_reminded(lesson_id)
        await session.commit()


def schedule_lessons(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    session_factory,
    lessons: list[Lesson],
    user: User,
) -> None:
    from bot.database.models import DEFAULT_REMINDER_OFFSETS

    now = datetime.utcnow()
    offsets = user.reminder_offsets or DEFAULT_REMINDER_OFFSETS
    # Последнее напоминание (минимальный офсет) помечает пару как reminded
    last_offset = min(offsets)

    for lesson in lessons:
        for minutes in offsets:
            remind_at = lesson.start_dt_utc - timedelta(minutes=minutes)
            if remind_at <= now:
                continue

            job_id = f"reminder_{minutes}m_{lesson.id}_{user.telegram_id}"
            scheduler.add_job(
                _reminder_job,
                trigger=DateTrigger(run_date=remind_at),
                id=job_id,
                kwargs={
                    "bot": bot,
                    "session_factory": session_factory,
                    "lesson_id": lesson.id,
                    "user_telegram_id": user.telegram_id,
                    "minutes_before": minutes,
                    "is_last": minutes == last_offset,
                },
                replace_existing=True,
                misfire_grace_time=min(minutes * 60, 600),
            )


async def _reminder_job(
    bot: Bot,
    session_factory,
    lesson_id: int,
    user_telegram_id: int,
    minutes_before: int = 60,
    is_last: bool = False,
) -> None:
    async with session_factory() as session:
        await send_reminder(bot, session, lesson_id, user_telegram_id, minutes_before, is_last)
