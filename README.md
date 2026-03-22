# lsf — Least Slack First

A terminal assignment scheduler that prioritises tasks by how little time you have left, not just by due date. Each task's urgency is `priority / slack`, where slack is the available working hours before the deadline minus the time-adjusted estimate.

## Install

**Via pip (recommended):**
```sh
git clone https://github.com/suhao49/lsf
cd lsf
sudo ./install.sh            # system-wide, to /usr/local
PREFIX=~/.local ./install.sh # user-local, no sudo needed
```

Make sure the target `bin/` is on your `$PATH`. For `~/.local`:
```sh
export PATH="$HOME/.local/bin:$PATH"
```

**For development (editable install):**
```sh
./install.sh --dev
```
Changes to the source take effect immediately without reinstalling.

**On Arch Linux:**
```sh
makepkg -si   # from the repo root, requires python-build python-installer python-setuptools
```

## Usage

```sh
lsf                      # interactive schedule (default)
lsf start                # begin the current session (records start time)
lsf start 3              # begin session 3 from today's plan
lsf done                 # mark current session complete and log time spent
lsf panic                # survival triage (only available when overloaded)
lsf import tasks.csv     # import assignments from a CSV file
lsf import               # import from ~/.lsf/import.csv (hot-folder)
lsf --next               # machine-readable next task (for fastfetch/status bars)
lsf -v                   # print version and exit
```

## Configuration

On first run, lsf creates `~/.config/lsf/config.toml` with defaults. All scheduler
behaviour is controlled from this file — no source edits needed.

```toml
[defaults]
# ── Estimation ────────────────────────────────────────────────────────────────
risk_multiplier    = 1.4   # raw estimates are multiplied by this (Hofstadter buffer)

# ── Slices ────────────────────────────────────────────────────────────────────
slice_light_min    = 30    # session length for difficulty 1 tasks (reading, MCQ, admin)
slice_medium_min   = 60    # session length for difficulty 2 tasks (problem sets, writing)
slice_deep_min     = 90    # session length for difficulty 3 tasks (essays, coding)

# ── Breaks ────────────────────────────────────────────────────────────────────
break_min          = 10    # rest shown between slices in timetable; 0 to disable
                           # breaks are clipped to the remaining window time

# ── Urgency tuning ────────────────────────────────────────────────────────────
switch_penalty_min    = 10    # urgency penalty (minutes) applied when switching tasks
switch_urgency_penalty = 0.85 # multiplier on urgency of a different task (0–1)
                               # lower = stronger preference for continuing current task
urgency_slack_floor_h  = 0.25 # minimum effective slack used in urgency calculation (hours)
                               # prevents spikes when a task is almost exactly on time

# ── Burndown display ──────────────────────────────────────────────────────────
deep_cap_per_day   = 4.0   # assumed max deep work hours per day (burndown forecast)
medium_cap_per_day = 6.0   # assumed max medium work hours per day

[weekday]
windows = [
  { start = "10:15", end = "10:35" },
  { start = "12:50", end = "13:15" },
  { start = "19:30", end = "21:30" },
]

[weekend]
windows = [
  { start = "13:00", end = "15:00" },
  { start = "17:00", end = "17:30" },
  { start = "19:30", end = "22:30" },
]

# Per-day overrides (optional)
# [monday] through [sunday] take precedence over [weekday] / [weekend]
# for that specific day. Useful for days with unusual schedules.
[saturday]
windows = [
  { start = "10:00", end = "14:00" },
  { start = "20:00", end = "23:00" },
]
```

Any day name (`monday` through `sunday`) can be given its own `windows` list.
It overrides the `[weekday]` or `[weekend]` default only for that day — all
other days continue to use the group default.

**Config search order** (first match wins):
1. `$LSF_CONFIG` environment variable
2. `./config.toml` in the current working directory
3. `~/.config/lsf/config.toml` (default)

See `config/config.toml.example` for a full annotated reference.

## Data

Tasks are stored in `~/.lsf/tasks.json` and persist between runs.
Active session state (started via `lsf start`) is stored in `~/.lsf/session.json`
and cleared automatically when `lsf done` is run.

**Importing from CSV** — two ways:

