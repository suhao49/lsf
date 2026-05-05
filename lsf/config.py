"""
lsf.config -- configuration loader

Reads the platform config file, creates it with defaults on first run.
  Linux  : ~/.config/lsf/config.toml  (or $XDG_CONFIG_HOME/lsf)
  macOS  : ~/Library/Application Support/lsf/config.toml
  Windows: %APPDATA%\\lsf\\config.toml
Uses stdlib tomllib (3.11+) with a hand-rolled fallback for older Python.
"""

import os
import sys
from datetime import datetime, date, time as dtime

# -- Config loader ------------------------------------------------------------

# Platform-appropriate config directory:
#   macOS   : ~/Library/Application Support/lsf
#   Windows : %APPDATA%/lsf  (e.g. C:/Users/You/AppData/Roaming/lsf)
#   Linux   : ~/.config/lsf  (XDG_CONFIG_HOME)
def _default_config_dir() -> str:
    if os.name == "nt":                          # Windows
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "lsf")
    if sys.platform == "darwin":                 # macOS
        return os.path.expanduser("~/Library/Application Support/lsf")
    # Linux / everything else: respect XDG_CONFIG_HOME
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "lsf")

CONFIG_DIR  = _default_config_dir()
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.toml")

DEFAULT_CONFIG_TOML = """\
# lsf configuration
# Linux:   ~/.config/lsf/config.toml
# macOS:   ~/Library/Application Support/lsf/config.toml
# Windows: %APPDATA%/lsf/config.toml
#
# Define one or more time windows per day type.
# Use 24-hour "HH:MM" strings.
# Default below is a standard 9-5 workday (single window).
# Uncomment and edit the student example for a school schedule.

[defaults]
risk_multiplier    = 1.4   # estimates are multiplied by this (Hofstadter buffer)
switch_penalty_min = 10    # minutes of urgency penalty when switching tasks
break_min          = 10    # break shown between slices (clipped to window; 0 to disable)

# Slice lengths per difficulty level (minutes)
slice_light_min    = 30    # difficulty 1 -- reading, MCQ, admin
slice_medium_min   = 60    # difficulty 2 -- problem sets, short writing
slice_deep_min     = 90    # difficulty 3 -- essays, coding, creative work

# How strongly to favour continuing the current task vs switching
# 1.0 = no preference, 0.0 = never switch, 0.85 is a good default
switch_urgency_penalty = 0.85

# Minimum effective slack used in urgency calculation (hours)
# Prevents urgency spikes when a task is almost exactly on time
urgency_slack_floor_h  = 0.25

# Urgency banding: tasks within this fraction of the top urgency form a group.
# Within that group the task with least remaining work is picked first, so
# small urgent tasks push through rather than every task clumping in order.
# 0.0 = pure LSF (strict ordering), 0.20 = 20% band is a good default.
urgency_band_pct       = 0.20

# Burndown forecast caps (hours of each type per day)
deep_cap_per_day   = 4.0
medium_cap_per_day = 6.0

[weekday]
windows = [
  { start = "09:00", end = "17:00" },
]

[weekend]
windows = [
  { start = "09:00", end = "17:00" },
]

# -- Per-day overrides (optional) --
# Individual day sections override [weekday] or [weekend] for that specific day.
# Day names: [monday] [tuesday] [wednesday] [thursday] [friday] [saturday] [sunday]
#
# [saturday]
# windows = [
#   { start = "10:00", end = "14:00" },
#   { start = "20:00", end = "23:00" },
# ]
#
# -- Student / school schedule example (uncomment to use) --
#
# [weekday]
# windows = [
#   { start = "10:15", end = "10:35" },
#   { start = "12:50", end = "13:15" },
#   { start = "19:30", end = "21:30" },
# ]
#
# [weekend]
# windows = [
#   { start = "13:00", end = "15:00" },
#   { start = "17:00", end = "17:30" },
#   { start = "19:30", end = "22:30" },
# ]
"""

def _parse_time(s: str) -> dtime:
    """Parse 'HH:MM' into a time object."""
    h, m = map(int, s.strip().split(":"))
    return dtime(h, m)


def _load_toml(path: str) -> dict:
    """
    Load a TOML file using the best available parser:
      1. stdlib tomllib  (Python 3.11+)
      2. tomli backport  (pip install tomli, listed as dep for <3.11)
    """
    if sys.version_info >= (3, 11):
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    try:
        import tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        raise ImportError(
            "Python < 3.11 requires the 'tomli' package to parse config.toml.\n"
            "Install it with:  pip install tomli"
        )


def _find_config() -> str:
    """
    Locate the config file using a priority search:
      1. $LSF_CONFIG environment variable
      2. ./config.toml in the current working directory
      3. platform default config dir (always used as write target)
         Linux: ~/.config/lsf/  macOS: ~/Library/Application Support/lsf/
         Windows: %APPDATA%/lsf/
    """
    env = os.environ.get("LSF_CONFIG")
    if env:
        return env
    cwd_cfg = os.path.join(os.getcwd(), "config.toml")
    if os.path.isfile(cwd_cfg):
        return cwd_cfg
    return CONFIG_FILE


