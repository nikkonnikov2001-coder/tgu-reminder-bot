from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import async_session_factory
from bot.database.models import Lesson
from bot.database.repo import UserRepo, LessonRepo
from bot.keyboards.inline import timezone_keyboard, reminder_offsets_keyboard, REMINDER_OPTIONS
from bot.services.reminder import schedule_lessons
from bot.services.timezone import RUSSIAN_TIMEZONES

router = Router()


class SettingsFSM(StatesGroup):
    waiting_timezone = State()


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext, session: AsyncSession) -> None:
    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    current_label = next((label for label, tz in RUSSIAN_TIMEZONES if tz == user.timezone), user.timezone)

    await message.answer(
        f"⚙️ <b>Настройки</b>\n\n"
        f"Текущий часовой пояс: <b>{current_label}</b>\n\n"
        f"Выбери новый:",
        parse_mode="HTML",
        reply_markup=timezone_keyboard(prefix="settings_tz"),
    )
    await state.set_state(SettingsFSM.waiting_timezone)


@router.message(Command("stop"))
async def cmd_stop(
    message: Message, session: AsyncSession, scheduler: AsyncIOScheduler
) -> None:
    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return
    if not user.is_active:
        await message.answer("ℹ️ Напоминания уже отключены. Используй /resume чтобы включить.")
        return

    await repo.set_active(message.from_user.id, False)
    await session.commit()

    # Удаляем все запланированные jobs этого пользователя
    for job in scheduler.get_jobs():
        if f"_{user.telegram_id}" in job.id:
            scheduler.remove_job(job.id)

    await message.answer(
        "🔕 Напоминания отключены.\n"
        "Используй /resume чтобы включить снова."
    )


@router.message(Command("resume"))
async def cmd_resume(
    message: Message,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot,
) -> None:
    from sqlalchemy import select as sa_select
    from bot.database.models import Lesson
    from bot.database.engine import async_session_factory
    from bot.services.reminder import schedule_lessons

    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return
    if user.is_active:
        await message.answer("ℹ️ Напоминания уже включены.")
        return

    await repo.set_active(message.from_user.id, True)
    await session.commit()

    result = await session.execute(
        sa_select(Lesson).where(Lesson.user_id == user.id, Lesson.reminded == False)
    )
    lessons = list(result.scalars().all())
    schedule_lessons(scheduler, bot, async_session_factory, lessons, user)

    await message.answer(
        "🔔 Напоминания включены!\n"
        f"Запланировано пар: <b>{len(lessons)}</b>",
        parse_mode="HTML",
    )


@router.callback_query(SettingsFSM.waiting_timezone, F.data.startswith("settings_tz:"))
async def cb_settings_timezone(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    tz = callback.data.split(":", 1)[1]
    repo = UserRepo(session)
    await repo.update_timezone(callback.from_user.id, tz)
    await session.commit()

    label = next((label for label, t in RUSSIAN_TIMEZONES if t == tz), tz)
    await callback.message.edit_text(
        f"✅ Часовой пояс изменён на <b>{label}</b>\n\n"
        f"Все напоминания теперь будут показывать время в этом поясе.",
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


def _offsets_label(offsets: list[int]) -> str:
    labels = {m: l for m, l in REMINDER_OPTIONS}
    parts = [labels.get(m, f"{m} мин") for m in sorted(offsets, reverse=True)]
    return ", ".join(parts) if parts else "не выбрано"


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, state: FSMContext, session: AsyncSession) -> None:
    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    current = user.reminder_offsets or [60, 15]
    await state.update_data(offsets=current)
    await message.answer(
        f"⏰ <b>Напоминания о парах</b>\n\n"
        f"Текущие: <b>{_offsets_label(current)}</b>\n\n"
        f"Выбери когда получать напоминания (можно несколько):",
        parse_mode="HTML",
        reply_markup=reminder_offsets_keyboard(current),
    )


@router.callback_query(F.data.startswith("rem_toggle:"))
async def cb_rem_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    minutes = int(callback.data.split(":")[1])
    data = await state.get_data()
    offsets: list[int] = list(data.get("offsets", [60, 15]))

    if minutes in offsets:
        offsets.remove(minutes)
    else:
        offsets.append(minutes)

    await state.update_data(offsets=offsets)
    await callback.message.edit_reply_markup(reply_markup=reminder_offsets_keyboard(offsets))
    await callback.answer()


@router.callback_query(F.data == "rem_save")
async def cb_rem_save(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    scheduler,
    bot,
) -> None:
    data = await state.get_data()
    offsets: list[int] = data.get("offsets", [60, 15])

    if not offsets:
        await callback.answer("⚠️ Выбери хотя бы один вариант!", show_alert=True)
        return

    repo = UserRepo(session)
    await repo.update_reminder_offsets(callback.from_user.id, offsets)
    user = await repo.get_by_telegram_id(callback.from_user.id)
    await session.commit()

    # Пересчитываем jobs под новые офсеты
    from sqlalchemy import select
    result = await session.execute(
        select(Lesson).where(Lesson.user_id == user.id, Lesson.reminded == False)
    )
    lessons = list(result.scalars().all())

    # Удаляем старые jobs этого пользователя
    for job in scheduler.get_jobs():
        if f"_{user.telegram_id}" in job.id:
            scheduler.remove_job(job.id)

    schedule_lessons(scheduler, bot, async_session_factory, lessons, user)

    await state.clear()
    await callback.message.edit_text(
        f"✅ Сохранено! Напоминания: <b>{_offsets_label(offsets)}</b>\n\n"
        f"Все напоминания пересчитаны.",
        parse_mode="HTML",
    )
    await callback.answer()
