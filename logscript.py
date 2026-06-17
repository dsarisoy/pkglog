#!/usr/bin/env python3
"""
pkglog.py - Arch Linux package history tracker

Parses /var/log/pacman.log and writes a multi-sheet xlsx to ~/Scripts/pkglog.xlsx

Sheets:
  Explicitly Downloaded - packages you manually installed via pacman -S (official repos)
  AUR                  - packages you manually installed from AUR
  Official Dependencies - auto-installed dependencies from official repos
  History              - every install/upgrade/remove event, one row per event

Usage:
  python3 pkglog.py           # generate / refresh the spreadsheet
  python3 pkglog.py --setup   # first-time setup: install hook + generate spreadsheet
"""

import re
import subprocess
import sys
import os
import shutil
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from getpass import getuser

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.comments import Comment
except ImportError:
    print("Error: openpyxl is not installed.")
    print("Install it with:  sudo pacman -S python-openpyxl")
    sys.exit(1)

SCRIPT_PATH  = Path(__file__).resolve()
SCRIPT_DIR   = SCRIPT_PATH.parent
LOG_PATH     = Path("/var/log/pacman.log")
OUT_PATH     = SCRIPT_DIR / "pkglog.xlsx"
HOOK_DIR     = Path("/etc/pacman.d/hooks")
HOOK_PATH    = HOOK_DIR / "pkglog.hook"

HOOK_CONTENT = f"""\
[Trigger]
Operation = Install
Operation = Upgrade
Operation = Remove
Type = Package
Target = *

[Action]
Description = Updating pkglog package history...
When = PostTransaction
Exec = /usr/bin/python3 {SCRIPT_PATH}
"""

LOG_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})[^\]]*\] \[ALPM\] "
    r"(installed|upgraded|removed|reinstalled) ([^\s]+) \((.+)\)$"
)

EXPLICIT_FILL  = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
AUR_FILL       = PatternFill("solid", start_color="375623", end_color="375623")
OFFICIAL_FILL  = PatternFill("solid", start_color="4A235A", end_color="4A235A")
HISTORY_FILL   = PatternFill("solid", start_color="7B3F00", end_color="7B3F00")
HEADER_FONT    = Font(name="Arial", bold=True, color="FFFFFF", size=10)
BODY_FONT      = Font(name="Arial", size=10)

thin   = Side(style="thin", color="CCCCCC")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

ACTION_COLORS = {
    "installed":   "C6EFCE",
    "upgraded":    "DDEBF7",
    "reinstalled": "FFF2CC",
    "removed":     "FFCCCC",
}


# - setup -

def check_dependencies():
    ok = True
    if not shutil.which("pacman"):
        print("Error: pacman not found. pkglog requires Arch Linux.")
        ok = False
    if not LOG_PATH.exists():
        print(f"Error: {LOG_PATH} not found.")
        ok = False
    return ok


def install_hook():
    if HOOK_PATH.exists():
        print(f"Hook already installed at {HOOK_PATH}")
        return True

    print(f"Installing pacman hook to {HOOK_PATH} (requires sudo)...")
    try:
        subprocess.run(["sudo", "mkdir", "-p", str(HOOK_DIR)], check=True)
        tmp = Path("/tmp/pkglog.hook")
        tmp.write_text(HOOK_CONTENT)
        subprocess.run(["sudo", "cp", str(tmp), str(HOOK_PATH)], check=True)
        subprocess.run(["sudo", "chmod", "644", str(HOOK_PATH)], check=True)
        tmp.unlink()
        print(f"Hook installed - {HOOK_PATH}")
        return True
    except subprocess.CalledProcessError:
        print("Failed to install hook. You can install it manually:")
        print(f"  sudo mkdir -p {HOOK_DIR}")
        print(f"  sudo tee {HOOK_PATH} << 'EOF'")
        print(HOOK_CONTENT + "EOF")
        return False


def setup():
    print("=== pkglog setup ===\n")

    if not check_dependencies():
        sys.exit(1)

    hook_ok = install_hook()

    print("\nGenerating initial spreadsheet...")
    run()

    print("\n=== Setup complete ===")
    print(f"Spreadsheet: {OUT_PATH}")
    if hook_ok:
        print(f"Hook:        {HOOK_PATH}")
        print("\nThe spreadsheet will auto-update after every pacman transaction.")
    else:
        print("\nHook installation failed - auto-update won't work until the hook is installed.")


# - parsing -

def get_official_packages():
    try:
        result = subprocess.run(
            ["pacman", "-Slq"], capture_output=True, text=True, timeout=15
        )
        return set(result.stdout.splitlines())
    except Exception:
        return set()


