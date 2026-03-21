"""
lsf.scheduler -- persistence, display, interactive session, panic mode, and CLI entry point.
"""

import argparse
import csv
import json
import os
import uuid
from datetime import datetime, timedelta, date

from .task import (
    Task, Slice, schedule, schedule_sliced, today_slices, edf_max_subset,
    _windows_for_day, _day_total_h,
    daylight_hours_until, add_working_hours,
    RISK_MULTIPLIER, SWITCH_PENALTY_H, EPSILON,
    SLICE_H, SWITCH_URGENCY_PENALTY, BREAK_H,
    _remaining_in_window,
)
from .util import (
    prompt, parse_due_date, parse_duration,
    fmt_hours, fmt_dt, slack_bar,
)

# RISK_MULTIPLIER, SWITCH_PENALTY_H, EPSILON live in task.py (used by Task/schedule)
# and are imported via the task import block above.
DEEP_CAP_PER_DAY   = 4.0   # used only in burndown display
MEDIUM_CAP_PER_DAY = 6.0

DATA_DIR      = os.path.expanduser("~/.lsf")
DATA_FILE     = os.path.join(DATA_DIR, "tasks.json")
CSV_FILE      = os.path.join(DATA_DIR, "import.csv")
SESSION_FILE  = os.path.join(DATA_DIR, "session.json")

PRIORITY_LABELS = {
    1: "low      (homework / reading)",
    2: "medium   (quiz / minor project)",
    3: "high     (major assignment)",
    4: "critical (exam / thesis)",
}

DIFFICULTY_LABELS = {
    1: "light  (reading, MCQ, admin)",
    2: "medium (problem sets, short writing)",
    3: "deep   (essays, coding, creative work)",
}

DIFF_ICON = {1: "o", 2: "~", 3: "*"}   # light / medium / deep

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# -- Persistence --------------------------------------------------------------


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_tasks() -> list[dict]:
    ensure_data_dir()
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        print(f"  {YELLOW}Warning: could not read {DATA_FILE}, starting fresh.{RESET}")
        return []


def save_tasks(tasks: list[dict]):
    ensure_data_dir()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, default=str)



def load_session() -> dict | None:
    """Load the active session from ~/.lsf/session.json, or None if absent."""
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_session(task_id: str, duration_h: float, started_at: str):
    """Persist the current slice so lsf done knows what to mark complete."""
    ensure_data_dir()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"task_id": task_id, "duration_h": duration_h,
                   "started_at": started_at}, f)


def clear_session():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


def import_csv(existing: list[dict], csv_path: str = CSV_FILE) -> tuple[list[dict], int]:
    """
    Merge tasks from a CSV file into the existing task list.
    Tasks matched by (name, due) are left untouched to preserve progress.

    csv_path defaults to ~/.lsf/import.csv (hot-folder for automation),
    but any path can be passed explicitly via 'lsf import <file>'.
    """
    if not os.path.exists(csv_path):
        if csv_path != CSV_FILE:
            print(f"  {RED}Error: file not found: {csv_path}{RESET}")
        return existing, 0

    existing_keys = {
        (d["name"].strip().lower(), d["due"][:16])
        for d in existing
    }

    added     = 0
    errors    = []
    new_tasks = list(existing)

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            reader.fieldnames = [h.strip().lower() for h in (reader.fieldnames or [])]

            required = {"name", "due", "estimate", "priority"}
            if not required.issubset(set(reader.fieldnames)):
                print(f"  {YELLOW}Warning: import.csv missing columns "
                      f"(need: name, due, estimate, priority). Skipping import.{RESET}")
                return existing, 0

            for i, row in enumerate(reader, start=2):
                try:
                    name       = row["name"].strip()
                    due        = parse_due_date(row["due"].strip())
                    estimate   = parse_duration(row["estimate"].strip())
                    priority   = int(row["priority"].strip())
                    difficulty = int(row.get("difficulty", "2").strip())
                    if not name:
                        raise ValueError("empty name")
                    if priority not in (1, 2, 3, 4):
                        raise ValueError(f"priority must be 1-4, got {priority}")
                    if difficulty not in (1, 2, 3):
                        difficulty = 2

                    key = (name.lower(), due.isoformat()[:16])
                    if key in existing_keys:
                        continue

                    new_tasks.append({
                        "id":           str(uuid.uuid4())[:8],
                        "name":         name,
                        "due":          due.isoformat(),
                        "raw_estimate": estimate,
                        "priority":     priority,
                        "difficulty":   difficulty,
                        "time_spent":   0.0,
                    })
                    existing_keys.add(key)
                    added += 1

                except Exception as e:
                    errors.append(f"    row {i}: {e}")

    except IOError as e:
        print(f"  {YELLOW}Warning: could not read {csv_path} -- {e}{RESET}")
        return existing, 0

    if errors:
        print(f"  {YELLOW}CSV import warnings:{RESET}")
        for err in errors:
            print(f"  {YELLOW}{err}{RESET}")

    return new_tasks, added


