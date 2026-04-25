import logging
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from bot.database.engine import async_session_factory
from bot.database.models import User, Lesson
from bot.database.repo import LessonRepo
from bot.services.calendar_sync import fetch_and_parse
from bot.services.reminder import schedule_lessons

logger = logging.getLogger(__name__)


async def sync_all_users(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    logger.info("Auto-sync started")
    synced = 0
    failed = 0

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.is_active == True))
        users = list(result.scalars().all())

        for user in users:
            if not user.calendar_url or user.calendar_url.startswith("file:"):
                continue
            try:
                lessons_data = await fetch_and_parse(user.calendar_url)
                lesson_repo = LessonRepo(session)
                _, cancelled = await lesson_repo.upsert_lessons(user.id, lessons_data)
                await session.commit()
                if cancelled:
                    names = "\n".join(f"• {l.subject}" for l in cancelled[:5])
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            f"❌ <b>Обновление расписания:</b> отменены пары:\n{names}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

                lessons_result = await session.execute(
                    select(Lesson).where(Lesson.user_id == user.id, Lesson.reminded == False)
                )
                lessons = list(lessons_result.scalars().all())
                schedule_lessons(scheduler, bot, async_session_factory, lessons, user)
                synced += 1
            except Exception as e:
                logger.error("Auto-sync failed for user %s: %s", user.telegram_id, e)
                failed += 1

    logger.info("Auto-sync done: synced=%d, failed=%d", synced, failed)


def setup_autosync(scheduler: AsyncIOScheduler, bot: Bot) -> None:
    scheduler.add_job(
        sync_all_users,
        trigger=CronTrigger(hour=3, minute=0, timezone="Europe/Moscow"),
        id="autosync_all_users",
        kwargs={"bot": bot, "scheduler": scheduler},
        replace_existing=True,
        misfire_grace_time=600,
    )
    logger.info("Auto-sync scheduled at 03:00 MSK daily")
