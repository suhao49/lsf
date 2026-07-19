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
URGENCY_BAND_PCT      = CFG['urgency_band_pct']
MIN_SLICE_H           = CFG['min_slice_h']
EPSILON               = 0.01


# -- One-off busy blocks ------------------------------------------------------
# Busy blocks are one-time no-work periods ("Thursday 2-4pm meeting") that get
# subtracted from that date's working windows. The weekday-based O(1) prefix
# math below stays intact: per-date corrections are kept in small dicts and
# applied on top (blocks are few, so the extra term is effectively free).

_BUSY_SRC:       list[tuple[datetime, datetime, str]] = []
_EFF_WINDOWS:    dict[date, list[tuple[dtime, dtime]]] = {}
_BUSY_H_BY_DATE: dict[date, float] = {}


def _subtract_intervals(windows: list[tuple[dtime, dtime]],
                        busy: list[tuple[dtime, dtime]]) -> list[tuple[dtime, dtime]]:
    segs = list(windows)
    for bs, be in busy:
        nxt = []
        for s0, e0 in segs:
            if be <= s0 or bs >= e0:
                nxt.append((s0, e0))
                continue
            if bs > s0:
                nxt.append((s0, bs))
            if be < e0:
                nxt.append((be, e0))
        segs = nxt
    return [(s, e) for s, e in segs if e > s]


def set_busy_blocks(blocks: list[dict]) -> None:
    """Install one-off busy blocks ({"start": iso, "end": iso, "name": str})
    into the working-hours math. Replaces any previously installed set."""
    global _BUSY_SRC
    _BUSY_SRC = []
    _EFF_WINDOWS.clear()
    _BUSY_H_BY_DATE.clear()

    by_date: dict[date, list[tuple[dtime, dtime]]] = {}
    for b in blocks:
        try:
            s = datetime.fromisoformat(b["start"])
            e = datetime.fromisoformat(b["end"])
        except (KeyError, ValueError):
            continue
        if e <= s:
            continue
        _BUSY_SRC.append((s, e, b.get("name", "busy")))
        # Split multi-day blocks at midnight
        d = s.date()
        while d <= e.date():
            seg_s = s.time() if d == s.date() else dtime(0, 0)
            seg_e = e.time() if d == e.date() else dtime(23, 59, 59)
            by_date.setdefault(d, []).append((seg_s, seg_e))
            d += timedelta(days=1)

    def _h(wins):
        return sum(
            (datetime.combine(_EPOCH, e) - datetime.combine(_EPOCH, s)
             ).total_seconds() / 3600
            for s, e in wins
        )

    for d, busy in by_date.items():
        base = CFG["windows_by_day"][d.weekday()]
        eff  = _subtract_intervals(base, busy)
        _EFF_WINDOWS[d]    = eff
        _BUSY_H_BY_DATE[d] = _h(base) - _h(eff)


def busy_blocks_for_day(d: date) -> list[tuple[datetime, datetime, str]]:
    """Installed busy blocks overlapping date d (for display)."""
    day_s = datetime.combine(d, dtime(0, 0))
    day_e = day_s + timedelta(days=1)
    return [(s, e, name) for s, e, name in _BUSY_SRC
            if s < day_e and e > day_s]


# -- Time utilities -----------------------------------------------------------

def _windows_for_day(d: date) -> list[tuple[dtime, dtime]]:
    """Return the list of (start, end) time windows for a given date.
    Individual day sections ([monday]..[sunday]) take precedence over
    [weekday] / [weekend] defaults. One-off busy blocks are subtracted,
    so the result may be empty for a fully blocked day.
    """
    eff = _EFF_WINDOWS.get(d)
    if eff is not None:
        return eff
    return CFG["windows_by_day"][d.weekday()]


def _day_total_h(d: date) -> float:
    """Total available working hours on a given date."""
    return CFG["total_h_by_day"][d.weekday()] - _BUSY_H_BY_DATE.get(d, 0.0)


# Working-hours arithmetic in O(1):
# window hours depend only on the weekday, so cumulative working time from a
# fixed epoch is (whole weeks) x (weekly total) + a partial-week walk of <= 6
# days. daylight_hours_until / add_working_hours then reduce to arithmetic
# instead of walking day-by-day, which matters because the slice scheduler
# calls them for every task on every slice.

_EPOCH        = date(2001, 1, 1)   # a Monday, safely before any real task
_WEEK_TOTAL_H = sum(CFG["total_h_by_day"][i] for i in range(7))


def _working_h_from_epoch(d: date) -> float:
    """Total working hours in all days from _EPOCH up to (excluding) d."""
    days = (d - _EPOCH).days
    if days <= 0:
        return 0.0
    weeks, rem = divmod(days, 7)
    total = weeks * _WEEK_TOTAL_H
    for weekday in range(rem):   # _EPOCH is a Monday, so index == weekday
        total += CFG["total_h_by_day"][weekday]
    # Busy-block correction: subtract hours removed on dates before d
    for bd, h in _BUSY_H_BY_DATE.items():
        if bd < d:
            total -= h
    return total


