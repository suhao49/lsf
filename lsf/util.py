"""
lsf.util -- input parsers and display formatters.
"""

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


def parse_due_date(raw: str) -> datetime:
    raw = raw.strip().lower()
    now = datetime.now()
    if raw in ("tonight", "eod", "end of day"):
        return now.replace(hour=22, minute=0, second=0, microsecond=0)
    if raw.startswith("tomorrow"):
        parts     = raw.split()
        time_part = parts[1] if len(parts) > 1 else "23:59"
        h, m      = map(int, time_part.split(":"))
        return (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)

    # Year-bearing formats first (no ambiguity)
    for fmt in ["%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue

    # Yearless formats -- inject current year to avoid Python 3.15 deprecation warning
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
        injected   = f"{date_token}/{now.year} {rest}".strip()
        try:
            return datetime.strptime(injected, dst_fmt)
        except ValueError:
            continue

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

