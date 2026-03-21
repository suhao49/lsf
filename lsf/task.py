"""
lsf.task -- Task model, time utilities, and core scheduler.
"""

import uuid
from datetime import datetime, timedelta, date, time as dtime

from .config import load as load_config

# -- Time utilities -----------------------------------------------------------

CFG              = load_config()
RISK_MULTIPLIER  = CFG['risk_multiplier']
SWITCH_PENALTY_H = CFG['switch_penalty_h']
EPSILON          = 0.01


def _windows_for_day(d: date) -> list[tuple[dtime, dtime]]:
    """Return the list of (start, end) time windows for a given date."""
    if d.weekday() < 5:   # Mon=0 … Fri=4
        return CFG["windows_weekday"]
    return CFG["windows_weekend"]


def _day_total_h(d: date) -> float:
    """Total available working hours on a given date."""
    if d.weekday() < 5:
        return CFG["total_day_h_weekday"]
    return CFG["total_day_h_weekend"]


def daylight_hours_until(due: datetime, now: datetime) -> float:
    """
    Count available working hours between now and due across all configured
    time windows, correctly handling multi-window days.
    """
    if due <= now:
        return 0.0
    total  = 0.0
    cursor = now

    while cursor.date() <= due.date():
        for ws_t, we_t in _windows_for_day(cursor.date()):
            ws = datetime.combine(cursor.date(), ws_t)
            we = datetime.combine(cursor.date(), we_t)
            window_start = max(cursor, ws)
            window_end   = min(due, we)
            if window_end > window_start:
                total += (window_end - window_start).total_seconds() / 3600
        # Advance to start of next day (first window)
        next_date = cursor.date() + timedelta(days=1)
        wins_next = _windows_for_day(next_date)
        cursor    = datetime.combine(next_date, wins_next[0][0])
        if cursor >= due:
            break

    return total


def add_working_hours(start: datetime, hours: float) -> datetime:
    """
    Advance start by hours of working time, skipping gaps between windows
    and between days.
    """
    remaining = hours
    cursor    = start

    while remaining > 0:
        advanced = False
        for ws_t, we_t in _windows_for_day(cursor.date()):
            ws = datetime.combine(cursor.date(), ws_t)
            we = datetime.combine(cursor.date(), we_t)
            window_start = max(cursor, ws)
            window_end   = we
            if window_end <= window_start:
                continue   # cursor is past this window already
            available = (window_end - window_start).total_seconds() / 3600
            if remaining <= available:
                cursor    = window_start + timedelta(hours=remaining)
                remaining = 0
                advanced  = True
                break
            else:
                remaining -= available
                cursor    = window_end   # move to end of this window, next loop picks up next
                advanced  = True

        if remaining > 0:
            # All windows on this day exhausted — jump to first window of next day
            next_date = cursor.date() + timedelta(days=1)
            wins_next = _windows_for_day(next_date)
            cursor    = datetime.combine(next_date, wins_next[0][0])

    return cursor

# -- Task model ---------------------------------------------------------------

class Task:
    def __init__(self, name: str, due: datetime, estimate_h: float,
                 priority: int, task_id: str = None, difficulty: int = 2):
        self.id           = task_id or str(uuid.uuid4())[:8]
        self.name         = name
        self.due          = due
        self.raw_estimate = estimate_h
        self.priority     = priority
        self.difficulty   = difficulty   # 1=light  2=medium  3=deep
        self.time_spent   = 0.0

        # Computed fields
        self.adjusted_estimate:  float    = 0.0
        self.remaining_estimate: float    = 0.0
        self.available_h:        float    = 0.0
        self.slack:              float    = 0.0
        self.urgency:            float    = 0.0
        self.overloaded:         bool     = False
        self.will_be_late:       bool     = False
        self.finish_time:        datetime = datetime.now()

    def compute(self, now: datetime):
        # Fix 2: guard against zero estimates (causes division quirks downstream)
        self.adjusted_estimate  = max(self.raw_estimate, 0.1) * RISK_MULTIPLIER
        self.remaining_estimate = max(self.adjusted_estimate - self.time_spent, 0.0)

        # Deadline before first working window of that day: snap to end of last
        # window the previous day so available time is calculated correctly.
        effective_due  = self.due
        due_wins       = _windows_for_day(self.due.date())
        first_win_start = datetime.combine(self.due.date(), due_wins[0][0])
        if self.due <= first_win_start:
            prev_wins     = _windows_for_day((self.due - timedelta(days=1)).date())
            effective_due = datetime.combine(
                (self.due - timedelta(days=1)).date(), prev_wins[-1][1]
            )

        self.available_h = daylight_hours_until(effective_due, now)
        self.slack       = self.available_h - self.remaining_estimate
        self.overloaded  = self.slack < 0

        # Overloaded: large constant keeps sort stable; priority still respected within tier
        if self.overloaded:
            self.urgency = 1e9 + self.priority * 1e6
        else:
            # Fix 1: clamp slack to 0.25h minimum to prevent urgency spikes near zero
            effective_slack = max(self.slack, 0.25)
            self.urgency    = self.priority / effective_slack

# -- Scheduler ----------------------------------------------------------------

def schedule(tasks: list[Task], now: datetime) -> list[Task]:
    for t in tasks:
        t.compute(now)
    # Fix 3: explicit 0/1 tier is clearer than (not t.overloaded)
    sorted_tasks = sorted(tasks, key=lambda t: (
        0 if t.overloaded else 1,
        -t.urgency
    ))
    cursor = now
    for i, t in enumerate(sorted_tasks):
        switch_cost    = SWITCH_PENALTY_H if i > 0 else 0.0
        start          = add_working_hours(cursor, switch_cost)
        finish         = add_working_hours(start, t.remaining_estimate)
        t.finish_time  = finish
        t.will_be_late = finish > t.due
        cursor         = finish
    return sorted_tasks

