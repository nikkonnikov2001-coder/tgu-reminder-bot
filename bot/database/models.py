import json
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DEFAULT_REMINDER_OFFSETS = [60, 15]  # минуты до начала пары


class JsonList(TypeDecorator):
    """Хранит list[int] как JSON-строку в TEXT-колонке."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value) if value is not None else json.dumps(DEFAULT_REMINDER_OFFSETS)

    def process_result_value(self, value, dialect):
        if value is None:
            return DEFAULT_REMINDER_OFFSETS
        try:
            return json.loads(value)
        except Exception:
            return DEFAULT_REMINDER_OFFSETS


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    reminder_offsets: Mapped[list] = mapped_column(JsonList, default=lambda: DEFAULT_REMINDER_OFFSETS)
    calendar_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lessons: Mapped[list["Lesson"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    uid: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    teacher_name: Mapped[str | None] = mapped_column(String(256))
    start_dt_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_dt_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    room: Mapped[str | None] = mapped_column(String(128))
    conference_url: Mapped[str | None] = mapped_column(Text)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="lessons")


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(512))