def dict_to_task(d: dict) -> "Task":
    t = Task(
        name       = d["name"],
        due        = datetime.fromisoformat(d["due"]),
        estimate_h = d["raw_estimate"],
        priority   = d["priority"],
        task_id    = d["id"],
        difficulty = d.get("difficulty", 2),
    )
    t.time_spent = d.get("time_spent", 0.0)
    return t


def task_to_dict(t: "Task") -> dict:
    return {
        "id":           t.id,
        "name":         t.name,
        "due":          t.due.isoformat(),
        "raw_estimate": t.raw_estimate,
        "priority":     t.priority,
        "difficulty":   t.difficulty,
        "time_spent":   t.time_spent,
    }


# -- Display ------------------------------------------------------------------

def _fmt_window_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _fmt_slice_time(dt: datetime, now: datetime) -> str:
    """HH:MM for today, 'Mon 23 HH:MM' for future dates."""
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%a %d %H:%M")


def display(tasks: list[Task], slices: list[Slice], now: datetime):
    """
    Display today's timetable, per-task summary, and footer stats.
    """
    total_raw       = sum(t.raw_estimate       for t in tasks)
    total_adjusted  = sum(t.adjusted_estimate  for t in tasks)
    total_remaining = sum(t.remaining_estimate for t in tasks)
    total_spent     = sum(t.time_spent         for t in tasks)
    day_h           = _day_total_h(now.date())

    if tasks:
        latest_due      = max(t.due for t in tasks)
        available_total = daylight_hours_until(latest_due, now)
        working_days    = available_total / day_h if day_h > 0 else 0.0
        pace_per_day    = total_remaining / max(working_days, 1 / 24)
        short_horizon   = working_days < 1.0
    else:
        available_total = working_days = pace_per_day = 0.0
        short_horizon   = False

    print()
    print(f"{BOLD}{'-'*62}{RESET}")
    print(f"{BOLD}  ASSIGNMENT SCHEDULE  {DIM}(as of {now:%H:%M, %a %d %b}){RESET}")
    print(f"{BOLD}{'-'*62}{RESET}")

    # -- Today's timetable ----------------------------------------------------
    day_slices, day_start, day_end = today_slices(slices, now)
    wins = _windows_for_day(day_start.date())

    print()
    if day_start.date() == now.date():
        print(f"  {BOLD}Today's plan:{RESET}  "
              f"{DIM}({day_start.date().strftime('%a %d %b')}){RESET}")
    else:
        print(f"  {BOLD}Next working day:{RESET}  "
              f"{DIM}({day_start.date().strftime('%a %d %b')} -- "
              f"no windows remaining today){RESET}")
    print()

    if not day_slices:
        print(f"  {DIM}No work scheduled for this period.{RESET}")
    else:
        # Build timetable: show windows with assigned slices inside them
        # and gaps between windows
        slice_idx = 0
        for ws_t, we_t in wins:
            ws = datetime.combine(day_start.date(), ws_t)
            we = datetime.combine(day_start.date(), we_t)
            win_dur = round((we - ws).total_seconds() / 60)

            # Skip windows entirely in the past
            if we <= now:
                continue

            dur_h_str = (f"{win_dur // 60}h{win_dur % 60:02d}m" if win_dur >= 60
                         else f"{win_dur}m")
            print(f"  {DIM}── {_fmt_window_time(ws)}–{_fmt_window_time(we)}"
                  f" ({dur_h_str}) {'─'*40}{RESET}")

            # Slices that overlap this window
            win_slices = [s for s in day_slices
                          if s.start < we and s.end > ws]

            if not win_slices:
                print(f"    {DIM}(free){RESET}")
            else:
                prev_slice_end = None
                active_session = load_session()
                for s in win_slices:
                    # Clip to window boundaries for display
                    display_start = max(s.start, ws, now)
                    display_end   = min(s.end, we)
                    dur_min       = round((display_end - display_start
                                           ).total_seconds() / 60)
                    if dur_min <= 0:
                        continue
                    # Show break gap between consecutive slices
                    if prev_slice_end is not None and BREAK_H > 0:
                        gap_min = round((display_start - prev_slice_end
                                         ).total_seconds() / 60)
                        if 0 < gap_min <= round(BREAK_H * 60) + 1:
                            brk_start = _fmt_slice_time(prev_slice_end, now)
                            brk_end   = _fmt_window_time(display_start)
                            print(f"    {DIM}{brk_start} → {brk_end}  "
                                  f"(break {gap_min}m){RESET}")
                    diff_icon = DIFF_ICON.get(s.task.difficulty, "~")
                    late_flag = f"  {YELLOW}!{RESET}" if s.will_be_late else ""
                    is_active = (active_session and
                                 active_session.get("task_id") == s.task.id and
                                 s.start <= now <= s.end)
                    active_flag = f"  {CYAN}▶{RESET}" if is_active else ""
                    start_str = _fmt_slice_time(display_start, now)
                    end_str   = _fmt_window_time(display_end)
                    print(f"    {start_str} → {end_str}  "
                          f"{BOLD}{s.task.name}{RESET}  "
                          f"{DIM}{diff_icon} {dur_min}m{RESET}"
                          f"{late_flag}{active_flag}")
                    prev_slice_end = display_end
            print()

    # Show how many more slices are scheduled beyond today
    remaining_slices = [s for s in slices if s not in day_slices]
    if remaining_slices:
        future_tasks: dict[str, float] = {}
        for s in remaining_slices:
            future_tasks[s.task.name] = future_tasks.get(s.task.name, 0) + s.duration_h
        next_day = day_start.date() + timedelta(days=1)
        # find next actual working day
        for d in range(1, 8):
            nd = day_start.date() + timedelta(days=d)
            if _day_total_h(nd) > 0:
                next_day = nd
                break
        print(f"  {DIM}Continuing {next_day.strftime('%a %d %b')} onwards:{RESET}")
        for _name, _h in future_tasks.items():
            print(f"    {DIM}{_name}  ({fmt_hours(_h)}){RESET}")
        print()

    # -- Per-task summary ------------------------------------------------------
    print(f"{BOLD}{'-'*62}{RESET}")
    print(f"  {BOLD}Task summary:{RESET}")
    print()

    # Pre-compute so task loop and burndown section share the same value
    _collectively_overloaded = (
        not any(t.overloaded for t in tasks)
        and total_remaining > available_total
    )

    from collections import defaultdict
    tasks_by_due: dict = defaultdict(list)
    for _t in sorted(tasks, key=lambda t: (0 if t.overloaded else 1, t.due, -t.urgency)):
        tasks_by_due[_t.due.date()].append(_t)

    display_tasks = [t for ts in tasks_by_due.values() for t in ts]
    _prev_due_date = None

    for i, t in enumerate(display_tasks, 1):
        if t.due.date() != _prev_due_date:
            _prev_due_date = t.due.date()
            _due_label = ("today" if t.due.date() == now.date() else
                          "tomorrow" if t.due.date() == (now + timedelta(days=1)).date()
                          else t.due.strftime('%a %d %b'))
            if i > 1: print()
            print(f"  {DIM}── due {_due_label} {'─'*40}{RESET}")
            print()

        if t.overloaded:
            icon   = f"{RED}x{RESET}"
            status = f"{RED}IMPOSSIBLE -- not enough time{RESET}"
        elif t.will_be_late and _collectively_overloaded:
            # Late because total workload exceeds capacity, not individually behind
            icon   = f"{DIM}~{RESET}"
            status = f"{DIM}delayed by workload{RESET}"
        elif t.will_be_late:
            icon   = f"{YELLOW}!{RESET}"
            status = f"{YELLOW}WILL BE LATE{RESET}"
        else:
            icon   = f"{GREEN}[x]{RESET}"
            status = f"{GREEN}on track{RESET}"

        slack_col  = RED if t.slack < 0 else (YELLOW if t.slack < 2 else GREEN)
        slack_str  = (f"+{fmt_hours(t.slack)}" if t.slack >= 0
                      else fmt_hours(t.slack))
        spent_note = f"  {DIM}({fmt_hours(t.time_spent)} spent){RESET}" if t.time_spent > 0 else ""
        diff_icon  = DIFF_ICON.get(t.difficulty, "~")

        stars = '*' * t.priority
        print(f"  {BOLD}{i}. {t.name}{RESET}  "
              f"{DIM}{diff_icon} {stars} [{t.id}]{RESET}")
        print(f"     Due:       {fmt_dt(t.due)}")
        print(f"     Estimate:  {fmt_hours(t.raw_estimate)} → "
              f"{fmt_hours(t.adjusted_estimate)} adjusted{spent_note}")
        print(f"     Remaining: {BOLD}{fmt_hours(t.remaining_estimate)}{RESET}  "
              f"Slack: {slack_col}{BOLD}{slack_str}{RESET}")
        print(f"     Finish ~{fmt_dt(t.finish_time)}  "
              f"{slack_bar(t.slack, t.adjusted_estimate)}  {status}")
        print()

    # -- Footer ----------------------------------------------------------------
    print(f"{BOLD}{'-'*62}{RESET}")
    print(f"  Tasks:                 {len(tasks)}")
    print(f"  Today's sessions:      {len(day_slices)}")
    print(f"  Total sessions:        {len(slices)}")
    print(f"  Total work (raw):      {fmt_hours(total_raw)}")
    print(f"  Total work (adjusted): {fmt_hours(total_adjusted)}")
    if total_spent > 0:
        print(f"  Already spent:         {fmt_hours(total_spent)}")
    print(f"  Remaining work:        {BOLD}{fmt_hours(total_remaining)}{RESET}")
    if short_horizon:
        pace_col = (RED    if total_remaining > available_total else
                    YELLOW if total_remaining > available_total * 0.8 else GREEN)
        print(f"  Hours needed today:    {BOLD}{pace_col}{fmt_hours(total_remaining)}{RESET}"
              f"  {DIM}(of {fmt_hours(available_total)} available){RESET}")
    else:
        pace_col = (RED    if pace_per_day > day_h * 0.9 else
                    YELLOW if pace_per_day > day_h * 0.6 else GREEN)
        print(f"  Required daily pace:   {BOLD}{pace_col}{fmt_hours(pace_per_day)}/day{RESET}"
              f"  {DIM}(over {working_days:.1f} working days){RESET}")
    print(f"{BOLD}{'-'*62}{RESET}")

    # -- Next session callout --------------------------------------------------
    if slices:
        first = slices[0]
        t     = first.task
        dur   = round(first.duration_h * 60)
        in_window = _remaining_in_window(now) > 0
        print()
        if t.overloaded:
            print(f"  {BOLD}Start now:{RESET}  "
                  f"{RED}OVERLOADED -- negotiate deadline or cut scope{RESET}")
        elif in_window:
            print(f"  {BOLD}Start now:{RESET}  "
                  f"{GREEN}{BOLD}▶ {t.name}{RESET}  {DIM}({dur} min){RESET}")
        else:
            # Outside working hours -- show when the next session starts
            next_start = first.start
            print(f"  {BOLD}Next session:{RESET}  "
                  f"{DIM}▶ {t.name}  ({dur} min)  "
                  f"starting {next_start.strftime('%a %H:%M')}{RESET}")

    # -- Collective overload warning -------------------------------------------
    collectively_overloaded = _collectively_overloaded
    if collectively_overloaded:
        shortfall = total_remaining - available_total
        print()
        print(f"{BOLD}{'-'*62}{RESET}")
        print(f"{BOLD}  {YELLOW}COLLECTIVE OVERLOAD{RESET}{BOLD}  "
              f"{DIM}workload exceeds available time by {fmt_hours(shortfall)}{RESET}")
        print(f"  {DIM}Every task has positive slack individually, but there is not"
              f" enough total time for all of them.{RESET}")
        print(f"{BOLD}{'-'*62}{RESET}")
        print(f"  {RED}Run {BOLD}lsf panic{RESET}{RED} for a triage plan.{RESET}")
        print()

    # -- Burndown forecast (individually impossible tasks only) ----------------
    overloaded_tasks = [t for t in tasks if t.overloaded]
    if not overloaded_tasks:
        if not _collectively_overloaded:
            print()
        return

    print()
    print(f"{BOLD}{'-'*62}{RESET}")
    print(f"{BOLD}  BURNDOWN FORECAST  {DIM}(overloaded workload detected){RESET}")
    print(f"{BOLD}{'-'*62}{RESET}")
    print()

    deep_h   = sum(t.remaining_estimate for t in tasks if t.difficulty == 3)
    medium_h = sum(t.remaining_estimate for t in tasks if t.difficulty == 2)
    light_h  = sum(t.remaining_estimate for t in tasks if t.difficulty == 1)

    if deep_h > 0:
        days_deep = deep_h / DEEP_CAP_PER_DAY
        print(f"  {DIFF_ICON[3]} Deep work:   {fmt_hours(deep_h):>8}  "
              f"-> {days_deep:.1f} days  {DIM}(max {fmt_hours(DEEP_CAP_PER_DAY)}/day){RESET}")
    if medium_h > 0:
        days_med = medium_h / MEDIUM_CAP_PER_DAY
        print(f"  {DIFF_ICON[2]} Medium work: {fmt_hours(medium_h):>8}  "
              f"-> {days_med:.1f} days  {DIM}(max {fmt_hours(MEDIUM_CAP_PER_DAY)}/day){RESET}")
    if light_h > 0:
        days_light = light_h / day_h if day_h > 0 else 0
        print(f"  {DIFF_ICON[1]} Light work:  {fmt_hours(light_h):>8}  "
              f"-> {days_light:.1f} days  {DIM}(max {fmt_hours(day_h)}/day){RESET}")

    print()
    print(f"  {BOLD}What it would take to meet each missed deadline:{RESET}")
    print()
    for t in overloaded_tasks:
        shortfall   = -t.slack
        avail_days  = max(daylight_hours_until(t.due, now) / day_h, 1 / 24) if day_h > 0 else 1
        extra_daily = shortfall / avail_days
        print(f"  {RED}x{RESET} {t.name}")
        print(f"      Shortfall: {fmt_hours(shortfall)}  |  "
              f"Need +{fmt_hours(extra_daily)}/day to finish on time")

    print()
    print(f"  {RED}Run {BOLD}lsf panic{RESET}{RED} for a triage plan.{RESET}")
    print()
    print(f"{BOLD}{'-'*62}{RESET}")
    print()

