"""
lsf.task -- Task model, time utilities, and schedulers.

Two schedulers are provided:
  schedule()        -- classic monolithic sort (used by panic mode)
  schedule_sliced() -- dynamic LSF time-slice scheduler (used by display)
"""

import uuid
from datetime import datetime, timedelta, date, time as dtime

from .config import load as load_config

# -- Constants ----------------------------------------------------------------

CFG                   = load_config()
RISK_MULTIPLIER       = CFG['risk_multiplier']
SWITCH_PENALTY_H      = CFG['switch_penalty_h']
BREAK_H               = CFG['break_h']
SLICE_H               = CFG['slice_h']
SWITCH_URGENCY_PENALTY = CFG['switch_urgency_penalty']
URGENCY_SLACK_FLOOR   = CFG['urgency_slack_floor_h']
EPSILON               = 0.01


# -- Time utilities -----------------------------------------------------------

def _windows_for_day(d: date) -> list[tuple[dtime, dtime]]:
    """Return the list of (start, end) time windows for a given date.
    Individual day sections ([monday]..[sunday]) take precedence over
    [weekday] / [weekend] defaults.
    """
    return CFG["windows_by_day"][d.weekday()]


def _day_total_h(d: date) -> float:
    """Total available working hours on a given date."""
    return CFG["total_h_by_day"][d.weekday()]


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
                continue
            available = (window_end - window_start).total_seconds() / 3600
            if remaining <= available:
                cursor    = window_start + timedelta(hours=remaining)
                remaining = 0
                advanced  = True
                break
            else:
                remaining -= available
                cursor    = window_end
                advanced  = True
                break   # restart outer loop so next window is found correctly

        if remaining > 0 and not advanced:
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

        # Computed by compute()
        self.adjusted_estimate:  float    = 0.0
        self.remaining_estimate: float    = 0.0
        self.available_h:        float    = 0.0
        self.slack:              float    = 0.0
        self.urgency:            float    = 0.0
        self.raw_urgency:        float    = 0.0
        self.overloaded:         bool     = False
        self.will_be_late:       bool     = False
        self.finish_time:        datetime = datetime.now()

    def compute(self, now: datetime, current_task_id: str | None = None):
        """
        Recompute all derived fields as of `now`.
        current_task_id: if set and differs from self.id, applies the switch
        urgency penalty so the scheduler favours continuing the current task.
        """
        self.adjusted_estimate  = max(self.raw_estimate, 0.1) * RISK_MULTIPLIER
        self.remaining_estimate = max(self.adjusted_estimate - self.time_spent, 0.0)

        # Deadline before first window of that day: snap to end of previous day's last window
        effective_due   = self.due
        due_wins        = _windows_for_day(self.due.date())
        first_win_start = datetime.combine(self.due.date(), due_wins[0][0])
        if self.due <= first_win_start:
            prev_wins     = _windows_for_day((self.due - timedelta(days=1)).date())
            effective_due = datetime.combine(
                (self.due - timedelta(days=1)).date(), prev_wins[-1][1]
            )

        self.available_h = daylight_hours_until(effective_due, now)
        self.slack       = self.available_h - self.remaining_estimate
        self.overloaded  = self.slack < 0

        # raw_urgency: continuous scale, used by the slice scheduler so overloaded
        # tasks still compete with non-overloaded ones rather than always winning.
        effective_slack   = max(self.slack, URGENCY_SLACK_FLOOR) if self.slack >= 0 \
                            else max(-self.slack, URGENCY_SLACK_FLOOR)
        self.raw_urgency  = self.priority / effective_slack
        if current_task_id is not None and current_task_id != self.id:
            self.raw_urgency *= SWITCH_URGENCY_PENALTY

        # urgency: overloaded tasks boosted to front for the classic monolithic scheduler
        if self.overloaded:
            self.urgency = 1e9 + self.priority * 1e6
        else:
            self.urgency = self.raw_urgency


# -- Slice dataclass ----------------------------------------------------------

class Slice:
    """A single scheduled work block for one task."""
    __slots__ = ("task", "duration_h", "start", "end", "will_be_late")

    def __init__(self, task: Task, duration_h: float,
                 start: datetime, end: datetime):
        self.task         = task
        self.duration_h   = duration_h
        self.start        = start
        self.end          = end
        self.will_be_late = end > task.due


# -- Classic monolithic scheduler (used by panic mode) ------------------------


