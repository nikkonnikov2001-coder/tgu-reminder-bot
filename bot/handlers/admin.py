import asyncio
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, PhotoSize
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import Teacher, User, Lesson
from bot.database.repo import TeacherRepo, UserRepo

router = Router()


class AddPhotoFSM(StatesGroup):
    waiting_photo = State()


class BroadcastFSM(StatesGroup):
    waiting_text = State()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("addphoto"))
async def cmd_addphoto(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /addphoto Фамилия И.О.\nЗатем отправь фото.")
        return

    teacher_name = args[1].strip()
    await state.update_data(teacher_name=teacher_name)
    await state.set_state(AddPhotoFSM.waiting_photo)
    await message.answer(f"📸 Отправь фото для преподавателя: <b>{teacher_name}</b>", parse_mode="HTML")


@router.message(AddPhotoFSM.waiting_photo, F.photo)
async def msg_photo_received(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    teacher_name = data["teacher_name"]

    photo: PhotoSize = message.photo[-1]
    file_id = photo.file_id

    repo = TeacherRepo(session)
    await repo.upsert_photo(teacher_name, file_id)
    await session.commit()

    await state.clear()
    await message.answer(f"✅ Фото сохранено для <b>{teacher_name}</b>", parse_mode="HTML")


@router.message(AddPhotoFSM.waiting_photo)
async def msg_not_photo(message: Message) -> None:
    await message.answer("❌ Это не фото. Отправь фотографию.")


@router.message(Command("listteachers"))
async def cmd_list_teachers(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа.")
        return

    result = await session.execute(select(Teacher).order_by(Teacher.name))
    teachers = list(result.scalars().all())

    if not teachers:
        await message.answer("Список преподавателей пуст.")
        return

    lines = []
    for t in teachers:
        status = "✅" if t.photo_file_id else "❌"
        lines.append(f"{status} {t.name}")

    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа.")
        return

    total_users = (await session.execute(select(func.count()).select_from(User))).scalar()
    active_users = (await session.execute(
        select(func.count()).select_from(User).where(User.is_active == True)
    )).scalar()
    with_calendar = (await session.execute(
        select(func.count()).select_from(User).where(User.calendar_url != None)
    )).scalar()
    total_lessons = (await session.execute(select(func.count()).select_from(Lesson))).scalar()
    reminded = (await session.execute(
        select(func.count()).select_from(Lesson).where(Lesson.reminded == True)
    )).scalar()
    teachers_with_photo = (await session.execute(
        select(func.count()).select_from(Teacher).where(Teacher.photo_file_id != None)
    )).scalar()

    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🟢 Активных: <b>{active_users}</b>\n"
        f"📅 С календарём: <b>{with_calendar}</b>\n\n"
        f"📚 Всего пар в БД: <b>{total_lessons}</b>\n"
        f"✅ Напоминаний отправлено: <b>{reminded}</b>\n\n"
        f"👤 Преподавателей с фото: <b>{teachers_with_photo}</b>",
        parse_mode="HTML",
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа.")
        return

    await message.answer(
        "📢 Введи текст рассылки.\n"
        "Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>).\n\n"
        "Для отмены — /cancel"
    )
    await state.set_state(BroadcastFSM.waiting_text)


@router.message(BroadcastFSM.waiting_text, F.text)
async def msg_broadcast_text(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    text = message.text.strip()

    result = await session.execute(select(User).where(User.is_active == True))
    users = list(result.scalars().all())

    await state.clear()
    status = await message.answer(f"⏳ Отправляю {len(users)} пользователям...")

    sent = 0
    failed = 0
    for user in users:
        try:
            await bot.send_message(user.telegram_id, text, parse_mode="HTML")
            sent += 1
        except TelegramForbiddenError:
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # ~20 msg/sec — в рамках лимитов Telegram

    await status.edit_text(
        f"✅ Рассылка завершена\n\n"
        f"Отправлено: <b>{sent}</b>\n"
        f"Ошибок: <b>{failed}</b>",
        parse_mode="HTML",
    )


