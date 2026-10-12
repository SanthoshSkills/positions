#!/usr/bin/env python3
"""
Shared chart settings for the trading hub (Supertrend period and multiplier)

The values live in ONE small file, hub_settings.json, in the same folder as the scripts. You change them from:
    python trading_hub.py settings                      show the current values
    python trading_hub.py settings supertrend 10 2      set period 10, multiplier 2
    python trading_hub.py settings reset                back to 10 and 2
    the browser page (python trading_hub.py web): "6. Chart settings"
    or the menu (option 13) in python trading_hub.py
ha_chart.py reads the file every time it draws a chart, so the screener, stock lookup and portfolio charts
all use the same values with no other change. Charts that are already drawn keep their old values until the
report is made again.

Limits: period 1 to 100 (whole number), multiplier 0.1 to 10. Anything else is refused and nothing is saved.
Keep this file in the same folder as the other scripts.
"""

import json
import sys
from pathlib import Path

FILE = Path("hub_settings.json")
DEFAULTS = {"supertrend_period": 10, "supertrend_mult": 2.0}


def clean(key, value):
    """Return the checked value for a setting, or raise ValueError with a plain message."""
    if key == "supertrend_period":
        try:
            f = float(str(value).strip())
        except ValueError:
            raise ValueError("The Supertrend period must be a whole number, for example 10.") from None
        if f != int(f) or not 1 <= f <= 100:
            raise ValueError("The Supertrend period must be a whole number from 1 to 100.")
        return int(f)
    if key == "supertrend_mult":
        try:
            f = float(str(value).strip())
        except ValueError:
            raise ValueError("The Supertrend multiplier must be a number, for example 2 or 3.") from None
        if not 0.1 <= f <= 10:
            raise ValueError("The Supertrend multiplier must be between 0.1 and 10.")
        return round(f, 4)
    raise ValueError(f"Unknown setting: {key}")


def load():
    """Current settings: the saved values, with the defaults for anything missing or unusable."""
    out = dict(DEFAULTS)
    if FILE.exists():
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            saved = {}
        for k in DEFAULTS:
            if k in saved:
                try:
                    out[k] = clean(k, saved[k])
                except ValueError:
                    pass
    return out


def update(**changes):
    """Check and save the given settings; returns all settings. Raises ValueError and saves nothing if one is bad."""
    new = load()
    for k, v in changes.items():
        new[k] = clean(k, v)
    try:
        FILE.write_text(json.dumps(new, indent=1), encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Could not save {FILE}: {e}") from None
    return new


def reset():
    return update(**DEFAULTS)


def supertrend():
    s = load()
    return s["supertrend_period"], s["supertrend_mult"]


def describe():
    p, m = supertrend()
    return (f"Supertrend: period {p}, multiplier {m:g}   (file: {FILE.resolve()})\n"
            "Used by the Heikin Ashi charts in the screener, stock lookup and portfolio reports.")


def run_cmd(args):
    if not args:
        print(describe())
        print("\nChange it:  settings supertrend PERIOD MULTIPLIER     (for example: settings supertrend 10 2)\n"
              "Undo:       settings reset")
        return
    cmd, rest = args[0].lower(), args[1:]
    try:
        if cmd == "supertrend" and len(rest) == 2:
            update(supertrend_period=rest[0], supertrend_mult=rest[1])
        elif cmd == "reset":
            reset()
        elif cmd == "show":
            pass
        else:
            print("Usage:  settings supertrend PERIOD MULTIPLIER   |   settings reset   |   settings show")
            return
    except ValueError as e:
        print(f"Not saved: {e}")
        return
    print(describe())
    print("Charts already drawn keep the old values: run the screen, lookup or portfolio report again.")


if __name__ == "__main__":
    run_cmd(sys.argv[1:])