def load_config() -> dict:
    """
    Load the lsf config file, creating the platform config file with defaults
    if no config is found in the search path ($LSF_CONFIG → ./config.toml → platform default).

    Returns a normalised dict with keys:
        windows_weekday        : list of (dtime, dtime) tuples  (default for Mon-Fri)
        windows_weekend        : list of (dtime, dtime) tuples  (default for Sat-Sun)
        windows_by_day         : dict[int, list[tuple]] keyed 0=Mon..6=Sun
                                 individual day sections override weekday/weekend
        risk_multiplier        : float
        switch_penalty_h       : float
        break_h                : float
        slice_h                : dict[int, float]  -- {1: light_h, 2: med_h, 3: deep_h}
        switch_urgency_penalty : float
        urgency_slack_floor_h  : float
        deep_cap_per_day       : float
        medium_cap_per_day     : float
        total_day_h_weekday    : float
        total_day_h_weekend    : float
    """
    cfg_path = _find_config()
    if not os.path.isfile(cfg_path):
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_TOML)
        print(f"  Config created at {cfg_path}")
        print(f"  Edit it to set your working hours, then re-run lsf.")
        print()

    try:
        raw = _load_toml(cfg_path)
    except Exception as e:
        print(f"  Warning: could not parse {cfg_path} ({e}). Using 9-5 defaults.")
        raw = {}

    _FALLBACK_WINDOWS = [(_parse_time("09:00"), _parse_time("17:00"))]

    def parse_windows(section_key: str) -> list[tuple[dtime, dtime]]:
        """Parse a [section] windows list, returning 9-5 if absent."""
        section = raw.get(section_key, {})
        wins    = section.get("windows", [])
        if not wins:
            return _FALLBACK_WINDOWS
        result = []
        for w in wins:
            try:
                result.append((_parse_time(w["start"]), _parse_time(w["end"])))
            except (KeyError, ValueError):
                pass
        return result or _FALLBACK_WINDOWS

    defaults         = raw.get("defaults", {})
    risk             = float(defaults.get("risk_multiplier", 1.4))
    switch_min       = float(defaults.get("switch_penalty_min", 10))
    break_min        = float(defaults.get("break_min", 10))
    slice_light      = float(defaults.get("slice_light_min", 30))
    slice_medium     = float(defaults.get("slice_medium_min", 60))
    slice_deep       = float(defaults.get("slice_deep_min", 90))
    switch_urg_pen   = float(defaults.get("switch_urgency_penalty", 0.85))
    slack_floor      = float(defaults.get("urgency_slack_floor_h", 0.25))
    band_pct         = float(defaults.get("urgency_band_pct", 0.20))
    deep_cap         = float(defaults.get("deep_cap_per_day", 4.0))
    medium_cap       = float(defaults.get("medium_cap_per_day", 6.0))
    wins_weekday     = parse_windows("weekday")
    wins_weekend     = parse_windows("weekend")

    # Per-day overrides: [monday]..[sunday] take precedence over weekday/weekend
    _DAY_NAMES = ["monday", "tuesday", "wednesday",
                  "thursday", "friday", "saturday", "sunday"]
    wins_by_day: dict[int, list[tuple[dtime, dtime]]] = {}
    for day_num, day_name in enumerate(_DAY_NAMES):
        if day_name in raw:
            wins_by_day[day_num] = parse_windows(day_name)
        elif day_num < 5:
            wins_by_day[day_num] = wins_weekday
        else:
            wins_by_day[day_num] = wins_weekend

    def total_h(windows):
        return sum(
            (datetime.combine(date.today(), e) - datetime.combine(date.today(), s)
             ).total_seconds() / 3600
            for s, e in windows if e > s
        )

    def total_h_for_day(day_num: int) -> float:
        return sum(
            (datetime.combine(date.today(), e) - datetime.combine(date.today(), s)
             ).total_seconds() / 3600
            for s, e in wins_by_day[day_num] if e > s
        )

    return {
        "windows_weekday":        wins_weekday,
        "windows_weekend":        wins_weekend,
        "windows_by_day":         wins_by_day,
        "total_h_by_day":         {d: total_h_for_day(d) for d in range(7)},
        "risk_multiplier":        risk,
        "switch_penalty_h":       switch_min / 60,
        "break_h":                break_min / 60,
        "slice_h": {
            1: slice_light  / 60,
            2: slice_medium / 60,
            3: slice_deep   / 60,
        },
        "switch_urgency_penalty": switch_urg_pen,
        "urgency_slack_floor_h":  slack_floor,
        "urgency_band_pct":       band_pct,
        "deep_cap_per_day":       deep_cap,
        "medium_cap_per_day":     medium_cap,
        "total_day_h_weekday":    total_h_for_day(0),  # Monday as representative weekday
        "total_day_h_weekend":    total_h_for_day(5),  # Saturday as representative weekend
    }

# Convenience alias
load = load_config
