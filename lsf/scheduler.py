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
    Task, schedule,
    _windows_for_day, _day_total_h,
    daylight_hours_until, add_working_hours,
    RISK_MULTIPLIER, SWITCH_PENALTY_H, EPSILON,
)
from .util import (
    prompt, parse_due_date, parse_duration,
    fmt_hours, fmt_dt, slack_bar,
)

# RISK_MULTIPLIER, SWITCH_PENALTY_H, EPSILON live in task.py (used by Task/schedule)
# and are imported via the task import block above.
DEEP_CAP_PER_DAY   = 4.0   # used only in burndown display
MEDIUM_CAP_PER_DAY = 6.0

DATA_DIR  = os.path.expanduser("~/.lsf")
DATA_FILE = os.path.join(DATA_DIR, "tasks.json")
CSV_FILE  = os.path.join(DATA_DIR, "import.csv")

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

def display(tasks: list[Task], now: datetime):
    total_raw       = sum(t.raw_estimate       for t in tasks)
    total_adjusted  = sum(t.adjusted_estimate  for t in tasks)
    total_remaining = sum(t.remaining_estimate for t in tasks)
    total_spent     = sum(t.time_spent         for t in tasks)
    day_h           = _day_total_h(now.date())   # hours available today per config

    if tasks:
        latest_due      = max(t.due for t in tasks)
        available_total = daylight_hours_until(latest_due, now)
        # Working days = available hours / typical day length (use today's schedule)
        working_days    = available_total / day_h if day_h > 0 else 0.0
        pace_per_day    = total_remaining / max(working_days, 1 / 24)
        short_horizon   = working_days < 1.0
    else:
        available_total = working_days = pace_per_day = 0.0
        short_horizon   = False

    print()
    print(f"{BOLD}{'─'*62}{RESET}")
    print(f"{BOLD}  ASSIGNMENT SCHEDULE  {DIM}(as of {now:%H:%M, %a %d %b}){RESET}")
    print(f"{BOLD}{'─'*62}{RESET}")
    print()

    for i, t in enumerate(tasks, 1):
        if t.overloaded:
            icon   = f"{RED}x{RESET}"
            status = f"{RED}IMPOSSIBLE -- not enough time{RESET}"
        elif t.will_be_late:
            icon   = f"{YELLOW}!{RESET}"
            status = f"{YELLOW}WILL BE LATE in this order{RESET}"
        else:
            icon   = f"{GREEN}✓{RESET}"
            status = f"{GREEN}on track{RESET}"

        slack_col  = RED if t.slack < 0 else (YELLOW if t.slack < 2 else GREEN)
        spent_note = f"  {DIM}({fmt_hours(t.time_spent)} spent){RESET}" if t.time_spent > 0 else ""
        diff_icon  = DIFF_ICON.get(t.difficulty, "~")

        print(f"  {BOLD}{i}. {t.name}{RESET}  {icon}  {DIM}{diff_icon} [{t.id}]{RESET}")
        print(f"     Due:       {fmt_dt(t.due)}")
        print(f"     Estimate:  {fmt_hours(t.raw_estimate)} raw -> "
              f"{fmt_hours(t.adjusted_estimate)} adjusted"
              f"{spent_note} -> {BOLD}{fmt_hours(t.remaining_estimate)} remaining{RESET}")
        print(f"     Available: {fmt_hours(t.available_h)}  |  "
              f"Slack: {slack_col}{fmt_hours(t.slack)}{RESET}")
        print(f"     Priority:  {'★' * t.priority}{'☆' * (4 - t.priority)}  |  "
              f"Finish ~{fmt_dt(t.finish_time)}")
        print(f"     {slack_bar(t.slack, t.adjusted_estimate)}  {status}")
        print()

    # Summary footer
    print(f"{BOLD}{'─'*62}{RESET}")
    print(f"  Tasks:                 {len(tasks)}")
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
    print(f"{BOLD}{'─'*62}{RESET}")

    # Next task callout
    next_task = tasks[0]
    if next_task.overloaded:
        next_label = f"{RED}OVERLOADED -- negotiate deadline or cut scope{RESET}"
    else:
        suggest_h   = min(next_task.remaining_estimate, 1.5)
        suggest_min = max(15, round(suggest_h * 60 / 15) * 15)
        next_label  = (f"{GREEN}>> {next_task.name}{RESET}"
                       f"  {DIM}-- suggested session: {suggest_min} min{RESET}")
    print()
    print(f"  {BOLD}Start now:{RESET}  {next_label}")

    # Burndown forecast -- only shown when at least one task is overloaded
    overloaded_tasks = [t for t in tasks if t.overloaded]
    if not overloaded_tasks:
        print()
        return

    print()
    print(f"{BOLD}{'─'*62}{RESET}")
    print(f"{BOLD}  BURNDOWN FORECAST  {DIM}(overloaded workload detected){RESET}")
    print(f"{BOLD}{'─'*62}{RESET}")
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
        days_light = light_h / day_h
        print(f"  {DIFF_ICON[1]} Light work:  {fmt_hours(light_h):>8}  "
              f"-> {days_light:.1f} days  {DIM}(max {fmt_hours(day_h)}/day){RESET}")

    print()
    print(f"  {BOLD}What it would take to meet each missed deadline:{RESET}")
    print()
    for t in overloaded_tasks:
        shortfall   = -t.slack
        avail_days  = max(daylight_hours_until(t.due, now) / day_h, 1 / 24)
        extra_daily = shortfall / avail_days
        print(f"  {RED}x{RESET} {t.name}")
        print(f"      Shortfall: {fmt_hours(shortfall)}  |  "
              f"Need +{fmt_hours(extra_daily)}/day to finish on time")

    print()
    total_shortfall         = sum(-t.slack for t in overloaded_tasks)
    earliest_overloaded_due = min(t.due for t in overloaded_tasks)
    days_to_rescue          = max(
        daylight_hours_until(earliest_overloaded_due, now) / day_h, 1 / 24
    )
    rescue_pace = total_shortfall / days_to_rescue
    print(f"  {BOLD}To rescue all overloaded tasks:{RESET}  "
          f"work an extra {YELLOW}{fmt_hours(rescue_pace)}/day{RESET} above your current pace")
    print(f"  until {fmt_dt(earliest_overloaded_due)}.")
    print()
    print(f"{BOLD}{'─'*62}{RESET}")
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
                print(f"  {GREEN}✓ Removed: {name}{RESET}")
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
        print(f"  {GREEN}✓ {task.name}: logged {fmt_hours(hours)} spent{RESET}")
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
        print(f"  {GREEN}✓ Added: {name}{RESET}")
        print()
        added = True

    return tasks, added


