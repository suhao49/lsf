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
```

## Configuration

On first run, lsf creates `~/.config/lsf/config.toml` with a default 9–5 schedule.
Edit it to match your actual available hours:

```toml
[defaults]
risk_multiplier    = 1.4  # estimates multiplied by this (Hofstadter buffer)
switch_penalty_min = 10   # used for urgency calculation, not clock time
break_min          = 10   # break shown between slices in the timetable

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

See `config/config.toml.example` for a full annotated reference.

**Config search order** (first match wins):
1. `$LSF_CONFIG` environment variable
2. `./config.toml` in the current working directory
3. `~/.config/lsf/config.toml` (default)

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

`lsf start` records which task and session length you are beginning. When you run
`lsf done`, it computes the actual elapsed time and logs it against the task's
`time_spent` field. If the actual time is within 20% of the planned session length
it uses the actual time; otherwise it falls back to the planned length to avoid
accidentally logging large values if you forgot to run `done`.

```sh
lsf start        # prompts you to pick a session if more than one is scheduled today
lsf start 2      # starts session 2 directly without prompting
lsf done         # logs time and shows what's next
```

## Breaks

`break_min` in config adds a rest period after each work slice in the timetable.
Breaks are clipped to the remaining window time — they never push the cursor past a
window boundary:

```
19:30 → 20:30  Composition draft  * 60m
20:30 → 20:40  (break 10m)
20:40 → 21:30  Composition draft  * 50m
```

If less time remains in the window than the configured break, the break is shortened
to fit. If the slice ends exactly at a window boundary, no break is shown and the
next session picks up at the next window naturally.

Set `break_min = 0` to disable breaks entirely.

Panic mode always ignores breaks and schedules continuous work — it is computing
the theoretical maximum, not a sustainable pace.

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
are listed clearly as deferrals.

```
Write-offs           tasks with negative individual slack (not enough time regardless of order)
Achievable on time   the EDF-optimal set to focus on
Defer or drop        tasks that cannot fit given the achievable set
Optimal session plan timetable for the achievable tasks only (no breaks)
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

Slice lengths are difficulty-dependent:

| Difficulty | Slice |
|---|---|
| light (1) | 30 min |
| medium (2) | 60 min |
| deep (3) | 90 min |

Urgency at each scheduling step:

```
urgency = priority / max(slack, 0.25h)
slack   = available_working_hours_until_deadline − adjusted_estimate
adjusted_estimate = raw_estimate × risk_multiplier
```

A switch urgency penalty (×0.85) slightly favours continuing the current task over
switching, creating natural session batching without burning clock time on
context-switch overhead.

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
