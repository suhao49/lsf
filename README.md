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
makepkg -si   # from the repo root
```

Requires `python-build`, `python-installer`, `python-setuptools`, and `python-wheel`
from the official repos. The PKGBUILD is included in the repository root.

### Debian / Ubuntu

The `debian/` directory in the repository root contains the packaging files.
To build a `.deb` locally:

```sh
# Install build dependencies (one time)
sudo apt install debhelper dh-python python3-all python3-setuptools \
                 python3-build python3-installer

# Build the package from the repo root
dpkg-buildpackage -us -uc -b
```

This produces a `.deb` file in the parent directory. Install it with:

```sh
sudo dpkg -i ../lsf_1.0.0-1_all.deb
```

### Fedora / RHEL / openSUSE

The `lsf.spec` file in the repository root is an RPM spec. To build locally:

```sh
# Install build dependencies (one time)
# Fedora/RHEL:
sudo dnf install python3-devel python3-setuptools python3-build python3-installer rpm-build
# openSUSE:
sudo zypper install python3-devel python3-setuptools python3-build python3-installer rpm-build

# Build the RPM from the repo root
rpmbuild -ba lsf.spec --define "_sourcedir $(pwd)" --define "_rpmdir $(pwd)/dist"
```

This produces an RPM under `dist/`. Install it with:

```sh
sudo rpm -i dist/noarch/lsf-1.0.0-1.*.noarch.rpm
# or with dnf to handle dependencies automatically:
sudo dnf install dist/noarch/lsf-1.0.0-1.*.noarch.rpm
```

### Development (editable install)

```sh
./install.sh --dev   # Linux/macOS
pip install -e .     # Windows
```

Changes to the source take effect immediately without reinstalling.

## Usage

Running `lsf` with no arguments opens the full-screen terminal interface:

```
┌─ Today's plan ──────────────┐┌─ Tasks ─────────────────────────────┐
│ -- 19:30-21:30 (2h00m) --   ││  #  Task            Due   Left Slack│
│  19:30 -> 20:30  Essay      ││  1  Essay           today  3h   +2h │
│  20:30 -> 20:40  (break 10m)││  2  Problem set 4   tmrw   1h   +9h │
│  20:40 -> 21:30  Essay      ││                                     │
└─────────────────────────────┘└─────────────────────────────────────┘
┌─ Timer ─────────────────────┐┌─ Details ───────────────────────────┐
│ > Essay  00:23:41           ││ Essay  [a1b2c3d4]                   │
│ s to stop & log · p to pause││ Remaining 3h · slack +2h · on track │
└─────────────────────────────┘└─────────────────────────────────────┘
 2 task(s) · remaining 4h · pace 2h/day over 2.0 working days
 a Add  e Edit  d Done  s Start/stop  p Pause  x Export  q Quit
```

Keys: `a` add task · `e` edit · `d` mark done · `u` undo · `s` start/stop timer ·
`p` pause/resume · `x` export .ics · `shift+p` panic triage · `r` reload · `q` quit.

While a timer is running the TUI rings the terminal bell and shows a
notification when you have worked a full slice ("time for a break") and when
a pause has lasted longer than `break_min` ("break over").

The timer, schedule, and task data are shared with the CLI commands below, so
you can mix and match freely (e.g. start a timer in the TUI, stop it with
`lsf done` later).

```sh
lsf                      # full-screen TUI (default)
lsf schedule             # classic prompt-based schedule
lsf add "Essay" --est 2h --due "friday 18:00" -p 3 -d 3   # non-interactive add
lsf undo                 # restore the most recently completed task
lsf history              # completed tasks with estimated vs actual time
lsf start                # start a timer for a task
lsf start 2              # start a timer for task 2 directly
lsf start a1b2           # start a timer by task id prefix
lsf done                 # stop timer and log elapsed time
lsf pause                # pause the active timer
lsf resume               # resume a paused timer
lsf edit                 # edit a task (prompts for task number)
lsf edit 2               # edit task 2 directly
lsf edit a1b2            # edit by task id prefix
lsf panic                # survival triage (only available when overloaded)
lsf import tasks.csv     # import assignments from a CSV file
lsf import               # import from the data dir hot-folder
lsf export               # export schedule to ~/lsf_schedule.ics
lsf export schedule.ics  # export to a specific path
lsf --next               # machine-readable next task (for status bars)
lsf --json               # full schedule as JSON (tasks, slices, summary)
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
min_slice_min      = 10    # shortest slice worth scheduling; smaller window
                           # tails are left free instead of getting a fragment

# ── Breaks ────────────────────────────────────────────────────────────────────
break_min          = 10    # rest shown between slices in timetable; 0 to disable
                           # breaks are clipped to the remaining window time

# ── Urgency tuning ────────────────────────────────────────────────────────────
switch_penalty_min     = 10    # urgency penalty when switching tasks
switch_urgency_penalty = 0.85  # multiplier on urgency of a different task (0–1)
                                # lower = stronger preference for continuing current task