# -- Session update flow ------------------------------------------------------

def update_session(tasks: list[Task]) -> tuple[list[Task], bool]:
    if not tasks:
        return tasks, False

    changed = False

    print(f"  {BOLD}Mark completed assignments as done{RESET}")
    print(f"  {DIM}Enter task numbers separated by spaces, or press Enter to skip.{RESET}")
    print()
    for i, t in enumerate(tasks, 1):
        spent_note = f"  {DIM}({fmt_hours(t.time_spent)} spent){RESET}" if t.time_spent > 0 else ""
        print(f"    {CYAN}{i}.{RESET} {t.name}  {DIM}(due {fmt_dt(t.due)}){RESET}{spent_note}")
    print()

    raw = input("  Done (e.g. 1 3): ").strip()
    if raw:
        done_indices = set()
        for tok in raw.split():
            try:
                idx = int(tok)
                if 1 <= idx <= len(tasks):
                    done_indices.add(idx - 1)
            except ValueError:
                pass
        if done_indices:
            removed = [tasks[i].name for i in sorted(done_indices)]
            tasks   = [t for i, t in enumerate(tasks) if i not in done_indices]
            print()
            for name in removed:
                print(f"  {GREEN}[x] Removed: {name}{RESET}")
            changed = True

    print()

    if not tasks:
        return tasks, changed

    print(f"  {BOLD}Log time already spent on an assignment{RESET}")
    print(f"  {DIM}Enter task number and time (e.g. '2 1h30m'), or press Enter to skip.{RESET}")
    print(f"  {DIM}You can update multiple tasks one at a time.{RESET}")
    print()
    for i, t in enumerate(tasks, 1):
        spent_note = f"  {DIM}(currently {fmt_hours(t.time_spent)} logged){RESET}" if t.time_spent > 0 else ""
        print(f"    {CYAN}{i}.{RESET} {t.name}{spent_note}")
    print()

    while True:
        raw = input("  Log time (e.g. '2 1h30m', blank to finish): ").strip()
        if not raw:
            break
        parts = raw.split(None, 1)
        if len(parts) != 2:
            print(f"  {RED}  Format: <number> <time>  e.g. '1 2h'{RESET}")
            continue
        try:
            idx   = int(parts[0])
            hours = parse_duration(parts[1])
            if not (1 <= idx <= len(tasks)):
                raise ValueError
        except ValueError:
            print(f"  {RED}  Invalid input.{RESET}")
            continue

        task = tasks[idx - 1]
        task.time_spent = hours
        print(f"  {GREEN}[x] {task.name}: logged {fmt_hours(hours)} spent{RESET}")
        changed = True

    print()
    return tasks, changed


