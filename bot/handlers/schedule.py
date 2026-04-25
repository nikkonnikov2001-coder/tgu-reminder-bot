from datetime import date, timedelta
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import async_session_factory
from bot.database.models import Lesson, User
from bot.database.repo import LessonRepo, UserRepo
from bot.keyboards.inline import week_navigation_keyboard, update_file_keyboard, refresh_today_keyboard
from bot.services.calendar_sync import fetch_and_parse, download_and_parse_file, CalendarError
from bot.services.reminder import schedule_lessons


class SyncFSM(StatesGroup):
    waiting_new_file = State()
from bot.services.timezone import utc_to_tz, format_time, format_date

router = Router()


def render_lesson(lesson: Lesson, user: User) -> str:
    local_dt = utc_to_tz(lesson.start_dt_utc, user.timezone)
    msk_dt = utc_to_tz(lesson.start_dt_utc, "Europe/Moscow")
    end_local = utc_to_tz(lesson.end_dt_utc, user.timezone)

    parts = [f"🕐 <b>{format_time(local_dt)}–{format_time(end_local)}</b>"]
    if user.timezone != "Europe/Moscow":
        parts[0] += f" (МСК {format_time(msk_dt)})"
    parts.append(f"📚 {lesson.subject}")
    if lesson.teacher_name:
        parts.append(f"👤 {lesson.teacher_name}")
    if lesson.room:
        parts.append(f"📍 {lesson.room}")
    if lesson.conference_url:
        parts.append(f'🔗 <a href="{lesson.conference_url}">Конференция</a>')
    return "\n".join(parts)


@router.message(Command("today"))
async def cmd_today(message: Message, session: AsyncSession) -> None:
    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not user or not user.calendar_url:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    today = date.today()
    lesson_repo = LessonRepo(session)
    lessons = await lesson_repo.get_lessons_for_date(user.id, today)

    if not lessons:
        await message.answer(
            f"📭 На сегодня (<b>{format_date_simple(today)}</b>) пар нет.",
            parse_mode="HTML",
            reply_markup=refresh_today_keyboard(),
        )
        return

    header = f"📅 <b>Пары на сегодня ({format_date_simple(today)})</b>\n\n"
    blocks = [render_lesson(l, user) for l in lessons]
    await message.answer(
        header + "\n\n".join(blocks),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=refresh_today_keyboard(),
    )


@router.message(Command("week"))
async def cmd_week(message: Message, session: AsyncSession) -> None:
    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not user or not user.calendar_url:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)

    lesson_repo = LessonRepo(session)
    lessons = await lesson_repo.get_lessons_for_week(user.id, week_start, week_end)

    if not lessons:
        await message.answer("📭 На этой неделе пар нет.")
        return

    days: dict[date, list[Lesson]] = {}
    for lesson in lessons:
        d = lesson.start_dt_utc.date()
        days.setdefault(d, []).append(lesson)

    sorted_days = sorted(days.keys())
    await _send_week_day(message, user, sorted_days, days, 0)


async def _send_week_day(
    target: Message | CallbackQuery,
    user: User,
    sorted_days: list[date],
    days: dict[date, list[Lesson]],
    offset: int,
) -> None:
    day = sorted_days[offset]
    lessons = days[day]
    header = f"📅 <b>{format_date_simple(day)}</b> ({offset + 1}/{len(sorted_days)})\n\n"
    blocks = [render_lesson(l, user) for l in lessons]
    text = header + "\n\n".join(blocks)
    kb = week_navigation_keyboard(offset, len(sorted_days))

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        await target.answer()
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("week_nav:"))
async def cb_week_nav(callback: CallbackQuery, session: AsyncSession) -> None:
    offset = int(callback.data.split(":")[1])

    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)

    lesson_repo = LessonRepo(session)
    lessons = await lesson_repo.get_lessons_for_week(user.id, week_start, week_end)

    days: dict[date, list[Lesson]] = {}
    for lesson in lessons:
        d = lesson.start_dt_utc.date()
        days.setdefault(d, []).append(lesson)

    sorted_days = sorted(days.keys())
    if offset >= len(sorted_days):
        await callback.answer("Нет данных", show_alert=True)
        return

    await _send_week_day(callback, user, sorted_days, days, offset)


@router.callback_query(F.data == "refresh:today")
async def cb_refresh_today(callback: CallbackQuery, session: AsyncSession) -> None:
    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    today = date.today()
    lesson_repo = LessonRepo(session)
    lessons = await lesson_repo.get_lessons_for_date(user.id, today)

    if not lessons:
        await callback.message.edit_text(
            f"📭 На сегодня (<b>{format_date_simple(today)}</b>) пар нет.",
            parse_mode="HTML",
            reply_markup=refresh_today_keyboard(),
        )
    else:
        header = f"📅 <b>Пары на сегодня ({format_date_simple(today)})</b>\n\n"
        blocks = [render_lesson(l, user) for l in lessons]
        await callback.message.edit_text(
            header + "\n\n".join(blocks),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=refresh_today_keyboard(),
        )
    await callback.answer("Обновлено")


