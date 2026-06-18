#!/usr/bin/env python3
"""
pkglog - Arch Linux package history tracker

Parses /var/log/pacman.log and writes a multi-sheet ods to /var/log/pkglog/pkglog.ods

Sheets:
  Official Repository   - one row per explicit package + indented deps, sorted by last updated
  AUR                  - one row per AUR package + indented deps, sorted by last updated
  System Packages      - auto-installed dependencies from official repos
  History Official Repo - flat chronological log for explicit packages
  History AUR          - flat chronological log for AUR packages
  History System       - flat chronological log for system packages
  History              - full log of every pacman event

Usage:
  pkglog            # open the spreadsheet in LibreOffice
  pkglog --view     # open the spreadsheet in LibreOffice (alias)
  pkglog --update   # regenerate the spreadsheet
  pkglog --setup    # first-time setup: install script + hook + generate spreadsheet
"""

import re
import subprocess
import sys
import shutil
import argparse
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict

try:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableRow, TableCell, TableColumn
    from odf.style import (Style, TextProperties, TableCellProperties,
                           TableColumnProperties, TableRowProperties)
    from odf.text import P
except ImportError:
    print("Error: odfpy is not installed.")
    print("Install it with:  sudo pacman -S python-odfpy")
    sys.exit(1)

PACMAN_LOG  = Path("/var/log/pacman.log")
OUT_DIR     = Path("/var/log/pkglog")
OUT_PATH    = OUT_DIR / "pkglog.ods"
README_DST  = OUT_DIR / "README.md"
PKG_CACHE   = OUT_DIR / "pkg_cache.json"
HOOK_DIR    = Path("/etc/pacman.d/hooks")
HOOK_PATH   = HOOK_DIR / "pkglog.hook"
INSTALL_BIN = Path("/usr/local/bin/pkglog")
SCRIPT_PATH = Path(__file__).resolve()

HOOK_CONTENT = """\
[Trigger]
Operation = Install
Operation = Upgrade
Operation = Remove
Type = Package
Target = *

[Action]
Description = Updating pkglog package history...
When = PostTransaction
Exec = /usr/local/bin/pkglog --update
"""

LOG_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})[^\]]*\] \[ALPM\] "
    r"(installed|upgraded|removed|reinstalled) ([^\s]+) \((.+)\)$"
)

COLORS = {
    "installed":   "C6EFCE",
    "upgraded":    "DDEBF7",
    "reinstalled": "FFF2CC",
    "removed":     "FFCCCC",
}

HEADER_COLORS = {
    "Official Repository":   "1F4E79",
    "AUR":                   "375623",
    "System Packages":       "4A235A",
    "History Official Repo": "2E75B6",
    "History AUR":           "548235",
    "History System":        "7030A0",
    "History":               "7B3F00",
}

SUMMARY_COLS   = ["Last Updated", "Package", "First Installed", "Total Updates", "Current Version"]
SUMMARY_WIDTHS = [4.0, 8.0, 4.0, 3.5, 5.0]
HISTORY_COLS   = ["Date", "Time", "Action", "Package", "Version / Change"]
HISTORY_WIDTHS = [3.5, 2.5, 3.5, 7.0, 10.0]

_PKG_INFO_CACHE = {}


# ── setup ─────────────────────────────────────────────────────────────────────

def check_dependencies():
    ok = True
    if not shutil.which("pacman"):
        print("Error: pacman not found. pkglog requires Arch Linux.")
        ok = False
    if not PACMAN_LOG.exists():
        print(f"Error: {PACMAN_LOG} not found.")
        ok = False
    return ok


def install_script():
    print(f"Installing pkglog to {INSTALL_BIN} (requires sudo)...")
    try:
        subprocess.run(["sudo", "cp", str(SCRIPT_PATH), str(INSTALL_BIN)], check=True)
        subprocess.run(["sudo", "chmod", "755", str(INSTALL_BIN)], check=True)
        print(f"Script installed - {INSTALL_BIN}")
        return True
    except subprocess.CalledProcessError:
        print(f"Failed to install script to {INSTALL_BIN}.")
        return False


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


