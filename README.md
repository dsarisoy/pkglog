# pkglog
A package history tracker for Arch Linux. Parses `/var/log/pacman.log` and generates a formatted `.ods` spreadsheet with a full record of every package ever installed, updated, or removed on your system — broken out by source.
Useful for auditing your system after security incidents like the [June 2026 AUR supply-chain attack](https://www.privacyguides.org/news/2026/06/12/around-1-500-aur-packages-compromised-with-rootkit-like-malware/).
---
## Preview
![pkglog spreadsheet preview](preview.png)
## Sheets
| Sheet | Contents |
|---|---|
| **Official Repository** | Packages you manually installed via `pacman -S` from official repos |
| **AUR** | AUR and manually installed packages |
| **System Packages** | Auto-installed dependencies from official repos |
| **History Official Repo** | Every pacman event for Official Repository packages |
| **History AUR** | Every pacman event for AUR packages |
| **History System** | Every pacman event for System Packages |
| **History** | Every pacman event ever, color-coded by action |

### All sheet columns
| Column | Description |
|---|---|
| Last Updated | Date of most recent upgrade or reinstall |
| Package | Package name (dependency rows are indented with └) |
| First Installed | Date first installed |
| Total Updates | Count of all upgrades and reinstalls |
| Current Version | Currently installed version |

History sheets use these columns instead:

| Column | Description |
|---|---|
| Date | Date of the event |
| Time | Time of the event |
| Action | `installed` / `upgraded` / `reinstalled` / `removed` |
| Package | Package name |
| Version / Change | Version string, or `old -> new` for upgrades |

Rows are color-coded by action:
- 🟢 Green — installed
- 🔵 Blue — upgraded
- 🟡 Yellow — reinstalled
- 🔴 Red — removed

Summary sheets (Official Repository, AUR) show one row per package with dependency rows indented underneath. Dependency rows use a lighter shade of the same action color.
---
## Requirements
- Arch Linux (requires `pacman`)
- Python 3.8+
- `python-odfpy`

```bash
sudo pacman -S python-odfpy
```

To open the generated spreadsheet:
```bash
sudo pacman -S libreoffice-fresh
```
---
## Installation
```bash
git clone https://github.com/dsarisoy/pkglog
cd pkglog
python3 logscript.py --setup
```
Once setup is run, you can delete the pkglog repo if you wish. 

`--setup` will:
1. Check that dependencies are present
2. Install the script to `/usr/local/bin/pkglog` (prompts for sudo)
3. Install a pacman hook to `/etc/pacman.d/hooks/pkglog.hook` (prompts for sudo)
4. Copy the README to `/var/log/pkglog/README.md`
5. Generate the initial spreadsheet from your full pacman log history

---
## File locations

| File | Path |
|---|---|
| Script | `/usr/local/bin/pkglog` |
| Pacman hook | `/etc/pacman.d/hooks/pkglog.hook` |
| Spreadsheet output | `/var/log/pkglog/pkglog.ods` |
| Package info cache | `/var/log/pkglog/pkg_cache.json` |
| README | `/var/log/pkglog/README.md` |
| Pacman log (input) | `/var/log/pacman.log` |

---
## Usage
```bash
# First-time setup (installs script + hook + generates spreadsheet)
python3 logscript.py --setup

# Open the spreadsheet in LibreOffice
pkglog
pkglog --view

# Regenerate the spreadsheet manually
pkglog --update
```

Once set up, the spreadsheet regenerates automatically after every `pacman` or AUR transaction — installs, upgrades, and removals.

---
## How it works
pacman writes a timestamped log of every transaction to `/var/log/pacman.log`. pkglog parses that log and classifies packages using two queries:
- **Official vs AUR** — checked against `pacman -Slq` (all packages in the sync databases). Packages not found there are classified as AUR or manually installed.
- **Explicit vs system** — checked against `pacman -Qqe` (explicitly installed packages). Official packages not in this list are classified as System Packages.
- **Versions and dependencies** — resolved via a batched `pacman -Qi` call and cached to `/var/log/pkglog/pkg_cache.json`. On subsequent runs only new or recently changed packages are re-queried, making hook runs significantly faster.

The pacman hook (`pkglog.hook`) triggers after every transaction and runs `pkglog --update` silently in the background.

---
## Caveats
- **Source detection is current-state only.** If a package was in the official repos when you installed it but has since been removed from them, it may be misclassified as AUR. This is a limitation of `pacman -Slq`.
- **Explicit detection is current-state only.** If you explicitly installed a package and have since removed it, it won't appear in `pacman -Qqe` and will be treated as a System Package.
- **Dependency tree is current-state only.** Dependency relationships are resolved using `pacman -Qi`, which only reflects currently installed packages. If a package has been removed, its dependency tree is lost and its dependencies will fall back to the System Packages sheet.
- **Log rotation.** If your pacman log has been rotated or truncated, history before that point won't appear. The default Arch setup does not rotate `/var/log/pacman.log` automatically, so this is usually not an issue.
- **The hook runs as root** (all pacman hooks do), writing the ods to `/var/log/pkglog/`. This directory is created automatically by the script.
---
## License
MIT
