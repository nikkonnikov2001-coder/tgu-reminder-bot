from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import settings

router = Router()

USER_HELP = """
📖 <b>Команды бота</b>

<b>Пары:</b>
/today — пары на сегодня
/week — расписание на неделю
/next — ближайшая пара

<b>Задания:</b>
/tasks — список активных заданий
/addtask — добавить задание вручную

<b>Управление:</b>
/sync — обновить пары и задания из календаря
/settings — сменить часовой пояс
/reminders — настроить время напоминаний
/stop — отключить напоминания
/resume — включить напоминания
/start — подключить другой календарь
/cancel — отменить текущее действие

<b>Напоминания о парах:</b>
🔔 За 1 час · ⚡️ За 15 минут (настраивается)

<b>Напоминания о заданиях:</b>
📝 За 1 день · + настраиваемые интервалы
""".strip()

ADMIN_HELP = """

<b>Команды администратора:</b>
/addphoto Фамилия И.О. — добавить фото преподавателя
/listteachers — список преподавателей
/stats — статистика бота
/broadcast — рассылка всем пользователям
""".strip()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current:
        await state.clear()
        await message.answer("❌ Действие отменено.")
    else:
        await message.answer("Нечего отменять.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = USER_HELP
    if message.from_user.id in settings.admin_ids:
        text += "\n\n" + ADMIN_HELP
    await message.answer(text, parse_mode="HTML")