def install_readme():
    readme_src = SCRIPT_PATH.parent / "README.md"
    if not readme_src.exists():
        return
    try:
        subprocess.run(["sudo", "cp", str(readme_src), str(README_DST)], check=True)
        print(f"README installed - {README_DST}")
    except subprocess.CalledProcessError:
        pass


def ensure_out_dir():
    if not OUT_DIR.exists():
        subprocess.run(["sudo", "mkdir", "-p", str(OUT_DIR)], check=True)
        subprocess.run(["sudo", "chmod", "777", str(OUT_DIR)], check=True)


def setup():
    print("=== pkglog setup ===\n")
    if not check_dependencies():
        sys.exit(1)
    ensure_out_dir()
    install_script()
    install_hook()
    install_readme()
    print("\nGenerating initial spreadsheet...")
    run()
    print("\n=== Setup complete ===")
    print(f"Spreadsheet: {OUT_PATH}")
    print(f"Run 'pkglog' from anywhere to open it.")


def view():
    if not OUT_PATH.exists():
        print(f"No spreadsheet found at {OUT_PATH}")
        print("Run 'pkglog --update' to generate it.")
        sys.exit(1)
    subprocess.Popen(["libreoffice", "--calc", str(OUT_PATH)])


# ── data gathering ─────────────────────────────────────────────────────────────

def get_official_packages():
    try:
        r = subprocess.run(["pacman", "-Slq"], capture_output=True, text=True, timeout=15)
        return set(r.stdout.splitlines())
    except Exception:
        return set()


def get_explicit_packages():
    try:
        r = subprocess.run(["pacman", "-Qqe"], capture_output=True, text=True, timeout=15)
        return set(r.stdout.splitlines())
    except Exception:
        return set()