def _working_h_before(dt: datetime) -> float:
    """Working hours within dt's own day that fall before time dt."""
    total = 0.0
    for ws_t, we_t in _windows_for_day(dt.date()):
        ws = datetime.combine(dt.date(), ws_t)
        we = datetime.combine(dt.date(), we_t)
        end = min(dt, we)
        if end > ws:
            total += (end - ws).total_seconds() / 3600
    return total


def _working_position(dt: datetime) -> float:
    """Absolute position of dt on the working-hours axis (from _EPOCH)."""
    return _working_h_from_epoch(dt.date()) + _working_h_before(dt)


def daylight_hours_until(due: datetime, now: datetime) -> float:
    """
    Count available working hours between now and due across all configured
    time windows, correctly handling multi-window days.
    """
    if due <= now:
        return 0.0
    return max(_working_position(due) - _working_position(now), 0.0)


def add_working_hours(start: datetime, hours: float) -> datetime:
    """
    Advance start by hours of working time, skipping gaps between windows
    and between days.
    """
    if hours <= 0:
        return start

    target = _working_position(start) + hours

    d = start.date()
    if _WEEK_TOTAL_H > 0:
        # Jump whole weeks in one step, then walk the remainder day-by-day
        deficit = target - _working_h_from_epoch(d)
        weeks   = int(deficit // _WEEK_TOTAL_H)
        if weeks > 1:
            d += timedelta(weeks=weeks - 1)

    for _ in range(4000):   # safety bound (~11 years of zero-hour days)
        if _working_h_from_epoch(d + timedelta(days=1)) >= target - 1e-9:
            # target lands within day d -- walk its windows
            wins = _windows_for_day(d)
            if not wins:   # fully blocked day (busy blocks) -- keep walking
                d += timedelta(days=1)
                continue
            need = target - _working_h_from_epoch(d)
            for ws_t, we_t in wins:
                ws  = datetime.combine(d, ws_t)
                we  = datetime.combine(d, we_t)
                w_h = (we - ws).total_seconds() / 3600
                if w_h <= 0:
                    continue
                if need <= w_h + 1e-9:
                    return ws + timedelta(hours=max(need, 0.0))
                need -= w_h
            # float rounding tail: snap to the last window end
            return datetime.combine(d, wins[-1][1])
        d += timedelta(days=1)

    return datetime.combine(d, _windows_for_day(d)[0][0])   # unreachable fallback


# -- Task model ---------------------------------------------------------------

class Task:
    def __init__(self, name: str, due: datetime, estimate_h: float,
                 priority: int, task_id: str = None, difficulty: int = 2,
                 subtasks: list[dict] | None = None, ordered: bool = False,
                 recur: str | None = None):
        self.id           = task_id or str(uuid.uuid4())[:8]
        self.name         = name
        self.due          = due
        self.raw_estimate = estimate_h
        self.priority     = priority
        self.difficulty   = difficulty   # 1=light  2=medium  3=deep
        self.time_spent   = 0.0
        self.subtasks     = subtasks or []   # [{"name", "weight", "done"}]
        self.ordered      = ordered          # subtasks must be done in order
        self.recur        = recur            # None | "daily" | "weekly"

    # -- Subtask helpers ---------------------------------------------------------

    def subtask_counts(self) -> tuple[int, int]:
        """(done, total) subtask counts; (0, 0) when the task has no subtasks."""
        if not self.subtasks:
            return 0, 0
        return sum(1 for s in self.subtasks if s.get("done")), len(self.subtasks)

    def undone_fraction(self) -> float:
        """Weight fraction of subtasks not yet done (1.0 without subtasks)."""
        if not self.subtasks:
            return 1.0
        total = sum(max(float(s.get("weight", 1.0)), 0.0) for s in self.subtasks)
        if total <= 0:
            return 1.0
        undone = sum(max(float(s.get("weight", 1.0)), 0.0)
                     for s in self.subtasks if not s.get("done"))
        return undone / total

    def next_subtask(self) -> str | None:
        """Name of the first not-done subtask, or None."""
        for s in self.subtasks:
            if not s.get("done"):
                return s["name"]
        return None

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
        # Subtasks scale the estimate: only the not-yet-done weight fraction
        # still costs time. time_spent counts against that remaining portion
        # (it is reset when a subtask is completed, so overruns on a finished
        # part never eat into the estimates of the parts still to do).
        frac = self.undone_fraction()
        self.adjusted_estimate  = max(self.raw_estimate, 0.1) * RISK_MULTIPLIER * frac
        self.remaining_estimate = max(self.adjusted_estimate - self.time_spent, 0.0)

        # Deadline before first window of that day: snap to end of previous day's last window
        effective_due = self.due
        due_wins      = _windows_for_day(self.due.date())
        if not due_wins or self.due <= datetime.combine(self.due.date(), due_wins[0][0]):
            # Walk back to the previous day that has any working window
            # (busy blocks can empty a day entirely)
            for back in range(1, 8):
                prev_d    = (self.due - timedelta(days=back)).date()
                prev_wins = _windows_for_day(prev_d)
                if prev_wins:
                    effective_due = datetime.combine(prev_d, prev_wins[-1][1])
                    break

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
                    break_h: float | None = None,
                    start_after: datetime | None = None) -> tuple[list[Slice], list[Task]]:
    """
    Dynamic Least-Slack-First scheduler with time slicing.

    Instead of committing a full task at a time, repeatedly picks the most
    urgent task, works one slice, then re-evaluates priorities. This prevents
    large low-urgency tasks from blocking small urgent ones.

    Slice lengths are difficulty-dependent:
        light  (1) -- 30 min
        medium (2) -- 60 min
        deep   (3) -- 90 min

    Slices are window-aware: within the urgency band, tasks whose natural
    slice fits the remaining time in the current window are preferred, so
    short windows (recess breaks) get light/short work instead of splitting
    a deep slice across a gap. If nothing fits, the winning task's slice is
    clipped to the window; slivers shorter than min_slice_min are skipped.

    break_h parameter: override break length (pass 0.0 to disable, e.g. for panic mode).
    Defaults to the configured break_h from config.toml.

    start_after: earliest moment the first slice may begin (used to honour the
    rest break after a just-logged work session). Slack and per-task stats are
    still computed from `now`.

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
    first_at  = now if start_after is None else max(now, start_after)
    cursor    = _snap_to_next_window(first_at)
    prev_id:  str | None  = None
    # Window clipping can produce more slices than remaining/slice_h alone
    # (each short window adds one), so cap on the finest granularity.
    max_iter  = sum(
        max(1, int(remaining[t.id] / max(MIN_SLICE_H, 1e-3)) + 1)
        for t in tasks
    ) + len(tasks) + 10   # safety cap

    for _ in range(max_iter):
        # Filter to tasks with work remaining
        active = [t for t in tasks if remaining[t.id] > 1e-6]
        if not active:
            break

        # Skip window slivers too short for useful work (unless some task
        # could actually finish inside the sliver)
        cursor   = _snap_to_next_window(cursor)
        win_left = _remaining_in_window(cursor)
        min_useful = min(MIN_SLICE_H, min(remaining[t.id] for t in active))
        if win_left < min_useful - 1e-9:
            cursor = _snap_to_next_window(
                add_working_hours(cursor, max(win_left, 1e-4)))
            win_left = _remaining_in_window(cursor)

        # Recompute urgency for each active task as of cursor,
        # passing the current task id so the switch penalty applies
        for t in active:
            # Temporarily adjust remaining_estimate to the live value
            orig = t.remaining_estimate
            t.remaining_estimate = remaining[t.id]
            t.compute(cursor, current_task_id=prev_id)
            # restore so task object still reflects original for display
            t.remaining_estimate = orig

        # Urgency banding: tasks within URGENCY_BAND_PCT of the top urgency
        # compete as a group; within the group prefer least remaining work.
        # This prevents a task with marginally higher urgency from monopolising
        # every slice -- small urgent tasks push through naturally.
        max_urg    = max(t.raw_urgency for t in active)
        band_floor = max_urg * (1.0 - URGENCY_BAND_PCT)
        band       = [t for t in active if t.raw_urgency >= band_floor]

        # Window-aware pick: prefer band tasks whose natural slice fits what
        # is left of the current window, so short windows get short work.
        fitting = [t for t in band
                   if min(SLICE_H[t.difficulty], remaining[t.id])
                   <= win_left + 1e-9]
        pool    = fitting if fitting else band
        chosen  = min(pool, key=lambda t: remaining[t.id])

        # Slice size: natural slice, clipped to the window so sessions never
        # straddle a gap between windows.
        slice_h = min(SLICE_H[chosen.difficulty], remaining[chosen.id])
        if not fitting:
            slice_h = min(slice_h, win_left)

        start  = cursor
        end    = add_working_hours(cursor, slice_h)

        # If there's a tiny tail left (< 5 min) after this slice,
        # absorb it now rather than scheduling a fragment later --
        # but only if the extended slice still fits the current window.
        tail = remaining[chosen.id] - slice_h
        if 0 < tail < 5/60 and slice_h + tail <= win_left + 1e-9:
            slice_h += tail
            end = add_working_hours(cursor, slice_h)
        slices.append(Slice(chosen, slice_h, start, end))
        remaining[chosen.id] -= slice_h
        # Apply break after slice, clipped to remaining window time.
        # Breaks never push the cursor past a window boundary -- the next
        # slice picks up at the next window start naturally. The break applies
        # whenever any work follows, including when the next slice belongs to
        # a different task (finishing one task doesn't skip the rest).
        if break_h > 0 and any(remaining[t.id] > 1e-6 for t in tasks):
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
        if not wins:     # fully blocked day (busy blocks)
            check_date += timedelta(days=1)
            continue
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

