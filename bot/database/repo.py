from datetime import datetime, date
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.models import User, Lesson, Teacher, Assignment


class UserRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_id: int, username: str | None) -> tuple[User, bool]:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            return user, False
        user = User(telegram_id=telegram_id, username=username)
        self.session.add(user)
        await self.session.flush()
        return user, True

    async def update_timezone(self, telegram_id: int, timezone: str) -> None:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.timezone = timezone
            await self.session.flush()

    async def update_calendar_url(self, telegram_id: int, url: str) -> None:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.calendar_url = url
            await self.session.flush()

    async def update_reminder_offsets(self, telegram_id: int, offsets: list[int]) -> None:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.reminder_offsets = offsets
            await self.session.flush()

    async def set_active(self, telegram_id: int, is_active: bool) -> None:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.is_active = is_active
            await self.session.flush()

    async def get_all_active(self) -> list[User]:
        result = await self.session.execute(select(User).where(User.is_active == True))
        return list(result.scalars().all())


class LessonRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_lessons(self, user_id: int, lessons: list[dict]) -> tuple[int, list[Lesson]]:
        now = datetime.utcnow()
        incoming_uids = {d["uid"] for d in lessons}

        existing_result = await self.session.execute(
            select(Lesson).where(Lesson.user_id == user_id)
        )
        existing_lessons = {l.uid: l for l in existing_result.scalars().all()}

        # Найти отменённые — те, которых нет в новом iCal, но ещё не прошли
        cancelled = [
            l for uid, l in existing_lessons.items()
            if uid not in incoming_uids and l.start_dt_utc > now
        ]

        new_count = 0
        for data in lessons:
            uid = data["uid"]
            if uid in existing_lessons:
                lesson = existing_lessons[uid]
                lesson.subject = data["subject"]
                lesson.teacher_name = data.get("teacher_name")
                lesson.start_dt_utc = data["start_dt_utc"]
                lesson.end_dt_utc = data["end_dt_utc"]
                lesson.room = data.get("room")
                lesson.conference_url = data.get("conference_url")
                lesson.reminded = False
            else:
                lesson = Lesson(user_id=user_id, **data)
                self.session.add(lesson)
                new_count += 1

        # Удаляем отменённые будущие пары
        for lesson in cancelled:
            await self.session.delete(lesson)

        await self.session.flush()
        return new_count, cancelled

    async def get_lessons_for_date(self, user_id: int, target_date: date) -> list[Lesson]:
        result = await self.session.execute(
            select(Lesson)
            .where(
                Lesson.user_id == user_id,
                Lesson.start_dt_utc >= datetime.combine(target_date, datetime.min.time()),
                Lesson.start_dt_utc < datetime.combine(target_date, datetime.max.time()),
            )
            .order_by(Lesson.start_dt_utc)
        )
        return list(result.scalars().all())

    async def get_lessons_for_week(self, user_id: int, week_start: date, week_end: date) -> list[Lesson]:
        result = await self.session.execute(
            select(Lesson)
            .where(
                Lesson.user_id == user_id,
                Lesson.start_dt_utc >= datetime.combine(week_start, datetime.min.time()),
                Lesson.start_dt_utc < datetime.combine(week_end, datetime.max.time()),
            )
            .order_by(Lesson.start_dt_utc)
        )
        return list(result.scalars().all())

    async def get_upcoming_unreminded(self) -> list[Lesson]:
        now = datetime.utcnow()
        result = await self.session.execute(
            select(Lesson)
            .where(
                Lesson.reminded == False,
                Lesson.start_dt_utc > now,
            )
            .order_by(Lesson.start_dt_utc)
        )
        return list(result.scalars().all())

    async def mark_reminded(self, lesson_id: int) -> None:
        result = await self.session.execute(select(Lesson).where(Lesson.id == lesson_id))
        lesson = result.scalar_one_or_none()
        if lesson:
            lesson.reminded = True
            await self.session.flush()

    async def delete_old_lessons(self, user_id: int) -> None:
        await self.session.execute(
            delete(Lesson).where(
                Lesson.user_id == user_id,
                Lesson.start_dt_utc < datetime.utcnow(),
            )
        )
        await self.session.flush()


class TeacherRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_name(self, name: str) -> Teacher | None:
        result = await self.session.execute(
            select(Teacher).where(Teacher.name == name)
        )
        return result.scalar_one_or_none()

    async def upsert_photo(self, name: str, photo_file_id: str) -> Teacher:
        teacher = await self.get_by_name(name)
        if teacher:
            teacher.photo_file_id = photo_file_id
        else:
            teacher = Teacher(name=name, photo_file_id=photo_file_id)
            self.session.add(teacher)
        await self.session.flush()
        return teacher

    async def search_by_name(self, name: str) -> Teacher | None:
        result = await self.session.execute(
            select(Teacher).where(Teacher.name.ilike(f"%{name}%"))
        )
        return result.scalar_one_or_none()


class AssignmentRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_from_ical(self, user_id: int, assignments: list[dict]) -> tuple[int, list[Assignment]]:
        now = datetime.utcnow()
        incoming_uids = {a["uid"] for a in assignments if a.get("uid")}

        existing_result = await self.session.execute(
            select(Assignment).where(Assignment.user_id == user_id, Assignment.is_manual == False)
        )
        existing = {a.uid: a for a in existing_result.scalars().all() if a.uid}

        cancelled = [
            a for uid, a in existing.items()
            if uid not in incoming_uids and (a.deadline_utc is None or a.deadline_utc > now) and not a.is_done
        ]

        new_count = 0
        for data in assignments:
            uid = data.get("uid")
            if uid and uid in existing:
                a = existing[uid]
                a.subject = data["subject"]
                a.description = data.get("description")
                a.deadline_utc = data.get("deadline_utc")
            else:
                self.session.add(Assignment(user_id=user_id, **data))
                new_count += 1

        for a in cancelled:
            await self.session.delete(a)

        await self.session.flush()
        return new_count, cancelled

    async def add_manual(self, user_id: int, subject: str, description: str | None, deadline_utc: datetime | None) -> Assignment:
        a = Assignment(
            user_id=user_id,
            subject=subject,
            description=description,
            deadline_utc=deadline_utc,
            is_manual=True,
        )
        self.session.add(a)
        await self.session.flush()
        return a

    async def get_active(self, user_id: int) -> list[Assignment]:
        result = await self.session.execute(
            select(Assignment)
            .where(Assignment.user_id == user_id, Assignment.is_done == False)
            .order_by(Assignment.deadline_utc.asc().nullsfirst())
        )
        return list(result.scalars().all())

    async def get_by_id(self, assignment_id: int) -> Assignment | None:
        result = await self.session.execute(select(Assignment).where(Assignment.id == assignment_id))
        return result.scalar_one_or_none()

    async def mark_done(self, assignment_id: int) -> None:
        a = await self.get_by_id(assignment_id)
        if a:
            a.is_done = True
            await self.session.flush()

    async def get_upcoming_unreminded(self, user_id: int) -> list[Assignment]:
        now = datetime.utcnow()
        result = await self.session.execute(
            select(Assignment).where(
                Assignment.user_id == user_id,
                Assignment.is_done == False,
                Assignment.deadline_utc > now,
            )
        )
        return list(result.scalars().all())
