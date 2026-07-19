"""
lsf.util -- input parsers and display formatters.
"""

import re
from datetime import datetime, timedelta, date, time as dtime

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

    # Bare time: '21:00', '9pm', '9:30pm' -> today at that time,
    # or tomorrow if it has already passed (useful for recurring tasks).
    m_t = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", raw)
    if m_t and (m_t.group(2) is not None or m_t.group(3)):  # bare '21' is too ambiguous
        h, mnt, ap = int(m_t.group(1)), int(m_t.group(2) or 0), m_t.group(3)
        if ap == "pm" and h != 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mnt <= 59:
            result = now.replace(hour=h, minute=mnt, second=0, microsecond=0)
            if result <= now:
                result += timedelta(days=1)
            return result

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


_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")


def _clock(h: str, m: str | None, ampm: str | None) -> dtime:
    hh, mm = int(h), int(m or 0)
    if ampm == "pm" and hh != 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"invalid time '{h}:{m or '00'}'")
    return dtime(hh, mm)


def parse_time_range(raw: str) -> tuple[dtime, dtime]:
    """Parse '14:00-16:00', '2-4pm', '2:30pm-4pm', '9-11am' into (start, end)."""
    m = _TIME_RANGE_RE.fullmatch(raw.strip().lower())
    if not m:
        raise ValueError(f"not a time range: '{raw}'")
    h1, m1, ap1, h2, m2, ap2 = m.groups()
    if ap1 is None and ap2 is not None and int(h1) <= 12:
        ap1 = ap2                      # '2-4pm' -> both pm
    start, end = _clock(h1, m1, ap1), _clock(h2, m2, ap2)
    if end <= start:
        raise ValueError(f"range end must be after start: '{raw}'")
    return start, end


def parse_busy_block(raw: str) -> dict:
    """
    Parse a one-off no-work period: '[day] <start>-<end> [label]'.
        thursday 14:00-16:00 team meeting
        2-4pm dentist                      (day defaults to today)
        25/03 9am-12pm open day
    Returns {"start": iso, "end": iso, "name": label}.
    """
    rng, span = None, None
    for m in _TIME_RANGE_RE.finditer(raw.lower()):
        try:
            rng  = parse_time_range(m.group(0))
            span = m.span()
            break
        except ValueError:
            continue                     # e.g. '26-03' inside an ISO date
    if rng is None:
        raise ValueError(
            "No time range found -- expected e.g. 'thursday 14:00-16:00 meeting'")
    day_spec = raw[:span[0]].strip()
    label    = raw[span[1]:].strip() or "busy"
    day      = parse_due_date(day_spec).date() if day_spec else date.today()
    return {
        "start": datetime.combine(day, rng[0]).isoformat(),
        "end":   datetime.combine(day, rng[1]).isoformat(),
        "name":  label,
    }


_SUBTASK_RANGE_RE = re.compile(r"(\d+)\s*\.\.\s*(\d+)$")


def parse_subtasks(raw: str) -> list[dict]:
    """
    Parse a subtask spec into [{"name", "weight", "done"}].
        'Chapter 1..11'            -> 11 subtasks, weight 1 each
        'intro, body*3, end*2'     -> weights after '*'
        'Ch 1..3*2'                -> range expansion, each with weight 2
    Empty input returns [].
    """
    items: list[dict] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        weight = 1.0
        name   = part
        if "*" in part:
            head, _, tail = part.rpartition("*")
            try:
                weight = float(tail.strip())
                name   = head.strip()
            except ValueError:
                pass                     # '*' is part of the name
        if weight <= 0:
            raise ValueError(f"subtask weight must be positive: '{part}'")
        if not name:
            raise ValueError(f"subtask has no name: '{part}'")
        m = _SUBTASK_RANGE_RE.search(name)
        if m and int(m.group(1)) <= int(m.group(2)):
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi - lo >= 200:
                raise ValueError(f"subtask range too large: '{name}'")
            prefix = name[:m.start()].rstrip()
            for i in range(lo, hi + 1):
                items.append({"name": f"{prefix} {i}".strip() if prefix else str(i),
                              "weight": weight, "done": False})
        else:
            items.append({"name": name, "weight": weight, "done": False})
    return items


def fmt_subtasks(subs: list[dict]) -> str:
    """Serialize subtasks back into the parse_subtasks() syntax (loses done flags)."""
    parts = []
    for s in subs:
        w = float(s.get("weight", 1.0))
        w_str = "" if w == 1.0 else f"*{w:g}"
        parts.append(f"{s['name']}{w_str}")
    return ", ".join(parts)


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