# -- Panic mode ---------------------------------------------------------------

def panic(tasks: list[Task], now: datetime):
    """
    lsf panic -- survival triage for overloaded schedules.
    Shows minimum daily work needed, which deadlines are at risk,
    and which low-priority tasks can be dropped to recover headroom.
    """
    day_h = _day_total_h(now.date())   # use today's schedule as representative

    overloaded  = [t for t in tasks if t.overloaded]
    will_late   = [t for t in tasks if t.will_be_late and not t.overloaded]
    droppable   = sorted(
        [t for t in tasks if t.priority == 1],
        key=lambda t: t.remaining_estimate, reverse=True
    )

    total_remaining = sum(t.remaining_estimate for t in tasks)
    hours_behind    = sum(-t.slack for t in overloaded) if overloaded else 0.0
    latest_due      = max(t.due for t in tasks)
    available_total = daylight_hours_until(latest_due, now)
    working_days    = max(available_total / day_h, 1 / 24) if day_h > 0 else 1.0
    min_daily       = total_remaining / working_days

    deep_h   = sum(t.remaining_estimate for t in tasks if t.difficulty == 3)
    medium_h = sum(t.remaining_estimate for t in tasks if t.difficulty == 2)
    light_h  = sum(t.remaining_estimate for t in tasks if t.difficulty == 1)

    print()
    print(f"{BOLD}{'─'*62}{RESET}")
    print(f"{BOLD}  PANIC MODE  {DIM}(as of {now:%H:%M, %a %d %b}){RESET}")
    print(f"{BOLD}{'─'*62}{RESET}")
    print()

    if not overloaded and not will_late:
        print(f"  {GREEN}No overloaded or late tasks. You're actually fine.{RESET}")
        print(f"  Minimum daily pace: {fmt_hours(min_daily)}/day over {working_days:.1f} days.")
        print()
        return

    if hours_behind > 0:
        print(f"  {RED}You are {fmt_hours(hours_behind)} behind schedule.{RESET}")
    elif will_late:
        print(f"  {YELLOW}{len(will_late)} task(s) will be late in the current order.{RESET}")
    print()

    if overloaded:
        print(f"  {BOLD}Impossible tasks ({len(overloaded)}):{RESET}")
        for t in overloaded:
            print(f"    {RED}x{RESET} {t.name}  {DIM}(due {fmt_dt(t.due)}, "
                  f"short by {fmt_hours(-t.slack)}){RESET}")
        print()

    if will_late:
        print(f"  {BOLD}Will be late in current order ({len(will_late)}):{RESET}")
        for t in will_late:
            print(f"    {YELLOW}!{RESET} {t.name}  {DIM}(due {fmt_dt(t.due)}){RESET}")
        print()

    # Day-by-day survival plan using actual configured windows
    print(f"  {BOLD}Minimum survival plan:{RESET}")
    print()

    cursor    = now
    plan_days = []
    work_left = total_remaining
    for _ in range(30):
        if work_left <= 0:
            break
        d_h = _day_total_h(cursor.date())
        # Available from cursor onward in this day's windows
        end_of_day = datetime.combine(
            cursor.date(), _windows_for_day(cursor.date())[-1][1]
        )
        day_avail = daylight_hours_until(end_of_day, cursor)
        if day_avail <= 0:
            next_date = cursor.date() + timedelta(days=1)
            cursor    = datetime.combine(next_date, _windows_for_day(next_date)[0][0])
            continue
        alloc = min(work_left, day_avail)
        plan_days.append((cursor.date(), alloc, d_h))
        work_left -= alloc
        next_date  = cursor.date() + timedelta(days=1)
        cursor     = datetime.combine(next_date, _windows_for_day(next_date)[0][0])

    for d, h, dh in plan_days[:7]:
        label     = "today    " if d == now.date() else d.strftime("%a %d %b")
        bar_w     = int((h / max(dh, 0.01)) * 20)
        bar       = GREEN + "|" * bar_w + DIM + "." * (20 - bar_w) + RESET
        intensity = "heavy" if h > dh * 0.7 else "moderate" if h > dh * 0.4 else "light"
        print(f"    {label}  {fmt_hours(h):>6}  {bar}  {DIM}{intensity}{RESET}")

    if len(plan_days) > 7:
        print(f"    {DIM}... +{len(plan_days) - 7} more day(s){RESET}")
    print()

    # What you can drop to recover headroom
    if droppable:
        recoverable = sum(t.remaining_estimate for t in droppable)
        print(f"  {BOLD}Skip these low-priority tasks to recover "
              f"{fmt_hours(recoverable)}:{RESET}")
        for t in droppable:
            print(f"    {DIM}-{RESET} {t.name}  {DIM}({fmt_hours(t.remaining_estimate)}){RESET}")
        print()
        new_total   = total_remaining - recoverable
        new_daily   = new_total / working_days
        print(f"  Without them: {fmt_hours(new_total)} remaining, "
              f"{fmt_hours(new_daily)}/day needed.")
        print()

    print(f"{BOLD}{'─'*62}{RESET}")
    print()


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="lsf",
        description="Least Slack First assignment scheduler"
    )
    parser.add_argument(
        "command", nargs="?", default="schedule",
        choices=["schedule", "panic", "import"],
        help=(
            "schedule (default) -- interactive schedule; "
            "panic -- survival triage when overloaded; "
            "import -- import tasks from a CSV file"
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

    # ── lsf import [file] ────────────────────────────────────────────
    if args.command == "import":
        csv_path = args.file or CSV_FILE
        raw      = load_tasks()
        raw, n   = import_csv(raw, csv_path)
        if n:
            save_tasks(raw)
            print(f"  {GREEN}✓ Imported {n} new task(s) from {csv_path}{RESET}")
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
            print(f"\n  {GREEN}✓ Auto-imported {csv_added} new task(s) from import.csv{RESET}")

    tasks = [dict_to_task(d) for d in raw]

    if not tasks:
        if not args.next:
            print("  No saved tasks found. Run without flags to add assignments.")
        return

    ordered = schedule(tasks, now)

    # --next: machine-readable single-line output for fastfetch / status bars
    if args.next:
        t           = ordered[0]
        suggest_min = max(15, round(min(t.remaining_estimate, 1.5) * 60 / 15) * 15)
        if t.overloaded:
            print(f"{t.name}\n0")   # 0 = signal that it's overloaded
        else:
            print(f"{t.name}\n{suggest_min}")
        return

    # panic mode
    if args.command == "panic":
        panic(ordered, now)
        return

    # Default: full interactive schedule
    print()
    print(f"{BOLD}{'─'*62}{RESET}")
    print(f"{BOLD}  lsf -- Least Slack First{RESET}  {DIM}(as of {now:%H:%M, %a %d %b}){RESET}")
    print(f"{BOLD}{'─'*62}{RESET}")
    print(f"  {DIM}{len(tasks)} task(s) loaded from ~/.lsf/tasks.json{RESET}")
    print()

    changed = False
    print("  What would you like to do first?")
    print(f"    {CYAN}1.{RESET} Update progress (mark done / log time spent)")
    print(f"    {CYAN}2.{RESET} Add new assignments")
    print(f"    {CYAN}3.{RESET} Both")
    print(f"    {CYAN}4.{RESET} Just show the schedule")
    print()
    choice = prompt("Choice", "4").strip()

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
        ordered = schedule(tasks, now)   # re-run after changes

    display(ordered, now)


if __name__ == "__main__":
    main()