def add_new_tasks(tasks: list[Task]) -> tuple[list[Task], bool]:
    print(f"  {BOLD}Add new assignments{RESET}")
    print(f"  {DIM}(leave name blank to finish){RESET}")
    print()

    added = False
    while True:
        print(f"  {CYAN}New task{RESET}")
        name = prompt("Name (blank to finish)")
        if not name:
            break

        while True:
            try:
                raw_due = prompt("Due date", "tomorrow 23:59")
                due     = parse_due_date(raw_due)
                break
            except ValueError as e:
                print(f"  {RED}  {e}{RESET}")

        while True:
            try:
                raw_est  = prompt("Estimated time (e.g. 2h, 90m, 1h30m)")
                estimate = parse_duration(raw_est)
                break
            except ValueError:
                print(f"  {RED}  Could not parse duration.{RESET}")

        print("  Priority levels:")
        for k, v in PRIORITY_LABELS.items():
            print(f"    {k} = {v}")
        while True:
            try:
                priority = int(prompt("Priority", "1"))
                if priority not in PRIORITY_LABELS:
                    raise ValueError
                break
            except ValueError:
                print(f"  {RED}  Enter 1-4{RESET}")

        print("  Difficulty levels:")
        for k, v in DIFFICULTY_LABELS.items():
            print(f"    {k} = {v}")
        while True:
            try:
                difficulty = int(prompt("Difficulty", "2"))
                if difficulty not in DIFFICULTY_LABELS:
                    raise ValueError
                break
            except ValueError:
                print(f"  {RED}  Enter 1-3{RESET}")

        tasks.append(Task(name, due, estimate, priority, difficulty=difficulty))
        print(f"  {GREEN}[x] Added: {name}{RESET}")
        print()
        added = True

    return tasks, added


