import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.config import settings
from bot.database.engine import engine, async_session_factory
from bot.database.models import Base, Lesson, User
from bot.handlers import start, schedule, admin, help as help_handler, assignments
from bot.handlers import settings as settings_handler
from bot.middleware import DbSessionMiddleware, SchedulerMiddleware
from bot.services.autosync import setup_autosync
from bot.services.reminder import schedule_lessons

import os
from logging.handlers import RotatingFileHandler

os.makedirs("logs", exist_ok=True)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_file_handler = RotatingFileHandler(
    "logs/bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])
logger = logging.getLogger(__name__)


async def setup_bot_commands(bot: Bot) -> None:
    user_commands = [
        BotCommand(command="today",     description="📅 Пары на сегодня"),
        BotCommand(command="week",      description="🗓 Расписание на неделю"),
        BotCommand(command="next",      description="⏭ Ближайшая пара"),
        BotCommand(command="tasks",     description="📝 Список заданий"),
        BotCommand(command="addtask",   description="➕ Добавить задание"),
        BotCommand(command="sync",      description="🔄 Обновить расписание и задания"),
        BotCommand(command="reminders", description="⏰ Настроить время напоминаний"),
        BotCommand(command="settings",  description="⚙️ Сменить часовой пояс"),
        BotCommand(command="stop",      description="🔕 Отключить напоминания"),
        BotCommand(command="resume",    description="🔔 Включить напоминания"),
        BotCommand(command="help",      description="📖 Все команды"),
        BotCommand(command="start",     description="🚀 Подключить календарь"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="stats",        description="📊 Статистика"),
        BotCommand(command="broadcast",    description="📢 Рассылка"),
        BotCommand(command="addphoto",     description="📸 Фото преподавателя"),
        BotCommand(command="listteachers", description="👤 Список преподавателей"),
    ]

    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            pass
    logger.info("Bot commands set")


async def restore_scheduled_reminders(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.is_active == True)
        )
        users = list(result.scalars().all())
        for user in users:
            lessons_result = await session.execute(
                select(Lesson).where(Lesson.user_id == user.id, Lesson.reminded == False)
            )
            lessons = list(lessons_result.scalars().all())
            schedule_lessons(scheduler, bot, async_session_factory, lessons, user)
    logger.info("Restored reminders for %d users", len(users))


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Миграция: добавляем колонку reminder_offsets если её нет
        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE users ADD COLUMN reminder_offsets TEXT DEFAULT '[60, 15]'"
                )
            )
            logger.info("Migration: added reminder_offsets column")
        except Exception:
            pass
        try:
            await conn.execute(__import__("sqlalchemy").text("""
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    uid VARCHAR(512),
                    subject VARCHAR(512) NOT NULL,
                    description TEXT,
                    deadline_utc DATETIME,
                    is_done BOOLEAN NOT NULL DEFAULT 0,
                    is_manual BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            logger.info("Migration: created assignments table")
        except Exception:
            pass

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start()

    dp = Dispatcher()

    db_middleware = DbSessionMiddleware(async_session_factory)
    scheduler_middleware = SchedulerMiddleware(scheduler, bot)

    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)
    dp.message.middleware(scheduler_middleware)
    dp.callback_query.middleware(scheduler_middleware)

    dp.include_router(start.router)
    dp.include_router(schedule.router)
    dp.include_router(settings_handler.router)
    dp.include_router(assignments.router)
    dp.include_router(help_handler.router)
    dp.include_router(admin.router)

    await setup_bot_commands(bot)
    await restore_scheduled_reminders(bot, scheduler)
    setup_autosync(scheduler, bot)

    logger.info("Bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
