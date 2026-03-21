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
lsf import tasks.csv     # import assignments from a CSV file
lsf import               # import from ~/.lsf/import.csv (hot-folder)
lsf panic                # survival triage when overloaded
lsf --next               # machine-readable next task (for fastfetch/status bars)
```

## Configuration

On first run, lsf creates `~/.config/lsf/config.toml` with a default 9–5 schedule.
Edit it to match your actual available hours:

```toml
[defaults]
risk_multiplier    = 1.4  # estimates multiplied by this (Hofstadter buffer)
switch_penalty_min = 10   # minutes lost switching between tasks

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

## fastfetch integration

```sh
lsf --next
```
Prints two lines: the task name, then the suggested session length in minutes (or `0` if overloaded). Wire it into fastfetch:

```jsonc
{ "type": "command", "key": "Next task", "text": "lsf --next" }
```

## Requirements

Python 3.9+. No external dependencies on Python 3.11+.  
On Python < 3.11, install [`tomli`](https://pypi.org/project/tomli/): `pip install tomli`

## How it works

Tasks are sorted by urgency descending, where:

```
urgency = priority / max(slack, 0.25h)
slack   = available_working_hours_until_deadline − adjusted_estimate
adjusted_estimate = raw_estimate × risk_multiplier
```

Overloaded tasks (negative slack) always float to the top regardless of priority. The scheduler then simulates sequential execution with a 10-minute context-switch penalty between tasks to produce realistic finish-time estimates.

Available hours are computed from your configured time windows, correctly handling multi-window days, deadlines that fall before the working day starts, and weekday/weekend schedule differences.
