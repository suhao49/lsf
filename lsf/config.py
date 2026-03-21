"""
lsf.config -- configuration loader

Reads ~/.config/lsf/config.toml, creates it with defaults on first run.
Uses stdlib tomllib (3.11+) with a hand-rolled fallback for older Python.
"""

import os
import sys
from datetime import datetime, date, time as dtime

# -- Config loader ------------------------------------------------------------

CONFIG_DIR  = os.path.expanduser("~/.config/lsf")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.toml")

DEFAULT_CONFIG_TOML = """\
# lsf configuration
# Paths: ~/.config/lsf/config.toml
#
# Define one or more time windows per day type.
# Use 24-hour "HH:MM" strings.
# Default below is a standard 9-5 workday (single window).
# Uncomment and edit the student example for a school schedule.

[defaults]
risk_multiplier    = 1.4   # estimates are multiplied by this (Hofstadter buffer)
switch_penalty_min = 10    # minutes lost when switching between tasks
break_min          = 10    # break between slices (display only, clipped to window)

[weekday]
windows = [
  { start = "09:00", end = "17:00" },
]

[weekend]
windows = [
  { start = "09:00", end = "17:00" },
]

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
      3. ~/.config/lsf/config.toml  (XDG default, always used as write target)
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
    Load the lsf config file, creating ~/.config/lsf/config.toml with defaults
    if no config is found in the search path ($LSF_CONFIG → ./config.toml → XDG).

    Returns a normalised dict with keys:
        windows_weekday     : list of (dtime, dtime) tuples
        windows_weekend     : list of (dtime, dtime) tuples
        risk_multiplier     : float
        switch_penalty_h    : float
        total_day_h_weekday : float
        total_day_h_weekend : float
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

    def parse_windows(section_key: str) -> list[tuple[dtime, dtime]]:
        section = raw.get(section_key, {})
        wins    = section.get("windows", [])
        if not wins:
            # fallback: bare 9-5
            return [(_parse_time("09:00"), _parse_time("17:00"))]
        result = []
        for w in wins:
            try:
                result.append((_parse_time(w["start"]), _parse_time(w["end"])))
            except (KeyError, ValueError):
                pass
        return result or [(_parse_time("09:00"), _parse_time("17:00"))]

    defaults         = raw.get("defaults", {})
    risk             = float(defaults.get("risk_multiplier", 1.4))
    switch_min       = float(defaults.get("switch_penalty_min", 10))
    break_min        = float(defaults.get("break_min", 10))
    wins_weekday     = parse_windows("weekday")
    wins_weekend     = parse_windows("weekend")

    def total_h(windows):
        return sum(
            (datetime.combine(date.today(), e) - datetime.combine(date.today(), s)
             ).total_seconds() / 3600
            for s, e in windows if e > s
        )

    return {
        "windows_weekday":      wins_weekday,
        "windows_weekend":      wins_weekend,
        "risk_multiplier":      risk,
        "switch_penalty_h":     switch_min / 60,
        "break_h":              break_min / 60,
        "total_day_h_weekday":  total_h(wins_weekday),
        "total_day_h_weekend":  total_h(wins_weekend),
    }

# Convenience alias
load = load_config