# -- Panic mode ---------------------------------------------------------------

def panic(tasks: list[Task], now: datetime):
    """
    lsf panic -- survival triage for overloaded schedules.

    Uses EDF (Earliest Deadline First) to find the maximum subset of tasks
    that can all be completed on time. Impossible tasks (slack < 0) are
    reported as write-offs first. The rest are run through EDF to find the
    optimal achievable set, and deferred tasks are listed clearly.
    """
    day_h = _day_total_h(now.date())

    # First pass: identify individually impossible tasks.
    # Recompute slack fresh at `now` -- cached t.slack may be stale if tasks
    # were computed earlier in the session.
    def _fresh_slack(t):
        return daylight_hours_until(t.due, now) - t.remaining_estimate

    impossible = [t for t in tasks if _fresh_slack(t) < 0]
    pos_slack  = [t for t in tasks if _fresh_slack(t) >= 0]

    # EDF on positive-slack tasks to find the maximum on-time subset
    edf_on_time, edf_deferred = edf_max_subset(pos_slack, now)

    total_remaining = sum(t.remaining_estimate for t in pos_slack)
    latest_due      = max((t.due for t in pos_slack), default=now)
    available_total = daylight_hours_until(latest_due, now)
    working_days    = available_total / day_h if day_h > 0 else 0.0

    print()
    print(f"{BOLD}{'-'*62}{RESET}")
    print(f"{BOLD}  PANIC MODE  {DIM}(as of {now:%H:%M, %a %d %b}){RESET}")
    print(f"{BOLD}{'-'*62}{RESET}")
    print()

    # All clear
    if not impossible and not edf_deferred:
        min_daily = total_remaining / working_days if working_days > 0 else 0.0
        print(f"  {GREEN}All tasks are achievable. You're fine.{RESET}")
        print(f"  Minimum daily pace: {fmt_hours(min_daily)}/day over {working_days:.1f} days.")
        print()
        print(f"{BOLD}{'-'*62}{RESET}")
        print()
        return

    # -- Write-offs ------------------------------------------------------------
    if impossible:
        print(f"  {BOLD}{RED}Write-offs ({len(impossible)}) -- I'm sorry, there is nothing more LSF can do for you - suhao49:{RESET}")
        for t in impossible:
            print(f"    {RED}x{RESET} {t.name}  "
                  f"{DIM}due {fmt_dt(t.due)}, short by {fmt_hours(-t.slack)}{RESET}")
        print()
        if not pos_slack:
            print(f"  {RED}Nothing left to salvage.{RESET}")
            print()
            print(f"{BOLD}{'-'*62}{RESET}")
            print()
            return

    # -- EDF optimal schedule --------------------------------------------------
    shortfall = total_remaining - available_total
    if shortfall > 0:
        print(f"  {YELLOW}Workload exceeds available time by {fmt_hours(shortfall)}.{RESET}")
        print(f"  {DIM}EDF found the maximum set that fits -- "
              f"{len(edf_on_time)} task(s) on time, "
              f"{len(edf_deferred)} must be deferred.{RESET}")
    print()

    if edf_on_time:
        print(f"  {BOLD}{GREEN}Achievable on time ({len(edf_on_time)}) -- focus on these:{RESET}")
        for t in sorted(edf_on_time, key=lambda t: t.due):
            diff_icon = DIFF_ICON.get(t.difficulty, '~')
            stars     = '*' * t.priority + '.' * (4 - t.priority)
            print(f"    {GREEN}[x]{RESET} {BOLD}{t.name}{RESET}  "
                  f"{DIM}{diff_icon} {stars}  "
                  f"due {fmt_dt(t.due)}  "
                  f"est {fmt_hours(t.remaining_estimate)}{RESET}")
        print()

    if edf_deferred:
        print(f"  {BOLD}{YELLOW}Defer or drop ({len(edf_deferred)}):{RESET}")
        for t in sorted(edf_deferred, key=lambda t: t.due):
            diff_icon = DIFF_ICON.get(t.difficulty, '~')
            stars     = '*' * t.priority + '.' * (4 - t.priority)
            print(f"    {YELLOW}~{RESET} {t.name}  "
                  f"{DIM}{diff_icon} {stars}  "
                  f"due {fmt_dt(t.due)}  "
                  f"est {fmt_hours(t.remaining_estimate)}{RESET}")
        print()

    # -- Optimal session plan (EDF subset only) --------------------------------
    if edf_on_time:
        print(f"  {BOLD}Optimal session plan (on-time tasks only):{RESET}")
        print()
        opt_slices, _ = schedule_sliced(edf_on_time, now)
        opt_day_slices, _, _ = today_slices(opt_slices, now)

        wins = _windows_for_day(now.date())
        printed_any = False
        for ws_t, we_t in wins:
            ws = datetime.combine(now.date(), ws_t)
            we = datetime.combine(now.date(), we_t)
            if we <= now:
                continue
            win_slices = [s for s in opt_day_slices if s.start < we and s.end > ws]
            if not win_slices:
                continue
            win_dur = round((we - ws).total_seconds() / 60)
            print(f"  {DIM}-- {ws_t.strftime("%H:%M")} - "
                  f"{we_t.strftime("%H:%M")} ({win_dur}m) --{RESET}")
            for s in win_slices:
                disp_start = max(s.start, ws, now)
                disp_end   = min(s.end, we)
                dur_min    = round((disp_end - disp_start).total_seconds() / 60)
                if dur_min <= 0:
                    continue
                diff_icon = DIFF_ICON.get(s.task.difficulty, "~")
                print(f"    {disp_start:%H:%M} -> {disp_end:%H:%M}  "
                      f"{BOLD}{s.task.name}{RESET}  "
                      f"{DIM}{diff_icon} {dur_min}m{RESET}")
            print()
            printed_any = True

        if not printed_any:
            print(f"  {DIM}No windows remaining today -- "
                  f"first session tomorrow.{RESET}")
            print()

        # Day-by-day beyond today
        future = [s for s in opt_slices if s not in opt_day_slices]
        if future:
            by_day: dict[date, float] = {}
            for s in future:
                d = s.start.date()
                by_day[d] = by_day.get(d, 0.0) + s.duration_h
            for d, h in list(by_day.items())[:7]:
                d_h   = _day_total_h(d)
                bar_w = int((h / max(d_h, 0.01)) * 20)
                bar   = GREEN + "|" * bar_w + DIM + "." * (20 - bar_w) + RESET
                intensity = ("heavy"    if h > d_h * 0.7 else
                             "moderate" if h > d_h * 0.4 else "light")
                print(f"    {d.strftime("%a %d %b")}  "
                      f"{fmt_hours(h):>6}  {bar}  {DIM}{intensity}{RESET}")
            if len(by_day) > 7:
                print(f"    {DIM}... +{len(by_day) - 7} more day(s){RESET}")
            print()

    print(f"{BOLD}{'-'*62}{RESET}")
    print()


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="lsf",
        description="Least Slack First assignment scheduler"
    )
    parser.add_argument(
        "command", nargs="?", default="schedule",
        choices=["schedule", "panic", "import", "start", "done"],
        help=(
            "schedule (default) -- interactive schedule; "
            "panic -- survival triage when overloaded; "
            "import -- import tasks from a CSV file; "
            "start -- begin the current slice (saves to session.json); "
            "done -- mark current slice complete and add time to task"
        )
    )
    parser.add_argument(
        "file", nargs="?", default=None,
        help="CSV file to import (used with the 'import' command)"
    )
    parser.add_argument(
        "--next", action="store_true",
        help="Machine-readable output: print next task name and session minutes, then exit"
    )
    args = parser.parse_args()

    # -- lsf start [n] -------------------------------------------------
    if args.command == "start":
        raw    = load_tasks()
        tasks  = [dict_to_task(d) for d in raw]
        if not tasks:
            print("  No tasks found.")
            return
        _now = datetime.now()
        slices, _ = schedule_sliced(tasks, _now)
        if not slices:
            print("  No sessions to start.")
            return

        # args.file is reused as the optional slice number argument
        # e.g. "lsf start 3" starts the 3rd slice in today's plan
        day_slices, _, _ = today_slices(slices, _now)
        if not day_slices:
            day_slices = slices   # outside windows -- show all

        chosen = None
        if args.file is not None:
            try:
                n = int(args.file)
                if 1 <= n <= len(day_slices):
                    chosen = day_slices[n - 1]
                else:
                    print(f"  {RED}Invalid slice number. Today has {len(day_slices)} session(s).{RESET}")
                    return
            except ValueError:
                print(f"  {RED}Usage: lsf start [session number]{RESET}")
                return

        if chosen is None:
            if len(day_slices) == 1:
                chosen = day_slices[0]
            else:
                # Show a numbered list and ask
                print()
                print(f"  {BOLD}Choose a session to start:{RESET}")
                print()
                for i, s in enumerate(day_slices, 1):
                    dur_min  = round(s.duration_h * 60)
                    d_icon   = DIFF_ICON.get(s.task.difficulty, "~")
                    start_s  = _fmt_slice_time(s.start, _now)
                    end_s    = _fmt_window_time(s.end)
                    print(f"    {CYAN}{i}.{RESET} {start_s} -> {end_s}  "
                          f"{s.task.name}  {DIM}{d_icon} {dur_min}m{RESET}")
                print()
                raw_choice = prompt("Session number", "1")
                try:
                    n = int(raw_choice)
                    if 1 <= n <= len(day_slices):
                        chosen = day_slices[n - 1]
                    else:
                        print(f"  {RED}Invalid number.{RESET}")
                        return
                except ValueError:
                    print(f"  {RED}Invalid input.{RESET}")
                    return

        save_session(chosen.task.id, chosen.duration_h,
                     datetime.now().isoformat())
        dur_min = round(chosen.duration_h * 60)
        print(f"\n  {GREEN}>>  Started: {chosen.task.name}  ({dur_min} min){RESET}")
        print(f"  {DIM}Run 'lsf done' when finished.{RESET}\n")
        return

    # -- lsf done -----------------------------------------------------
    if args.command == "done":
        session = load_session()
        if not session:
            print(f"  {YELLOW}No active session. Run 'lsf start' first.{RESET}")
            return
        raw   = load_tasks()
        tasks = [dict_to_task(d) for d in raw]
        target = next((t for t in tasks if t.id == session["task_id"]), None)
        if not target:
            print(f"  {YELLOW}Session task no longer exists.{RESET}")
            clear_session()
            return
        started    = datetime.fromisoformat(session["started_at"])
        actual_h   = (datetime.now() - started).total_seconds() / 3600
        logged_h   = session["duration_h"]
        # Use actual time if it's within 20% of planned, otherwise use planned
        # This prevents accidental huge logs if the user forgot to run done
        used_h     = actual_h if abs(actual_h - logged_h) / max(logged_h, 0.1) < 0.2 \
                     else logged_h
        # Find and update the matching task in raw dicts
        for d in raw:
            if d["id"] == session["task_id"]:
                d["time_spent"] = round(d.get("time_spent", 0.0) + used_h, 4)
                break
        save_tasks(raw)
        clear_session()
        print(f"\n  {GREEN}[x]  Done: {target.name}  "
              f"(+{fmt_hours(used_h)} logged, "
              f"{fmt_hours(d['time_spent'])} total){RESET}\n")
        return

    # -- lsf import [file] --------------------------------------------
    if args.command == "import":
        csv_path = args.file or CSV_FILE
        raw      = load_tasks()
        raw, n   = import_csv(raw, csv_path)
        if n:
            save_tasks(raw)
            print(f"  {GREEN}[x] Imported {n} new task(s) from {csv_path}{RESET}")
        else:
            print(f"  No new tasks found in {csv_path}")
        return

    now = datetime.now()
    raw = load_tasks()

    # Hot-folder auto-import from ~/.lsf/import.csv
    raw, csv_added = import_csv(raw)
    if csv_added:
        save_tasks(raw)
        if not args.next:
            print(f"\n  {GREEN}[x] Auto-imported {csv_added} new task(s) from import.csv{RESET}")

    tasks = [dict_to_task(d) for d in raw]

    if not tasks and args.next:
        return   # nothing to report to fastfetch

    slices, ordered = schedule_sliced(tasks, now) if tasks else ([], [])

    # --next: machine-readable output for fastfetch / status bars
    # Uses the first slice so the session length matches the sliced plan
    if args.next:
        if slices:
            t       = slices[0].task
            dur_min = round(slices[0].duration_h * 60)
        else:
            t       = ordered[0]
            dur_min = 0
        if t.overloaded:
            print(f"{t.name}\n0")
        else:
            print(f"{t.name}\n{dur_min}")
        return

    # panic mode: only available when the scheduler detects overload
    if args.command == "panic":
        # Check for overload conditions using the same logic as display()
        _check_slices, _check_ordered = schedule_sliced(tasks, now)
        _total_rem   = sum(t.remaining_estimate for t in _check_ordered)
        _latest_due  = max(t.due for t in _check_ordered)
        _avail_total = daylight_hours_until(_latest_due, now)
        _any_impossible      = any(t.overloaded for t in _check_ordered)
        _collectively_over   = _total_rem > _avail_total and not _any_impossible
        _overload_detected   = _any_impossible or _collectively_over
        if not _overload_detected:
            print()
            print(f"  {GREEN}[x] No overload detected -- lsf panic is not needed.{RESET}")
            print(f"  {DIM}Run 'lsf' to see your schedule.{RESET}")
            print()
            return
        panic_tasks = schedule(tasks, now)
        panic(panic_tasks, now)
        return

    # Default: full interactive schedule
    print()
    print(f"{BOLD}{'-'*62}{RESET}")
    print(f"{BOLD}  lsf -- Least Slack First{RESET}  {DIM}(as of {now:%H:%M, %a %d %b}){RESET}")
    print(f"{BOLD}{'-'*62}{RESET}")
    if tasks:
        print(f"  {DIM}{len(tasks)} task(s) loaded from ~/.lsf/tasks.json{RESET}")
    else:
        print(f"  {DIM}No saved tasks found.{RESET}")
    print()

    changed = False
    if tasks:
        print("  What would you like to do first?")
        print(f"    {CYAN}1.{RESET} Update progress (mark done / log time spent)")
        print(f"    {CYAN}2.{RESET} Add new assignments")
        print(f"    {CYAN}3.{RESET} Both")
        print(f"    {CYAN}4.{RESET} Just show the schedule")
        print()
        choice = prompt("Choice", "4").strip()
    else:
        choice = "2"   # no tasks -- go straight to adding
    if choice in ("1", "3"):
        tasks, c = update_session(tasks)
        changed = changed or c
    if choice in ("2", "3"):
        tasks, c = add_new_tasks(tasks)
        changed = changed or c

    if not tasks:
        print("  No tasks to display. Exiting.")
        if changed:
            save_tasks([])
        return

    if changed:
        save_tasks([task_to_dict(t) for t in tasks])
        print(f"  {DIM}Saved to ~/.lsf/tasks.json{RESET}")
        print()
        slices, ordered = schedule_sliced(tasks, now)   # re-run after changes

    display(ordered, slices, now)


if __name__ == "__main__":
    main()