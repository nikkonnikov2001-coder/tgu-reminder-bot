from datetime import datetime
import pytz

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

RUSSIAN_TIMEZONES = [
    ("🏙 Москва (UTC+3)", "Europe/Moscow"),
    ("🌆 Калининград (UTC+2)", "Europe/Kaliningrad"),
    ("🏔 Самара (UTC+4)", "Europe/Samara"),
    ("🌃 Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
    ("🏙 Омск (UTC+6)", "Asia/Omsk"),
    ("🌇 Красноярск (UTC+7)", "Asia/Krasnoyarsk"),
    ("🏞 Иркутск (UTC+8)", "Asia/Irkutsk"),
    ("🌄 Якутск (UTC+9)", "Asia/Yakutsk"),
    ("🌅 Владивосток (UTC+10)", "Asia/Vladivostok"),
    ("🌉 Магадан (UTC+11)", "Asia/Magadan"),
    ("🌃 Камчатка (UTC+12)", "Asia/Kamchatka"),
]


def utc_to_tz(dt_utc: datetime, tz_name: str) -> datetime:
    utc = pytz.utc
    if dt_utc.tzinfo is None:
        dt_utc = utc.localize(dt_utc)
    target_tz = pytz.timezone(tz_name)
    return dt_utc.astimezone(target_tz)


def utc_to_moscow(dt_utc: datetime) -> datetime:
    return utc_to_tz(dt_utc, "Europe/Moscow")


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def format_date(dt: datetime) -> str:
    months = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return f"{days[dt.weekday()]}, {dt.day} {months[dt.month - 1]}"
