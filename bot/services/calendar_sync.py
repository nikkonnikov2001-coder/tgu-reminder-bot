from datetime import datetime, timezone
from typing import Any
import httpx
from icalendar import Calendar


def _parse_dt(dt_val: Any) -> datetime:
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            import pytz
            msk = pytz.timezone("Europe/Moscow")
            dt_val = msk.localize(dt_val)
        return dt_val.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime(dt_val.year, dt_val.month, dt_val.day, 0, 0, 0)


def _extract_conference_url(description: str | None) -> str | None:
    if not description:
        return None
    for line in description.splitlines():
        for part in (line.strip(),) + tuple(line.split()):
            if part.startswith(("https://zoom.us", "https://meet.google", "https://teams.microsoft")):
                return part
    return None


def _is_assignment(summary: str) -> bool:
    """Событие является заданием если начинается с 'Задание'."""
    return summary.strip().lower().startswith("задание")


def parse_both(raw: bytes) -> tuple[list[dict], list[dict]]:
    """
    Парсит iCal и возвращает (lessons, assignments).
    VEVENT с названием 'Задание ...' → задание с дедлайном.
    Остальные VEVENT → пары.
    VTODO → тоже задания.
    """
    cal = Calendar.from_ical(raw)
    lessons = []
    assignments = []

    for component in cal.walk():
        if component.name == "VEVENT":
            uid = str(component.get("UID", ""))
            summary = str(component.get("SUMMARY", "Без названия"))
            description = str(component.get("DESCRIPTION", "") or "").strip()
            location = str(component.get("LOCATION", "") or "")

            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if not dtstart:
                continue

            start_utc = _parse_dt(dtstart.dt)

            if _is_assignment(summary):
                # Дедлайн = время начала события (срок сдачи)
                assignments.append({
                    "uid": uid,
                    "subject": summary,
                    "description": description or None,
                    "deadline_utc": start_utc,
                    "is_manual": False,
                })
            else:
                if not dtend:
                    continue
                end_utc = _parse_dt(dtend.dt)

                teacher_name = None
                for line in description.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("http"):
                        teacher_name = stripped
                        break

                lessons.append({
                    "uid": uid,
                    "subject": summary,
                    "teacher_name": teacher_name,
                    "start_dt_utc": start_utc,
                    "end_dt_utc": end_utc,
                    "room": location or None,
                    "conference_url": _extract_conference_url(description),
                })

        elif component.name == "VTODO":
            uid = str(component.get("UID", ""))
            summary = str(component.get("SUMMARY", "Без названия"))
            description = str(component.get("DESCRIPTION", "") or "").strip() or None
            due = component.get("DUE") or component.get("DTEND")
            deadline_utc = _parse_dt(due.dt) if due else None
            assignments.append({
                "uid": uid,
                "subject": summary,
                "description": description,
                "deadline_utc": deadline_utc,
                "is_manual": False,
            })

    return lessons, assignments


# Обратная совместимость для существующих хендлеров
def parse_ical(raw: bytes) -> list[dict]:
    lessons, _ = parse_both(raw)
    return lessons


def parse_assignments(raw: bytes) -> list[dict]:
    _, assignments = parse_both(raw)
    return assignments


class CalendarError(Exception):
    pass


def validate_ical(raw: bytes) -> None:
    if not raw.strip():
        raise CalendarError("Файл пустой.")
    if b"BEGIN:VCALENDAR" not in raw:
        raise CalendarError("Файл не является iCal-календарём (нет BEGIN:VCALENDAR).")
    if b"BEGIN:VEVENT" not in raw and b"BEGIN:VTODO" not in raw:
        raise CalendarError("В календаре нет событий.")


def parse_both_safe(raw: bytes) -> tuple[list[dict], list[dict]]:
    validate_ical(raw)
    try:
        return parse_both(raw)
    except Exception as e:
        raise CalendarError(f"Ошибка разбора календаря: {e}") from e


async def fetch_and_parse_both(url: str) -> tuple[list[dict], list[dict]]:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise CalendarError(f"Сервер вернул ошибку {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise CalendarError(f"Не удалось подключиться: {e}") from e
    return parse_both_safe(response.content)


async def fetch_and_parse(url: str) -> list[dict]:
    lessons, _ = await fetch_and_parse_both(url)
    return lessons


async def fetch_and_parse_assignments(url: str) -> list[dict]:
    _, assignments = await fetch_and_parse_both(url)
    return assignments


async def download_and_parse_file(bot, file_id: str) -> list[dict]:
    try:
        file = await bot.get_file(file_id)
        downloaded = await bot.download_file(file.file_path)
        raw = downloaded.read()
    except Exception as e:
        raise CalendarError(f"Не удалось скачать файл: {e}") from e
    return parse_ical(raw)


async def download_and_parse_both(bot, file_id: str) -> tuple[list[dict], list[dict]]:
    try:
        file = await bot.get_file(file_id)
        downloaded = await bot.download_file(file.file_path)
        raw = downloaded.read()
    except Exception as e:
        raise CalendarError(f"Не удалось скачать файл: {e}") from e
    return parse_both_safe(raw)