def _snap_to_next_window(now: datetime) -> datetime:
    """
    If `now` falls outside all configured working windows, advance it to
    the start of the next available window. If already inside a window,
    return `now` unchanged.
    """
    # Check today's windows first
    for ws_t, we_t in _windows_for_day(now.date()):
        ws = datetime.combine(now.date(), ws_t)
        we = datetime.combine(now.date(), we_t)
        if ws <= now < we:
            return now      # already inside a window
        if ws > now:
            return ws       # next window is later today

    # All today's windows are past -- find first window of the next working day
    check = now.date() + timedelta(days=1)
    for _ in range(8):
        wins = _windows_for_day(check)
        if wins:
            return datetime.combine(check, wins[0][0])
        check += timedelta(days=1)

    return now  # fallback: shouldn't happen with a valid config


def _remaining_in_window(cursor: datetime) -> float:
    """Working hours left in the current window from cursor.
    Returns 0 if cursor is not inside any window.
    """
    for ws_t, we_t in _windows_for_day(cursor.date()):
        ws = datetime.combine(cursor.date(), ws_t)
        we = datetime.combine(cursor.date(), we_t)
        if ws <= cursor < we:
            return (we - cursor).total_seconds() / 3600
    return 0.0


def schedule(tasks: list[Task], now: datetime) -> list[Task]:
    """
    Sort tasks once by urgency and simulate sequential execution.
    Used by panic() which needs a task-level view, not a session list.
    """
    for t in tasks:
        t.compute(now)
    sorted_tasks = sorted(tasks, key=lambda t: (
        0 if t.overloaded else 1,
        -t.urgency
    ))
    cursor = _snap_to_next_window(now)
    for i, t in enumerate(sorted_tasks):
        # No clock cost for switching -- the urgency multiplier (SWITCH_URGENCY_PENALTY)
        # already discourages unnecessary switches without burning scheduled time.
        finish         = add_working_hours(cursor, t.remaining_estimate)
        t.finish_time  = finish
        t.will_be_late = finish > t.due
        cursor         = finish
    return sorted_tasks


# -- Dynamic LSF time-slice scheduler -----------------------------------------

def schedule_sliced(tasks: list[Task], now: datetime,
                    break_h: float | None = None) -> tuple[list[Slice], list[Task]]:
    """
    Dynamic Least-Slack-First scheduler with time slicing.

    Instead of committing a full task at a time, repeatedly picks the most
    urgent task, works one slice, then re-evaluates priorities. This prevents
    large low-urgency tasks from blocking small urgent ones.

    Slice lengths are difficulty-dependent:
        light  (1) -- 30 min
        medium (2) -- 60 min
        deep   (3) -- 90 min

    break_h parameter: override break length (pass 0.0 to disable, e.g. for panic mode).
    Defaults to the configured break_h from config.toml.

    Returns:
        slices     -- ordered list of Slice objects (the session plan)
        tasks      -- the original task list with finish_time / will_be_late set
    """
    if break_h is None:
        break_h = BREAK_H
    # Initial compute() pass to populate adjusted/remaining estimates.
    # We do this here so callers don't have to pre-compute.
    for t in tasks:
        t.compute(now)

    # Work on copies of remaining work so we don't mutate task.time_spent
    remaining = {t.id: t.remaining_estimate for t in tasks}

    slices:   list[Slice] = []
    cursor    = _snap_to_next_window(now)
    prev_id:  str | None  = None
    max_iter  = sum(
        max(1, int(remaining[t.id] / SLICE_H[t.difficulty]) + 1)
        for t in tasks
    ) + len(tasks) + 10   # safety cap

    for _ in range(max_iter):
        # Filter to tasks with work remaining
        active = [t for t in tasks if remaining[t.id] > 1e-6]
        if not active:
            break

        # Recompute urgency for each active task as of cursor,
        # passing the current task id so the switch penalty applies
        for t in active:
            # Temporarily adjust remaining_estimate to the live value
            orig = t.remaining_estimate
            t.remaining_estimate = remaining[t.id]
            t.compute(cursor, current_task_id=prev_id)
            # restore so task object still reflects original for display
            t.remaining_estimate = orig

        # Pick by raw_urgency so overloaded tasks compete on the same scale.
        # The slice scheduler's job is to interleave optimally -- the overloaded
        # flag is for display only, not for scheduling priority.
        chosen = max(active, key=lambda t: t.raw_urgency)

        # Slice size: min(difficulty slice, what's left)
        slice_h = min(SLICE_H[chosen.difficulty], remaining[chosen.id])

        start  = cursor
        end    = add_working_hours(cursor, slice_h)

        # If there's a tiny tail left (< 5 min) after this slice,
        # absorb it now rather than scheduling a fragment that will
        # get preempted and pushed far into the future.
        tail = remaining[chosen.id] - slice_h
        if 0 < tail < 5/60:
            slice_h += tail
            end = add_working_hours(cursor, slice_h)
        slices.append(Slice(chosen, slice_h, start, end))
        remaining[chosen.id] -= slice_h
        # Apply break after slice, clipped to remaining window time.
        # Breaks never push the cursor past a window boundary -- the next
        # slice picks up at the next window start naturally.
        if break_h > 0 and remaining[chosen.id] > 1e-6:
            win_left = _remaining_in_window(end)
            actual_break = min(break_h, win_left)
            if actual_break > 0:
                cursor = end + timedelta(hours=actual_break)
            else:
                cursor = end
        else:
            cursor = end
        prev_id = chosen.id

    # Back-fill task-level finish_time and will_be_late from the last slice of each task
    last_slice: dict[str, Slice] = {}
    for s in slices:
        last_slice[s.task.id] = s

    for t in tasks:
        if t.id in last_slice:
            t.finish_time  = last_slice[t.id].end
            t.will_be_late = last_slice[t.id].end > t.due
        else:
            # No slices scheduled -- task was already done or had 0 remaining
            t.finish_time  = now
            t.will_be_late = False

    # Final compute() pass with original remaining_estimate so display is accurate
    for t in tasks:
        t.compute(now)

    return slices, tasks


