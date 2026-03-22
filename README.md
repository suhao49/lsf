# lsf — Least Slack First

A terminal assignment scheduler that prioritises tasks by how little time you have left, not just by due date. Each task's urgency is `priority / slack`, where slack is the available working hours before the deadline minus the time-adjusted estimate.

## Install

### Linux / macOS

```sh
git clone https://github.com/suhao49/lsf
cd lsf
./install.sh          # user install (recommended, no sudo)
sudo ./install.sh     # system-wide
```

The installer uses `pip install --user` for user installs, which puts the binary
and module in the correct locations automatically. If the script warns that the
user bin directory is not on your PATH, add the printed line to your shell config
(`~/.bashrc`, `~/.zshrc`, etc.).

### Windows

Python 3.9+ must be installed and on your `PATH`. Then from a terminal (cmd or PowerShell):

```bat
git clone https://github.com/suhao49/lsf
cd lsf
pip install .
```

`lsf` will be available as a command after the install. If pip warns about the scripts directory not being on `PATH`, add it — typically `%APPDATA%\Python\PythonXY\Scripts`.

### Arch Linux

```sh
makepkg -si   # from the repo root, requires python-build python-installer python-setuptools
```

### Development (editable install)

```sh
./install.sh --dev   # Linux/macOS
pip install -e .     # Windows
```

Changes to the source take effect immediately without reinstalling.

## Usage

```sh
lsf                      # interactive schedule (default)
lsf start                # start a timer for a task
lsf start 2              # start a timer for task 2 directly
lsf start a1b2           # start a timer by task id prefix
lsf done                 # stop timer and log elapsed time
lsf panic                # survival triage (only available when overloaded)
lsf import tasks.csv     # import assignments from a CSV file
lsf import               # import from the data dir hot-folder
lsf --next               # machine-readable next task (for status bars)
lsf -v                   # print version and exit
```

## File locations

lsf uses platform-appropriate directories automatically — no configuration needed.

| Platform | Config file | Data directory |
|---|---|---|
| Linux | `~/.config/lsf/config.toml` | `~/.lsf/` |
| macOS | `~/Library/Application Support/lsf/config.toml` | `~/Library/Application Support/lsf/` |
| Windows | `%APPDATA%\lsf\config.toml` | `%LOCALAPPDATA%\lsf\` |

You can override the config location with the `$LSF_CONFIG` environment variable,
or drop a `config.toml` in the current working directory and it will be used instead.

## Configuration

On first run, lsf creates the config file with defaults. All scheduler behaviour
is controlled from this file — no source edits needed.

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
switch_penalty_min     = 10    # urgency penalty when switching tasks
switch_urgency_penalty = 0.85  # multiplier on urgency of a different task (0–1)
                                # lower = stronger preference for continuing current task
urgency_slack_floor_h  = 0.25  # minimum effective slack in urgency calculation (hours)
                                # prevents urgency spikes near zero slack

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
```

**Per-day overrides** — any day name (`monday` through `sunday`) can be given its
own `windows` list, which takes precedence over `[weekday]` or `[weekend]` for that
day only:

```toml
[saturday]
windows = [
  { start = "10:00", end = "14:00" },
  { start = "20:00", end = "23:00" },
]
```

**Config search order** (first match wins):
1. `$LSF_CONFIG` environment variable
2. `./config.toml` in the current working directory
3. Platform default (see File locations above)

## Data

Tasks persist in `tasks.json` in the platform data directory (see File locations).

**Importing from CSV:**

Drop a file into the hot-folder and it will be picked up automatically on the next run:
```sh
# Linux/macOS
cp assignments.csv ~/.lsf/import.csv

# Windows
copy assignments.csv %LOCALAPPDATA%\lsf\import.csv
```

Or import explicitly from any path:
```sh
lsf import ~/Downloads/assignments.csv
lsf import C:\Users\You\Downloads\assignments.csv
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
elapsed time to that task's `time_spent` field.

```sh
lsf start        # shows your tasks and prompts you to pick one
lsf start 2      # start by task number
lsf start a1b2   # start by task id prefix
lsf done         # stop and log however long it actually took
```

If a session is already running, `lsf start` will warn you before overwriting it.

## Breaks

`break_min` adds a rest period after each work slice in the timetable. Breaks are
clipped to the remaining window time and never push past a window boundary:

```
19:30 → 20:30  Composition draft  * 60m
20:30 → 20:40  (break 10m)
20:40 → 21:30  Composition draft  * 50m
```

Set `break_min = 0` to disable. Panic mode always ignores breaks.

## fastfetch integration

`lsf --next` prints two lines: the task name, then the session length in minutes
(or `0` if overloaded). Wire it into fastfetch:

```jsonc
{ "type": "command", "key": "Next task", "text": "lsf --next" }
```

## Panic mode

`lsf panic` is only available when the scheduler detects overload — running it
on a manageable schedule exits cleanly with a message.

When triggered, it uses Earliest Deadline First (EDF) to find the maximum subset of
tasks that can be completed on time and produces a session plan covering only those.
Tasks that cannot fit are listed as deferrals. Panic mode ignores breaks and urgency
tuning — it computes the theoretical maximum.

```
Write-offs           negative individual slack — missed regardless of order
Achievable on time   the EDF-optimal set to focus on
Defer or drop        tasks that cannot fit given the achievable set
Optimal session plan timetable for the achievable tasks only
```

## Requirements

Python 3.9+. No external dependencies on Python 3.11+.  
On Python < 3.11, install [`tomli`](https://pypi.org/project/tomli/): `pip install tomli`

## How it works

Tasks are scheduled using a dynamic Least Slack First time-slice algorithm. Instead
of sorting tasks once, the scheduler repeatedly picks the most urgent task, works one
slice, then re-evaluates priorities. This prevents large low-urgency tasks from
blocking small urgent ones. Slice lengths are configurable per difficulty level.

```
urgency = priority / max(slack, urgency_slack_floor_h)
slack   = available_working_hours_until_deadline − adjusted_estimate
adjusted_estimate = raw_estimate × risk_multiplier
```

A configurable switch urgency penalty (default 0.85) slightly favours continuing the
current task, creating natural session batching without burning scheduled time.

If lsf is run outside all configured windows, the scheduler snaps to the next
available window automatically. Individual day sections (`[monday]` etc.) override
the `[weekday]`/`[weekend]` defaults for that specific day.

When overloaded, panic mode uses priority-weighted EDF: tasks sorted by deadline
(tightest first), urgency as tiebreak within the same deadline, greedily scheduled
until no more fit.