Drop a file into the hot-folder and it will be picked up automatically on the next run:
```sh
cp assignments.csv ~/.lsf/import.csv
lsf
```

Or import explicitly from any path:
```sh
lsf import ~/Downloads/assignments.csv
```

CSV format (header row required, `difficulty` is optional):
```csv
name,due,estimate,priority,difficulty
Essay,25/03 23:59,3h,2,3
Problem set,24/03 08:00,1h30m,2,2
```

Priority: `1` low · `2` medium · `3` high · `4` critical  
Difficulty: `1` light · `2` medium · `3` deep

## Session tracking

`lsf start` starts a timer for any task. `lsf done` stops it and logs the actual
elapsed time directly to that task's `time_spent` field — no tolerance checks,
no planned duration involved.

```sh
lsf start        # shows your tasks and prompts you to pick one
lsf start 2      # starts a timer for task 2 directly
lsf start a1b2   # starts a timer by task id prefix
lsf done         # stops the timer and logs however long it actually took
```

If a session is already running when you call `lsf start`, it will warn you and
ask before overwriting it. The active session is stored in `~/.lsf/session.json`
and cleared when `lsf done` is run.

## Breaks

`break_min` adds a rest period after each work slice in the timetable. Breaks are
clipped to the remaining window time and never push the cursor past a window boundary:

```
19:30 → 20:30  Composition draft  * 60m
20:30 → 20:40  (break 10m)
20:40 → 21:30  Composition draft  * 50m
```

Set `break_min = 0` to disable breaks. Panic mode always ignores breaks.

## fastfetch integration

```sh
lsf --next
```
Prints two lines: the task name, then the suggested session length in minutes (or `0`
if overloaded). Wire it into fastfetch:

```jsonc
{ "type": "command", "key": "Next task", "text": "lsf --next" }
```

## Panic mode

`lsf panic` is only available when the scheduler detects that the total workload
exceeds available time. Running it on a manageable schedule prints a message and
exits cleanly.

When overload is detected, panic mode uses an Earliest Deadline First (EDF)
algorithm to find the maximum subset of tasks that can be completed on time, then
produces an optimal session plan covering only those tasks. Tasks that cannot fit
are listed clearly as deferrals. Panic mode ignores breaks and all urgency tuning
knobs — it computes the theoretical maximum.

```
Write-offs           tasks with negative individual slack (not enough time regardless of order)
Achievable on time   the EDF-optimal set to focus on
Defer or drop        tasks that cannot fit given the achievable set
Optimal session plan timetable for the achievable tasks only
```

## Requirements

Python 3.9+. No external dependencies on Python 3.11+.  
On Python < 3.11, install [`tomli`](https://pypi.org/project/tomli/): `pip install tomli`

## How it works

**Scheduling algorithm**

Tasks are scheduled using a dynamic Least Slack First time-slice algorithm. Instead
of sorting tasks once and simulating sequential execution, the scheduler repeatedly
picks the most urgent task, works one slice, then re-evaluates priorities. This
prevents large low-urgency tasks from blocking small urgent ones.

Slice lengths are configurable per difficulty level (defaults: 30/60/90 min).

Urgency at each scheduling step:

```
urgency = priority / max(slack, urgency_slack_floor_h)
slack   = available_working_hours_until_deadline − adjusted_estimate
adjusted_estimate = raw_estimate × risk_multiplier
```

A configurable switch urgency penalty (`switch_urgency_penalty`, default 0.85)
slightly favours continuing the current task over switching, creating natural session
batching without burning clock time on context-switch overhead.

**Time windows**

Available hours are computed from your configured time windows, correctly handling
multi-window days, deadlines that fall before the working day starts, and
weekday/weekend schedule differences. If lsf is run outside all configured windows,
the scheduler automatically advances to the next available window before producing
estimates, and the timetable shows "Next session at HH:MM" rather than "Start now".

**Panic mode algorithm**

When overloaded, panic mode uses a priority-weighted EDF algorithm: tasks are sorted
by deadline (tightest first), with urgency as a tiebreak within the same deadline.
The scheduler greedily adds each task to the schedule and defers any task whose
finish time would exceed its deadline. This is optimal for maximising the number of
on-time completions while respecting priority ordering among tasks that share a
deadline.
