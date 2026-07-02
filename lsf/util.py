"""
lsf.util -- input parsers and display formatters.
"""

import re
from datetime import datetime, timedelta, date

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"



def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  {text}{suffix}: ").strip()
    return val if val else default


_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = map(int, s.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid time '{s}'")
    return h, m


def parse_due_date(raw: str) -> datetime:
    """
    Accepted forms:
        tonight / eod                 today 22:00
        today [HH:MM]                 defaults 23:59
        tomorrow [HH:MM]              defaults 23:59
        friday [HH:MM]                next occurrence of that weekday
        next mon [HH:MM]              one week after the next occurrence
        +3d / +2w [HH:MM]             relative days / weeks from today
        25/03 [HH:MM]                 next 25th of March (rolls to next year if past)
        25/03/2026 [HH:MM]            explicit
        2026-03-25 [HH:MM]            ISO
    """
    raw = raw.strip().lower()
    now = datetime.now()
    if raw in ("tonight", "eod", "end of day"):
        return now.replace(hour=22, minute=0, second=0, microsecond=0)

    tokens = raw.split()

    if tokens and tokens[0] in ("today", "tomorrow"):
        h, m = _parse_hhmm(tokens[1]) if len(tokens) > 1 else (23, 59)
        base = now if tokens[0] == "today" else now + timedelta(days=1)
        return base.replace(hour=h, minute=m, second=0, microsecond=0)

    # Weekday names: 'friday', 'fri 18:00', 'next mon 18:00'
    next_week = False
    day_tokens = tokens
    if len(tokens) >= 2 and tokens[0] == "next":
        next_week  = True
        day_tokens = tokens[1:]
    if day_tokens and day_tokens[0] in _WEEKDAYS:
        h, m   = _parse_hhmm(day_tokens[1]) if len(day_tokens) > 1 else (23, 59)
        ahead  = (_WEEKDAYS[day_tokens[0]] - now.weekday()) % 7
        result = (now + timedelta(days=ahead)).replace(
            hour=h, minute=m, second=0, microsecond=0)
        if ahead == 0 and result <= now:
            result += timedelta(days=7)
        if next_week:
            result += timedelta(days=7)
        return result

    # Relative offsets: '+3d', '+2w 18:00'
    m_rel = re.fullmatch(r"\+(\d+)\s*([dw])(?:\s+(\d{1,2}:\d{2}))?", raw)
    if m_rel:
        n, unit, time_part = m_rel.groups()
        days = int(n) * (7 if unit == "w" else 1)
        h, m = _parse_hhmm(time_part) if time_part else (23, 59)
        return (now + timedelta(days=days)).replace(
            hour=h, minute=m, second=0, microsecond=0)

    # Year-bearing formats first (no ambiguity)
    for fmt in ["%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue

    # Yearless formats -- inject current year, rolling to next year if the
    # result is already past (typing '05/01' in December means next January).
    yearless = [("%d/%m %H:%M", "%d/%m/%Y %H:%M"),
                ("%d/%m",       "%d/%m/%Y")]
    for src_fmt, dst_fmt in yearless:
        try:
            datetime.strptime(raw, src_fmt)   # probe
        except ValueError:
            continue
        parts      = raw.split()
        date_token = parts[0]
        rest       = " ".join(parts[1:])
        for year in (now.year, now.year + 1):
            injected = f"{date_token}/{year} {rest}".strip()
            try:
                result = datetime.strptime(injected, dst_fmt)
            except ValueError:
                continue    # e.g. 29/02 in a non-leap year
            if result >= now or year > now.year:
                return result
        # both years parsed but current-year result was past and next-year
        # failed (unreachable in practice) -- fall through to error

    raise ValueError(f"Could not parse date: '{raw}'")


def parse_duration(raw: str) -> float:
    raw = raw.strip().lower().replace(" ", "")
    if not raw:
        return 0.0
    if "h" in raw and "m" in raw:
        h_part, m_part = raw.split("h")
        return float(h_part) + float(m_part.replace("m", "")) / 60
    elif raw.endswith("h"):
        return float(raw[:-1])
    elif raw.endswith("m"):
        return float(raw[:-1]) / 60
    else:
        return float(raw)


def fmt_hours(h: float) -> str:
    if h < 0:
        return f"-{fmt_hours(-h)}"
    total_min = round(abs(h) * 60)
    hh, mm    = divmod(total_min, 60)
    if hh and mm:
        return f"{hh}h {mm}m"
    elif hh:
        return f"{hh}h"
    else:
        return f"{mm}m"


def fmt_dt(dt: datetime) -> str:
    today    = date.today()
    tomorrow = today + timedelta(days=1)
    if dt.date() == today:
        return f"today {dt:%H:%M}"
    elif dt.date() == tomorrow:
        return f"tomorrow {dt:%H:%M}"
    else:
        return dt.strftime("%a %d %b %H:%M")


def slack_bar(slack: float, adjusted_estimate: float, width: int = 20) -> str:
    """
    Bar scaled to the task's own adjusted estimate.
    Full (green)  = 2x estimate -- you have twice as long as you need.
    Half (yellow) = slack equals the estimate exactly.
    Full red      = overloaded.
    Stays meaningful whether the task is due in hours or weeks.
    """
    if slack <= 0:
        return RED + "#" * width + RESET
    cap    = max(adjusted_estimate * 2, 0.01)
    ratio  = min(slack / cap, 1.0)
    filled = int(ratio * width)
    color  = GREEN if ratio > 0.5 else YELLOW
    bar    = color + "|" * filled + DIM + "." * (width - filled) + RESET
    return bar

