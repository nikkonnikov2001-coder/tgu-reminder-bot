from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import async_session_factory
from bot.database.models import Assignment
from bot.database.repo import UserRepo, AssignmentRepo
from bot.services.deadline_reminder import schedule_assignments
from bot.services.timezone import utc_to_tz, format_time, format_date

router = Router()


class AddTaskFSM(StatesGroup):
    waiting_subject = State()
    waiting_description = State()
    waiting_deadline = State()


# ─── Вспомогательные ────────────────────────────────────────────────

def _render_assignment(a: Assignment, user_tz: str) -> str:
    status = "✅" if a.is_done else "🔴"
    lines = [f"{status} <b>{a.subject}</b>"]
    if a.description:
        lines.append(f"📋 {a.description}")
    if a.deadline_utc:
        local = utc_to_tz(a.deadline_utc, user_tz)
        lines.append(f"⏰ Дедлайн: {format_date(local)} {format_time(local)}")
    if a.is_manual:
        lines.append("✏️ <i>Добавлено вручную</i>")
    return "\n".join(lines)


def _parse_deadline(text: str) -> datetime | None:
    """Парсит дату в форматах ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ"""
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ─── Список заданий ─────────────────────────────────────────────────

@router.message(Command("tasks"))
async def cmd_tasks(message: Message, session: AsyncSession) -> None:
    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    a_repo = AssignmentRepo(session)
    assignments = await a_repo.get_active(user.id)

    if not assignments:
        await message.answer(
            "📭 Активных заданий нет.\n"
            "Добавь вручную: /addtask\n"
            "Или синхронизируй расписание: /sync"
        )
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for a in assignments:
        deadline_str = ""
        if a.deadline_utc:
            local = utc_to_tz(a.deadline_utc, user.timezone)
            deadline_str = f" · {format_date(local)}"
        builder.button(
            text=f"✅ {a.subject[:30]}{deadline_str}",
            callback_data=f"task_done:{a.id}",
        )
    builder.adjust(1)

    blocks = [_render_assignment(a, user.timezone) for a in assignments]
    text = f"📝 <b>Задания ({len(assignments)})</b>\n\n" + "\n\n".join(blocks)
    text += "\n\n<i>Нажми кнопку чтобы отметить выполненным:</i>"

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("task_done:"))
async def cb_task_done(callback: CallbackQuery, session: AsyncSession) -> None:
    assignment_id = int(callback.data.split(":")[1])
    a_repo = AssignmentRepo(session)
    assignment = await a_repo.get_by_id(assignment_id)

    if not assignment:
        await callback.answer("Задание не найдено", show_alert=True)
        return

    await a_repo.mark_done(assignment_id)
    await session.commit()

    await callback.answer(f"✅ «{assignment.subject}» выполнено!")

    # Обновляем список
    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(callback.from_user.id)
    assignments = await a_repo.get_active(user.id)

    if not assignments:
        await callback.message.edit_text("✅ Все задания выполнены! Так держать 🎉")
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for a in assignments:
        deadline_str = ""
        if a.deadline_utc:
            local = utc_to_tz(a.deadline_utc, user.timezone)
            deadline_str = f" · {format_date(local)}"
        builder.button(
            text=f"✅ {a.subject[:30]}{deadline_str}",
            callback_data=f"task_done:{a.id}",
        )
    builder.adjust(1)

    blocks = [_render_assignment(a, user.timezone) for a in assignments]
    text = f"📝 <b>Задания ({len(assignments)})</b>\n\n" + "\n\n".join(blocks)
    text += "\n\n<i>Нажми кнопку чтобы отметить выполненным:</i>"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ─── Добавить задание вручную ────────────────────────────────────────

@router.message(Command("addtask"))
async def cmd_addtask(message: Message, state: FSMContext, session: AsyncSession) -> None:
    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❗ Сначала настрой бота командой /start")
        return

    await message.answer(
        "📝 <b>Новое задание</b>\n\n"
        "Введи название предмета:",
        parse_mode="HTML",
    )
    await state.set_state(AddTaskFSM.waiting_subject)


@router.message(AddTaskFSM.waiting_subject, F.text)
async def msg_task_subject(message: Message, state: FSMContext) -> None:
    await state.update_data(subject=message.text.strip())
    await message.answer(
        "📋 Опиши задание (или отправь /skip чтобы пропустить):"
    )
    await state.set_state(AddTaskFSM.waiting_description)


@router.message(AddTaskFSM.waiting_description, F.text)
async def msg_task_description(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    description = None if text == "/skip" else text
    await state.update_data(description=description)
    await message.answer(
        "⏰ Укажи дедлайн в формате <b>ДД.ММ.ГГГГ</b> или <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
        "(или /skip — без дедлайна):",
        parse_mode="HTML",
    )
    await state.set_state(AddTaskFSM.waiting_deadline)


@router.message(AddTaskFSM.waiting_deadline, F.text)
async def msg_task_deadline(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    scheduler: AsyncIOScheduler,
    bot: Bot,
) -> None:
    text = message.text.strip()
    deadline_utc = None

    if text != "/skip":
        deadline_utc = _parse_deadline(text)
        if not deadline_utc:
            await message.answer(
                "❌ Не могу распознать дату. Используй формат <b>ДД.ММ.ГГГГ</b> или <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>",
                parse_mode="HTML",
            )
            return

    data = await state.get_data()
    await state.clear()

    repo = UserRepo(session)
    user = await repo.get_by_telegram_id(message.from_user.id)

    a_repo = AssignmentRepo(session)
    assignment = await a_repo.add_manual(
        user_id=user.id,
        subject=data["subject"],
        description=data.get("description"),
        deadline_utc=deadline_utc,
    )
    await session.commit()

    if deadline_utc:
        schedule_assignments(scheduler, bot, async_session_factory, [assignment], user)

    deadline_str = ""
    if deadline_utc:
        local = utc_to_tz(deadline_utc, user.timezone)
        deadline_str = f"\n⏰ Дедлайн: {format_date(local)} {format_time(local)}"

    await message.answer(
        f"✅ Задание добавлено!\n\n"
        f"📚 <b>{data['subject']}</b>{deadline_str}",
        parse_mode="HTML",
    )