@router.message(Command("next"))
async def cmd_next(message: Message, session: AsyncSession) -> None:
    from datetime import datetime as dt
    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not user or not user.calendar_url:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    now_utc = dt.utcnow()
    result = await session.execute(
        select(Lesson)
        .where(Lesson.user_id == user.id, Lesson.start_dt_utc > now_utc)
        .order_by(Lesson.start_dt_utc)
        .limit(1)
    )
    lesson = result.scalar_one_or_none()

    if not lesson:
        await message.answer("📭 Ближайших пар не найдено.")
        return

    from bot.services.timezone import utc_to_tz, format_time, format_date
    local_dt = utc_to_tz(lesson.start_dt_utc, user.timezone)
    msk_dt = utc_to_tz(lesson.start_dt_utc, "Europe/Moscow")

    delta = lesson.start_dt_utc - now_utc
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes = remainder // 60

    if hours > 0:
        time_left = f"{hours} ч {minutes} мин"
    else:
        time_left = f"{minutes} мин"

    lines = [
        f"⏭ <b>Ближайшая пара</b> — через {time_left}",
        f"📅 {format_date(local_dt)}",
        "",
        f"📚 <b>{lesson.subject}</b>",
    ]
    if lesson.teacher_name:
        lines.append(f"👤 {lesson.teacher_name}")
    lines.append(f"🕐 {format_time(local_dt)}")
    if user.timezone != "Europe/Moscow":
        lines.append(f"🕐 МСК: {format_time(msk_dt)}")
    if lesson.room:
        lines.append(f"📍 {lesson.room}")
    if lesson.conference_url:
        lines.append(f'🔗 <a href="{lesson.conference_url}">Подключиться</a>')

    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("sync"))
async def cmd_sync(
    message: Message,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot,
) -> None:
    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not user or not user.calendar_url:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    if user.calendar_url.startswith("file:"):
        await message.answer(
            "📎 Твоё расписание было загружено файлом.\n"
            "Нажми кнопку ниже чтобы загрузить новый файл:",
            parse_mode="HTML",
            reply_markup=update_file_keyboard(),
        )
        return

    await message.answer("⏳ Обновляю расписание...")

    try:
        lessons_data = await fetch_and_parse(user.calendar_url)
    except CalendarError as e:
        await message.answer(f"❌ {e}", parse_mode="HTML")
        return
    except Exception as e:
        await message.answer(f"❌ Непредвиденная ошибка:\n<code>{e}</code>", parse_mode="HTML")
        return

    lesson_repo = LessonRepo(session)
    count, cancelled = await lesson_repo.upsert_lessons(user.id, lessons_data)
    await session.commit()

    result = await session.execute(select(Lesson).where(Lesson.user_id == user.id))
    lessons = list(result.scalars().all())
    schedule_lessons(scheduler, bot, async_session_factory, lessons, user)

    text = f"✅ Синхронизировано. Новых пар: <b>{count}</b>"
    if cancelled:
        names = "\n".join(f"• {l.subject}" for l in cancelled[:5])
        text += f"\n\n❌ <b>Отменены {len(cancelled)} пар:</b>\n{names}"
    await message.answer(text, parse_mode="HTML")


@router.message(F.document)
async def msg_ics_file(
    message: Message,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot,
) -> None:
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".ics"):
        return  # не наш файл — игнорируем

    user_repo = UserRepo(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    await message.answer("⏳ Читаю файл расписания...")

    try:
        lessons_data = await download_and_parse_file(bot, doc.file_id)
    except CalendarError as e:
        await message.answer(f"❌ {e}", parse_mode="HTML")
        return

    await user_repo.update_calendar_url(message.from_user.id, f"file:{doc.file_id}")
    lesson_repo = LessonRepo(session)
    count, cancelled = await lesson_repo.upsert_lessons(user.id, lessons_data)
    await session.commit()

    result = await session.execute(select(Lesson).where(Lesson.user_id == user.id))
    lessons = list(result.scalars().all())
    schedule_lessons(scheduler, bot, async_session_factory, lessons, user)

    text = f"✅ Расписание обновлено из файла! Загружено пар: <b>{count}</b>"
    if cancelled:
        names = "\n".join(f"• {l.subject}" for l in cancelled[:5])
        text += f"\n\n❌ <b>Отменены {len(cancelled)} пар:</b>\n{names}"
    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "sync:upload_file")
async def cb_upload_file(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "📎 Отправь новый <b>.ics файл</b> с расписанием:",
        parse_mode="HTML",
    )
    await state.set_state(SyncFSM.waiting_new_file)
    await callback.answer()


@router.message(SyncFSM.waiting_new_file, F.document)
async def msg_sync_new_file(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot: Bot,
) -> None:
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".ics"):
        await message.answer("❌ Нужен файл с расширением <b>.ics</b>. Попробуй ещё раз.", parse_mode="HTML")
        return

    await message.answer("⏳ Читаю файл...")

    try:
        lessons_data = await download_and_parse_file(bot, doc.file_id)
    except CalendarError as e:
        await message.answer(f"❌ {e}", parse_mode="HTML")
        return

    user_repo = UserRepo(session)
    await user_repo.update_calendar_url(message.from_user.id, f"file:{doc.file_id}")
    user = await user_repo.get_by_telegram_id(message.from_user.id)

    lesson_repo = LessonRepo(session)
    count, cancelled = await lesson_repo.upsert_lessons(user.id, lessons_data)
    await session.commit()

    result = await session.execute(select(Lesson).where(Lesson.user_id == user.id))
    lessons = list(result.scalars().all())
    schedule_lessons(scheduler, bot, async_session_factory, lessons, user)

    await state.clear()
    text = f"✅ Расписание обновлено из файла! Загружено пар: <b>{count}</b>"
    if cancelled:
        names = "\n".join(f"• {l.subject}" for l in cancelled[:5])
        text += f"\n\n❌ <b>Отменены {len(cancelled)} пар:</b>\n{names}"
    await message.answer(text, parse_mode="HTML")


@router.message(SyncFSM.waiting_new_file)
async def msg_sync_wrong(message: Message) -> None:
    await message.answer("❌ Отправь .ics файл или нажми /sync чтобы отменить.")


def format_date_simple(d: date) -> str:
    months = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return f"{days[d.weekday()]}, {d.day} {months[d.month - 1]}"