def today_slices(slices: list[Slice], now: datetime) -> tuple[list[Slice], datetime, datetime]:
    """
    Filter the full slice list to only those that fall within the current
    (or next) working day's windows.

    If now is outside all windows today, advances to the next available window.
    Returns (filtered_slices, day_start, day_end) where day_start/day_end are
    the first and last window boundaries for the relevant day.
    """
    # Find the relevant working day -- today if we're inside or before a window,
    # tomorrow if we've passed all windows today
    check_date = now.date()
    day_start  = None
    day_end    = None

    for _ in range(8):   # look ahead up to a week
        wins = _windows_for_day(check_date)
        # Last window end for this day
        last_end = datetime.combine(check_date, wins[-1][1])
        if last_end > now or check_date > now.date():
            # This day has future working time
            first_start = datetime.combine(check_date, wins[0][0])
            day_start   = max(first_start, now) if check_date == now.date() else first_start
            day_end     = last_end
            break
        check_date += timedelta(days=1)

    if day_start is None or day_end is None:
        return [], now, now

    # Keep only slices whose start falls before day_end
    day_slices = [s for s in slices if s.start < day_end]
    return day_slices, day_start, day_end


def edf_max_subset(tasks: list["Task"], now: datetime) -> tuple[list["Task"], list["Task"]]:
    """
    Priority-weighted greedy scheduler for panic triage.

    Recomputes slack fresh from `now` to avoid stale cached values.
    Sorts by urgency descending so high-priority tasks get scheduled first,
    falling back to deadline tiebreaking when urgency is equal.

    Algorithm:
      1. Recompute slack = daylight_hours_until(due, now) - remaining for each task
      2. Discard tasks with slack < 0 (individually impossible)
      3. Sort by urgency desc (priority / max(slack, 0.25)), deadline asc as tiebreak
      4. Greedily simulate: if a task finishes before its deadline, schedule it
      5. Otherwise defer it -- it cannot fit without sacrificing earlier tasks

    High-priority tasks are protected first. If a high-priority task truly
    cannot fit, it gets deferred and lower-priority ones fill remaining capacity.

    Returns:
        (on_time, deferred) -- deferred in removal order (first = first casualty)
    """
    def fresh_slack(t: "Task") -> float:
        """Recompute slack at now -- avoids using a stale cached t.slack."""
        return daylight_hours_until(t.due, now) - t.remaining_estimate

    def urgency_score(t: "Task", slack: float) -> float:
        eff_slack = max(slack, 0.25) if slack >= 0 else max(-slack, 0.25)
        return t.priority / eff_slack

    # Filter to individually achievable tasks using fresh slack
    candidates = [t for t in tasks if fresh_slack(t) >= 0]

    # Sort: highest urgency first, tightest deadline as tiebreak
    candidates.sort(key=lambda t: (-urgency_score(t, fresh_slack(t)), t.due))

    cursor   = now
    on_time:  list["Task"] = []
    deferred: list["Task"] = []

    for t in candidates:
        finish = add_working_hours(cursor, t.remaining_estimate)
        if finish <= t.due:
            on_time.append(t)
            cursor = finish
        else:
            deferred.append(t)

    return on_time, deferred