def batch_pkg_info(pkg_list):
    if not pkg_list:
        return {}
    try:
        r = subprocess.run(
            ["pacman", "-Qi"] + list(pkg_list),
            capture_output=True, text=True, timeout=60
        )
    except Exception:
        return {}

    info = {}
    current = None
    for line in r.stdout.splitlines():
        if line.startswith("Name"):
            current = line.split(":", 1)[1].strip()
            info[current] = {"version": "", "deps": []}
        elif current and line.startswith("Version"):
            info[current]["version"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("Depends On"):
            val = line.split(":", 1)[1].strip()
            if val != "None":
                deps = re.split(r"\s+", val)
                info[current]["deps"] = [re.split(r"[><=]", d)[0] for d in deps if d]
    return info


def get_deps_of(pkg):
    return _PKG_INFO_CACHE.get(pkg, {}).get("deps", [])


def get_current_version(pkg):
    return _PKG_INFO_CACHE.get(pkg, {}).get("version", "")


def parse_log():
    events = []
    with open(PACMAN_LOG, "r", errors="replace") as f:
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


def build_pkg_stats(events):
    stats = defaultdict(lambda: {
        "first_installed": None,
        "last_updated":    None,
        "total_updates":   0,
        "last_action":     "installed",
        "events":          [],
    })
    for e in events:
        pkg = e["package"]
        s   = stats[pkg]
        s["events"].append(e)
        if e["action"] == "installed" and s["first_installed"] is None:
            s["first_installed"] = e["date"]
        if e["action"] in ("upgraded", "reinstalled"):
            s["total_updates"] += 1
            s["last_updated"] = e["date"]
        if s["first_installed"] and not s["last_updated"]:
            s["last_updated"] = s["first_installed"]
        s["last_action"] = e["action"]
    return stats


def classify_packages(pkg_stats, official_pkgs, explicit_pkgs):
    explicit_set = set()
    aur_set      = set()
    system_set   = set()
    for pkg in pkg_stats:
        if pkg in official_pkgs:
            if pkg in explicit_pkgs:
                explicit_set.add(pkg)
            else:
                system_set.add(pkg)
        else:
            aur_set.add(pkg)
    return explicit_set, aur_set, system_set


# ── ods helpers ────────────────────────────────────────────────────────────────

def hex_to_rgb(h):
    return f"#{h}"


def style_id(sheet_name):
    return sheet_name.replace(" ", "_").replace(":", "_")


def make_style(doc, name, bg_hex, bold=False, italic=False,
               font_color="000000", font_size="10pt"):
    style = Style(name=name, family="table-cell")
    style.addElement(TableCellProperties(
        backgroundcolor=hex_to_rgb(bg_hex),
        border="0.05pt solid #CCCCCC",
        padding="0.05cm",
    ))
    tp_kwargs = dict(fontsize=font_size, color=hex_to_rgb(font_color))
    if bold:
        tp_kwargs["fontweight"] = "bold"
    if italic:
        tp_kwargs["fontstyle"] = "italic"
    style.addElement(TextProperties(**tp_kwargs))
    doc.automaticstyles.addElement(style)
    return name


def register_sheet_styles(doc, sheet_name, col_widths, rows_data, header_color):
    sid = style_id(sheet_name)

    for i, w in enumerate(col_widths):
        cs = Style(name=f"{sid}_col{i}", family="table-column")
        cs.addElement(TableColumnProperties(columnwidth=f"{w}cm"))
        doc.automaticstyles.addElement(cs)

    hrs = Style(name=f"{sid}_hrow", family="table-row")
    hrs.addElement(TableRowProperties(rowheight="0.8cm", useoptimalrowheight="false"))
    doc.automaticstyles.addElement(hrs)

    rs = Style(name=f"{sid}_row", family="table-row")
    rs.addElement(TableRowProperties(rowheight="0.7cm", useoptimalrowheight="false"))
    doc.automaticstyles.addElement(rs)

    make_style(doc, f"{sid}_header", header_color, bold=True, font_color="FFFFFF")

    seen = set()
    for _, bg_hex, is_dep in rows_data:
        key = (bg_hex, is_dep)
        if key not in seen:
            seen.add(key)
            font_color = "666666" if is_dep else "000000"
            make_style(doc, f"{sid}_c_{bg_hex}_{int(is_dep)}",
                       bg_hex, italic=is_dep, font_color=font_color)


def add_cell(row, style_name, value, doc):
    cell = TableCell(stylename=style_name, valuetype="string")
    cell.addElement(P(text=str(value) if value is not None else ""))
    row.addElement(cell)


def write_sheet(doc, sheet_name, col_names, col_widths, rows_data, header_color):
    sid   = style_id(sheet_name)
    table = Table(name=sheet_name)

    for i in range(len(col_widths)):
        table.addElement(TableColumn(stylename=f"{sid}_col{i}"))

    hrow = TableRow(stylename=f"{sid}_hrow")
    for col in col_names:
        add_cell(hrow, f"{sid}_header", col, doc)
    table.addElement(hrow)

    for row_vals, bg_hex, is_dep in rows_data:
        tr = TableRow(stylename=f"{sid}_row")
        for val in row_vals:
            add_cell(tr, f"{sid}_c_{bg_hex}_{int(is_dep)}", val, doc)
        table.addElement(tr)

    doc.spreadsheet.addElement(table)


# ── sheet builders ─────────────────────────────────────────────────────────────

def lighten(hex_color, amount=40):
    r = min(255, int(hex_color[0:2], 16) + amount)
    g = min(255, int(hex_color[2:4], 16) + amount)
    b = min(255, int(hex_color[4:6], 16) + amount)
    return f"{r:02x}{g:02x}{b:02x}"


def build_summary_rows(pkg_list, pkg_stats, show_deps):
    def sort_key(pkg):
        lu = pkg_stats[pkg]["last_updated"]
        return lu if lu else "0000-00-00"

    rows = []
    for pkg in sorted(pkg_list, key=sort_key, reverse=True):
        s       = pkg_stats[pkg]
        version = get_current_version(pkg)
        lu      = s["last_updated"] or s["first_installed"] or ""
        fi      = s["first_installed"] or ""
        tu      = s["total_updates"]
        color   = COLORS.get(s.get("last_action", "installed"), "C6EFCE")
        rows.append(([lu, pkg, fi, tu, version], color, False))

        if show_deps:
            for dep in sorted(get_deps_of(pkg)):
                if dep not in pkg_stats:
                    continue
                ds   = pkg_stats[dep]
                dver = get_current_version(dep)
                dlu  = ds["last_updated"] or ds["first_installed"] or ""
                dfi  = ds["first_installed"] or ""
                dtu  = ds["total_updates"]
                dcol = lighten(COLORS.get(ds.get("last_action", "installed"), "C6EFCE"))
                rows.append(([dlu, f"  \u2514 {dep}", dfi, dtu, dver], dcol, True))
    return rows


def build_history_rows(events):
    rows = []
    for e in reversed(events):
        color = COLORS.get(e["action"], "FFFFFF")
        rows.append((
            [e["date"], e["time"], e["action"], e["package"], e["version"]],
            color, False
        ))
    return rows


# ── main ───────────────────────────────────────────────────────────────────────

def run(include_history=True, silent=False):
    ensure_out_dir()

    if not silent:
        print("Reading official package list from pacman...")
    official_pkgs = get_official_packages()
    if not silent:
        print(f"  {len(official_pkgs)} packages in official repos")
        print("Reading explicitly installed packages...")
    explicit_pkgs = get_explicit_packages()
    if not silent:
        print(f"  {len(explicit_pkgs)} explicitly installed packages")
        print(f"Parsing {PACMAN_LOG}...")
    events    = parse_log()
    pkg_stats = build_pkg_stats(events)
    if not silent:
        print(f"  {len(events)} events, {len(pkg_stats)} unique packages")
        print("Classifying packages...")
    explicit_set, aur_set, system_set = \
        classify_packages(pkg_stats, official_pkgs, explicit_pkgs)
    if not silent:
        print(f"  {len(explicit_set)} explicit, {len(aur_set)} AUR, "
              f"{len(system_set)} system packages")
        print("Fetching package info (versions + dependencies)...")
    global _PKG_INFO_CACHE

    # load existing cache
    disk_cache = {}
    if PKG_CACHE.exists():
        try:
            disk_cache = json.loads(PKG_CACHE.read_text())
        except Exception:
            disk_cache = {}

    # only query packages not already cached
    all_pkgs     = set(pkg_stats.keys())
    cached_pkgs  = set(disk_cache.keys())
    # always re-query packages involved in this log's most recent event
    # (their version/deps may have changed)
    recent_pkgs  = {e["package"] for e in events[-50:]}
    need_query   = (all_pkgs - cached_pkgs) | (recent_pkgs & all_pkgs)

    if need_query:
        fresh = batch_pkg_info(need_query)
        disk_cache.update(fresh)
        try:
            PKG_CACHE.write_text(json.dumps(disk_cache))
        except Exception:
            pass
        if not silent:
            print(f"  {len(need_query)} packages queried, {len(cached_pkgs)} from cache")
    else:
        if not silent:
            print(f"  {len(disk_cache)} packages loaded from cache")

    _PKG_INFO_CACHE = disk_cache

    if not silent:
        print("Building spreadsheet...")
    doc = OpenDocumentSpreadsheet()

    exp_rows  = build_summary_rows(explicit_set, pkg_stats, show_deps=True)
    aur_rows  = build_summary_rows(aur_set, pkg_stats, show_deps=True)
    sys_rows  = build_summary_rows(system_set, pkg_stats, show_deps=False)
    hist_rows = build_history_rows(events)

    hist_exp_rows = hist_aur_rows = hist_sys_rows = []
    if include_history:
        hist_exp_rows = build_history_rows([e for e in events if e["package"] in explicit_set])
        hist_aur_rows = build_history_rows([e for e in events if e["package"] in aur_set])
        hist_sys_rows = build_history_rows([e for e in events if e["package"] in system_set])

    # register ALL styles before creating any tables
    register_sheet_styles(doc, "Official Repository", SUMMARY_WIDTHS, exp_rows,      HEADER_COLORS["Official Repository"])
    register_sheet_styles(doc, "AUR",                 SUMMARY_WIDTHS, aur_rows,      HEADER_COLORS["AUR"])
    register_sheet_styles(doc, "System Packages",     SUMMARY_WIDTHS, sys_rows,      HEADER_COLORS["System Packages"])
    register_sheet_styles(doc, "History Official Repo", HISTORY_WIDTHS, hist_exp_rows, HEADER_COLORS["History Official Repo"])
    register_sheet_styles(doc, "History AUR",           HISTORY_WIDTHS, hist_aur_rows, HEADER_COLORS["History AUR"])
    register_sheet_styles(doc, "History System",        HISTORY_WIDTHS, hist_sys_rows, HEADER_COLORS["History System"])
    register_sheet_styles(doc, "History",               HISTORY_WIDTHS, hist_rows,     HEADER_COLORS["History"])

    # write all tables
    write_sheet(doc, "Official Repository", SUMMARY_COLS, SUMMARY_WIDTHS, exp_rows,      HEADER_COLORS["Official Repository"])
    write_sheet(doc, "AUR",                 SUMMARY_COLS, SUMMARY_WIDTHS, aur_rows,      HEADER_COLORS["AUR"])
    write_sheet(doc, "System Packages",     SUMMARY_COLS, SUMMARY_WIDTHS, sys_rows,      HEADER_COLORS["System Packages"])
    if include_history:
        write_sheet(doc, "History Official Repo", HISTORY_COLS, HISTORY_WIDTHS, hist_exp_rows, HEADER_COLORS["History Official Repo"])
        write_sheet(doc, "History AUR",           HISTORY_COLS, HISTORY_WIDTHS, hist_aur_rows, HEADER_COLORS["History AUR"])
        write_sheet(doc, "History System",        HISTORY_COLS, HISTORY_WIDTHS, hist_sys_rows, HEADER_COLORS["History System"])
    write_sheet(doc, "History", HISTORY_COLS, HISTORY_WIDTHS, hist_rows, HEADER_COLORS["History"])

    doc.save(str(OUT_PATH))
    print(f"pkglog updated - {OUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="pkglog - Arch Linux package history tracker"
    )
    parser.add_argument("--setup",  action="store_true", help="First-time setup: install script + hook + generate spreadsheet")
    parser.add_argument("--update", action="store_true", help="Regenerate the spreadsheet")
    parser.add_argument("--view",   action="store_true", help="Open the spreadsheet in LibreOffice (default)")
    args = parser.parse_args()

    if args.setup:
        setup()
    elif args.update:
        if not check_dependencies():
            sys.exit(1)
        if sys.stdin.isatty():
            print("""
Row colors in the spreadsheet:
  Green  - installed   (package was freshly installed)
  Blue   - upgraded    (package was upgraded to a newer version)
  Yellow - reinstalled (package was reinstalled at the same version)
  Red    - removed     (package was removed from the system)

Dependency rows appear indented under their parent package
and use a lighter shade of the same color.
""")
            ans = input("Generate per-category history sheets? This will take more time. [y/N]: ").strip().lower()
            run(include_history=(ans == "y"))
        else:
            # running from pacman hook — no terminal, skip prompt
            run(include_history=False, silent=True)
    else:
        # default and --view: open the spreadsheet
        view()