def get_explicit_packages():
    try:
        result = subprocess.run(
            ["pacman", "-Qqe"], capture_output=True, text=True, timeout=15
        )
        return set(result.stdout.splitlines())
    except Exception:
        return set()


def parse_log():
    events = []
    with open(LOG_PATH, "r", errors="replace") as f:
        for line in f:
            m = LOG_RE.match(line.rstrip())
            if not m:
                continue
            date, time_, action, pkg, ver = m.groups()
            events.append({
                "date":     date,
                "time":     time_,
                "datetime": datetime.fromisoformat(f"{date}T{time_}"),
                "action":   action,
                "package":  pkg,
                "version":  ver,
            })
    return sorted(events, key=lambda e: e["datetime"])


def build_package_summary(events, official_pkgs, explicit_pkgs):
    explicit, aur, official_deps = [], [], []

    for e in events:
        pkg = e["package"]
        if pkg in official_pkgs:
            if pkg in explicit_pkgs:
                explicit.append(e)
            else:
                official_deps.append(e)
        else:
            aur.append(e)

    return list(reversed(explicit)), list(reversed(aur)), list(reversed(official_deps))


# - xlsx writing -

def style_header(ws, headers, fill):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font      = HEADER_FONT
        cell.fill      = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = BORDER
    ws.row_dimensions[1].height = 28


def write_sheet(ws, events, header_fill):
    headers = ["Date", "Time", "Action", "Package", "Version / Change"]
    style_header(ws, headers, header_fill)

    for i, w in enumerate([14, 10, 14, 30, 40], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A2"

    for r, e in enumerate(events, 2):
        c = ACTION_COLORS.get(e["action"], "FFFFFF")
        rf = PatternFill("solid", start_color=c, end_color=c)
        for col, val in enumerate(
            [e["date"], e["time"], e["action"], e["package"], e["version"]], 1
        ):
            cell           = ws.cell(row=r, column=col, value=val)
            cell.font      = BODY_FONT
            cell.fill      = rf
            cell.border    = BORDER
            cell.alignment = Alignment(vertical="center")


def write_history_sheet(ws, events):
    headers = ["Date", "Time", "Action", "Package", "Version / Change"]
    style_header(ws, headers, HISTORY_FILL)

    for i, w in enumerate([14, 10, 14, 30, 40], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A2"

    for r, e in enumerate(events, 2):
        c = ACTION_COLORS.get(e["action"], "FFFFFF")
        rf = PatternFill("solid", start_color=c, end_color=c)
        for col, val in enumerate(
            [e["date"], e["time"], e["action"], e["package"], e["version"]], 1
        ):
            cell           = ws.cell(row=r, column=col, value=val)
            cell.font      = BODY_FONT
            cell.fill      = rf
            cell.border    = BORDER
            cell.alignment = Alignment(vertical="center")


# - main -

def run():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("Reading official package list from pacman...")
    official_pkgs = get_official_packages()
    print(f"  {len(official_pkgs)} packages in official repos")

    print("Reading explicitly installed packages...")
    explicit_pkgs = get_explicit_packages()
    print(f"  {len(explicit_pkgs)} explicitly installed packages")

    print(f"Parsing {LOG_PATH}...")
    events = parse_log()
    print(f"  {len(events)} events found")

    explicit_rows, aur_rows, official_dep_rows = build_package_summary(
        events, official_pkgs, explicit_pkgs
    )
    print(f"  {len(explicit_rows)} explicit events, {len(aur_rows)} AUR events, {len(official_dep_rows)} official dependency events")

    wb = Workbook()

    ws_explicit = wb.active
    ws_explicit.title = "Explicitly Downloaded"
    write_sheet(ws_explicit, explicit_rows, EXPLICIT_FILL)
    comment = Comment(
        "Removed packages will not appear here — pacman -Qqe only tracks currently installed packages. Check the History sheet for removed package events.",
        "pkglog"
    )
    ws_explicit["A1"].comment = comment

    ws_aur = wb.create_sheet("AUR")
    write_sheet(ws_aur, aur_rows, AUR_FILL)

    ws_official = wb.create_sheet("Official Dependencies")
    write_sheet(ws_official, official_dep_rows, OFFICIAL_FILL)

    ws_history = wb.create_sheet("History")
    write_history_sheet(ws_history, list(reversed(events)))

    wb.save(OUT_PATH)
    print(f"Saved - {OUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="pkglog - Arch Linux package history tracker"
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="First-time setup: install pacman hook and generate spreadsheet"
    )
    args = parser.parse_args()

    if args.setup:
        setup()
    else:
        if not check_dependencies():
            sys.exit(1)
        run()
