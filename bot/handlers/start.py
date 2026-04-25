from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, Document
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.database.engine import async_session_factory
from bot.database.models import Lesson
from bot.database.repo import UserRepo, LessonRepo
from bot.keyboards.inline import timezone_keyboard
from bot.services.calendar_sync import fetch_and_parse_both, download_and_parse_both, download_and_parse_file, CalendarError
from bot.database.repo import AssignmentRepo
from bot.services.deadline_reminder import schedule_assignments
from bot.services.reminder import schedule_lessons

router = Router()


class OnboardingFSM(StatesGroup):
    waiting_timezone = State()
    waiting_calendar_url = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    repo = UserRepo(session)
    user, created = await repo.get_or_create(message.from_user.id, message.from_user.username)
    await session.commit()

    if not created and user.calendar_url:
        from bot.keyboards.inline import restart_keyboard
        await message.answer(
            "👋 С возвращением! Ты уже зарегистрирован.\n\n"
            "Команды:\n"
            "/today — пары на сегодня\n"
            "/week — расписание на неделю\n"
            "/sync — обновить расписание\n"
            "/settings — сменить часовой пояс\n\n"
            "Хочешь подключить другой календарь?",
            reply_markup=restart_keyboard(),
        )
        return

    await message.answer(
        "👋 Привет! Я буду напоминать тебе о парах в ТГУ за час до начала.\n\n"
        "Сначала выбери свой часовой пояс:",
        reply_markup=timezone_keyboard(),
    )
    await state.set_state(OnboardingFSM.waiting_timezone)


@router.callback_query(F.data == "start:restart")
async def cb_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Выбери часовой пояс:",
        reply_markup=timezone_keyboard(),
    )
    await state.set_state(OnboardingFSM.waiting_timezone)
    await callback.answer()


@router.callback_query(OnboardingFSM.waiting_timezone, F.data.startswith("tz:"))
async def cb_timezone_selected(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    tz = callback.data.split(":", 1)[1]
    repo = UserRepo(session)
    await repo.update_timezone(callback.from_user.id, tz)
    await session.commit()

    await state.update_data(timezone=tz)
    await callback.message.edit_text(
        "✅ Часовой пояс сохранён.\n\n"
        "Теперь отправь мне расписание одним из способов:\n\n"
        "🔗 <b>Ссылка на iCal</b> — вставь URL календаря с платформы ТГУ\n"
        "📎 <b>Файл .ics</b> — скачай и отправь файл напрямую\n\n"
        "📌 Как получить: платформа ТГУ → Расписание → Экспорт / iCal",
        parse_mode="HTML",
    )
    await state.set_state(OnboardingFSM.waiting_calendar_url)
    await callback.answer()


async def _save_and_schedule(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot: Bot,
    lessons_data: list[dict],
    assignments_data: list[dict],
    calendar_url: str,
) -> None:
    if not lessons_data and not assignments_data:
        await message.answer("⚠️ Календарь получен, но ничего не найдено. Проверь файл/ссылку.")
        return

    user_repo = UserRepo(session)
    await user_repo.update_calendar_url(message.from_user.id, calendar_url)
    user = await user_repo.get_by_telegram_id(message.from_user.id)

    lesson_repo = LessonRepo(session)
    lesson_count, _ = await lesson_repo.upsert_lessons(user.id, lessons_data)

    a_repo = AssignmentRepo(session)
    task_count, _ = await a_repo.upsert_from_ical(user.id, assignments_data)

    await session.commit()

    result = await session.execute(select(Lesson).where(Lesson.user_id == user.id))
    schedule_lessons(scheduler, bot, async_session_factory, list(result.scalars().all()), user)

    from bot.database.models import Assignment as AssignmentModel
    a_result = await session.execute(
        select(AssignmentModel).where(AssignmentModel.user_id == user.id, AssignmentModel.is_done == False)
    )
    schedule_assignments(scheduler, bot, async_session_factory, list(a_result.scalars().all()), user)

    await state.clear()
    summary = f"📅 Пар: <b>{lesson_count}</b>"
    if task_count:
        summary += f" · 📝 Заданий: <b>{task_count}</b>"

    await message.answer(
        f"✅ Расписание синхронизировано!\n{summary}\n\n"
        f"/today — пары на сегодня\n"
        f"/tasks — задания\n"
        f"/next — ближайшая пара\n"
        f"/sync — обновить\n"
        f"/settings — настройки",
        parse_mode="HTML",
    )


@router.message(OnboardingFSM.waiting_calendar_url, F.text)
async def msg_calendar_url(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot: Bot,
) -> None:
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Это не похоже на ссылку. Отправь URL или .ics файл.")
        return

    await message.answer("⏳ Загружаю расписание...")

    try:
        lessons_data, assignments_data = await fetch_and_parse_both(url)
    except CalendarError as e:
        await message.answer(f"❌ {e}", parse_mode="HTML")
        return
    except Exception as e:
        await message.answer(f"❌ Непредвиденная ошибка:\n<code>{e}</code>", parse_mode="HTML")
        return

    await _save_and_schedule(message, state, session, scheduler, bot, lessons_data, assignments_data, url)


@router.message(OnboardingFSM.waiting_calendar_url, F.document)
async def msg_calendar_file(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot: Bot,
) -> None:
    doc: Document = message.document

    if not doc.file_name or not doc.file_name.lower().endswith(".ics"):
        await message.answer("❌ Нужен файл с расширением <b>.ics</b>. Попробуй ещё раз.", parse_mode="HTML")
        return

    await message.answer("⏳ Читаю файл расписания...")

    try:
        lessons_data, assignments_data = await download_and_parse_both(bot, doc.file_id)
    except CalendarError as e:
        await message.answer(f"❌ {e}", parse_mode="HTML")
        return

    await _save_and_schedule(message, state, session, scheduler, bot, lessons_data, assignments_data, f"file:{doc.file_id}")


@router.message(OnboardingFSM.waiting_calendar_url)
async def msg_calendar_wrong(message: Message) -> None:
    await message.answer("❌ Отправь ссылку на iCal или .ics файл.")