urgency_slack_floor_h  = 0.25  # minimum effective slack in urgency calculation (hours)
                                # prevents urgency spikes near zero slack
urgency_band_pct       = 0.20  # tasks within this fraction of top urgency compete as a group;
                                # within the band the shortest task is picked first
                                # 0.0 = pure LSF, 0.20 = 20% band

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

## Date formats

Anywhere a due date is asked for (TUI form, `lsf add --due`, `lsf edit`, CSV):

| Input | Meaning |
|---|---|
| `tonight` / `eod` | today 22:00 |
| `today 18:00` | today at 18:00 (time defaults to 23:59) |
| `tomorrow 08:00` | tomorrow at 08:00 |
| `friday`, `fri 18:00` | next occurrence of that weekday |
| `next mon 18:00` | one week after the next Monday |
| `+3d`, `+2w 09:30` | 3 days / 2 weeks from today |
| `25/03 10:00` | next 25th of March — rolls to next year if already past |
| `25/03/2027 10:00` | explicit day/month/year |
| `2027-03-25 10:00` | ISO format |

## Completion history & undo

Marking a task done (TUI `d`, or the "mark done" prompt) archives it to
`done.json` in the data directory instead of deleting it.

```sh
lsf history   # completed tasks, estimated vs actual time (when timers were used)
lsf undo      # restore the most recently completed task (TUI: press u)
```

## Session tracking

`lsf start` starts a timer for any task. `lsf done` stops it and logs actual working time.

```sh
lsf start        # shows your tasks and prompts you to pick one
lsf start 2      # start by task number
lsf start a1b2   # start by task id prefix
lsf pause        # pause the timer (paused wall time is not counted)
lsf resume       # resume after a pause
lsf done         # stop and log however long it actually took
```

If a session is already running, `lsf start` will warn you before overwriting it.
Paused time is automatically excluded from the logged duration — only actual working time counts.

## Editing tasks

`lsf edit` lets you change any field of an existing task without touching `tasks.json` directly.

```sh
lsf edit         # shows task list and prompts for a number
lsf edit 2       # edit task 2 directly
lsf edit a1b2    # edit by task id prefix
```

Press Enter at any prompt to keep the current value.

## Calendar export

`lsf export` writes the full slice schedule as a standard `.ics` file

```sh
lsf export                   # exports to ~/lsf_schedule.ics
lsf export ~/Desktop/lsf.ics # export to a custom path
```

Session UIDs are derived from task id + start time, so re-exporting updates existing
events instead of creating duplicates.

## Breaks

`break_min` adds a rest period after each work slice in the timetable. Breaks are
clipped to the remaining window time and never push past a window boundary:

```
19:30 → 20:30  Composition draft  * 60m
20:30 → 20:40  (break 10m)
20:40 → 21:30  Composition draft  * 50m
```

Set `break_min = 0` to disable. Panic mode always ignores breaks.

## Machine-readable output

`lsf --next` prints two lines: the task name, then the session length in minutes
(or `0` if overloaded). Wire it into fastfetch:

```jsonc
{ "type": "command", "key": "Next task", "text": "lsf --next" }
```

`lsf --json` outputs the full schedule as JSON

```json
{
  "as_of": "2025-03-15T14:22:00",
  "active_session": { "task_id": "a1b2c3d4", "started_at": "...", "active_h": 0.5 },
  "tasks": [ { "id": "...", "name": "...", "slack_h": 3.2, ... } ],
  "slices": [ { "task_id": "...", "start": "...", "end": "...", "duration_min": 60 } ],
  "summary": { "task_count": 3, "total_remaining_h": 7.4, "any_overloaded": false }
}
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

Python 3.9+ and [`textual`](https://textual.textualize.io/) (installed
automatically by pip; packaged as `python-textual` / `python3-textual` in
distro repos) for the full-screen interface.  
On Python < 3.11, also install [`tomli`](https://pypi.org/project/tomli/): `pip install tomli`

If `textual` is not available, `lsf` falls back to the classic prompt-based
interface — the scheduler itself has no external dependencies on Python 3.11+.

## How it works

Tasks are scheduled using a dynamic Least Slack First time-slice algorithm. Instead
of sorting tasks once, the scheduler repeatedly picks the most urgent task, works one
slice, then re-evaluates priorities. This prevents large low-urgency tasks from
blocking small urgent ones. Slice lengths are configurable per difficulty level.

Slices are window-aware: among tasks competing in the urgency band, ones whose
slice fits the remaining time in the current window are preferred, so short
windows (a 20-minute recess) get short work instead of splitting a deep session
across a gap. When nothing fits naturally the winning task's slice is clipped to
the window boundary, and window tails shorter than `min_slice_min` are left free.

Every schedule also runs a silent EDF feasibility check. If some cluster of
deadlines cannot all be met even under optimal ordering — before any aggregate
overload is visible — a `DEADLINE RISK` warning appears and `lsf panic` becomes
available for triage.

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
