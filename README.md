# DupCls

Fast, console-based duplicate file cleaner for Windows.

DupCls scans a folder or a single reference file, finds duplicate files by content, and keeps one safe copy from each duplicate group. It can run in report-only mode, move duplicates to the Recycle Bin, or permanently delete duplicates after confirmation.

The scanner avoids unnecessary disk reads by grouping files by size first, then using a quick first/last chunk hash, and only full-hashing files that still need confirmation.

## Features

- Three-stage duplicate detection: file size, quick first/last chunk hash, then full content hash.
- Folder scan mode and single reference-file mode.
- Reference-file mode keeps the dropped file first when duplicates are found.
- Safer keep selection for folder scans, preferring original-looking names over copy, duplicate, backup, old, or numbered names.
- Optional BLAKE3 hashing with a built-in `hashlib.blake2b` fallback.
- Native Windows Recycle Bin deletion through batched `SHFileOperationW`, with `Send2Trash` fallback when available.
- Permanent delete mode with a default `No` confirmation prompt.
- CSV duplicate reports, unreadable-file reports, and delete logs.
- Runtime file logging with configurable log level and log path.
- Interactive settings for recursive scan, symlink handling, worker count, and hash chunk sizes.
- Built-in self-test for duplicate detection, keep selection, reference-file mode, and permanent deletion logic.

## Requirements

- Python 3.10 or newer.
- Windows 10 or Windows 11 is recommended.
- Optional packages listed in `requirements.txt`:
  - `blake3`
  - `Send2Trash`

DupCls is intended for Windows console use. The core scan and self-test paths are also exercised on Ubuntu in CI, but Windows provides the native Recycle Bin path and administrator relaunch support.

## Installation

Clone the repository:

```powershell
git clone https://github.com/KiaroSama/DupCls.git
cd DupCls
```

Install the optional dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run DupCls directly:

```powershell
python DupCls.py
```

Or use the PowerShell launcher:

```powershell
.\run.ps1
```

To make `DupCls` available as a command in new terminal sessions, run this once from the project folder:

```powershell
.\install-command.ps1
```

Then open a new terminal and run:

```powershell
DupCls
```

## Usage

Start the interactive menu:

```powershell
python DupCls.py
```

Menu options:

```text
1) Scan only and create duplicate report
2) Scan and move duplicates to Recycle Bin
3) Scan and permanently delete duplicates
4) Settings
5) Check dependencies again
0) Exit
{back=0, admin=admin, quit=exit}:
```

When asked for a target path, enter or drag and drop a folder or file:

- Dropping a folder scans that folder.
- Dropping a file enables reference-file mode. DupCls scans the file's parent folder and keeps the dropped file first if duplicates are found.

Useful command-line flags:

```powershell
python DupCls.py --self-test
python DupCls.py --log-level DEBUG
python DupCls.py --log-file .\reports\dupcls-debug.log
python DupCls.py --log-dir .\reports
python DupCls.py --no-color
python DupCls.py --no-log
```

Run the built-in validation suite:

```powershell
python -m py_compile DupCls.py
python DupCls.py --self-test
```

## Safety Notes

DupCls can move or delete real files. Review the generated report before deleting anything.

- Use option `1` first to create a report without deleting files.
- Option `2` moves duplicates to the Recycle Bin when supported.
- Option `3` permanently deletes duplicates and cannot be undone.
- Permanent deletion defaults to `No`; pressing Enter cancels the delete.
- Reference-file mode keeps the dropped file first.
- Folder scan mode keeps the best-ranked original-looking file first.
- Symlinks are not followed by default. Enable symlink following only when you understand the target tree.
- Reports and logs include file paths from your machine. Do not publish generated reports or logs if they contain private paths or filenames.

## Keep Selection

For a normal folder scan, DupCls ranks duplicate files and keeps the first item in each group.

The ranking prefers:

1. Names without copy markers such as `copy`, `duplicate`, `dupe`, `clone`, `backup`, `old`, `(1)`, or `[1]`.
2. Shorter file names.
3. Shallower paths.
4. Stable alphabetical order.

Example:

```text
Video.mkv         KEEP
Video - Copy.mkv  DELETE
Video (1).mkv     DELETE
```

In reference-file mode, the file you dropped is always kept first.

## Logs and Output

Generated files are written to `reports/` by default:

| File pattern | Purpose |
| --- | --- |
| `dupcls_duplicates_report_YYYYMMDD_HHMMSS.csv` | Keep/delete plan for duplicate groups. |
| `dupcls_unreadable_files_YYYYMMDD_HHMMSS.csv` | Files that could not be read or hashed. |
| `dupcls_delete_log_YYYYMMDD_HHMMSS.csv` | Results from a delete operation. |
| `dupcls_run_YYYYMMDD_HHMMSS.log` | Runtime diagnostics, settings, scan stats, errors, and timings. |

The `reports/` directory and `*.log` files are ignored by Git because they may contain local paths or private filenames.

## Important Repository Files

| Path | Description |
| --- | --- |
| `DupCls.py` | Main Python CLI application. |
| `run.ps1` | Windows PowerShell launcher that finds Python 3.10+ and runs `DupCls.py`. |
| `DupCls.cmd` | Command Prompt wrapper that calls `run.ps1`. |
| `install-command.ps1` | Adds this project folder to the current user's `PATH`. |
| `requirements.txt` | Optional runtime dependencies. |
| `.github/workflows/self-test.yml` | GitHub Actions workflow for compile and self-test checks. |
| `.gitignore` | Excludes local logs, cache files, generated output, secrets, and private config. |
| `LICENSE` | MIT License. |
| `ATTRIBUTION.md` | Attribution notice for redistribution. |
| `GITHUB_RELEASE_NOTES.md` | Draft release notes for the first GitHub release. |

## Troubleshooting

### Python is not found

Install Python 3.10 or newer, then reopen the terminal. The launcher tries `py -3`, `python`, `python3`, and user-local Python installs.

### Optional dependencies are missing

Run:

```powershell
python -m pip install -r requirements.txt
```

If `blake3` is missing, DupCls falls back to `hashlib.blake2b`. If `Send2Trash` is missing, non-native Recycle Bin fallback behavior may be unavailable.

### Recycle Bin deletion fails

Run a scan-only report first and inspect the delete plan. If Recycle Bin deletion fails because of permissions, try running the script from an elevated terminal or use the `admin` command in an interactive prompt on Windows.

### A file cannot be read or deleted

The file may be locked, protected, or unavailable. Check the unreadable-file report and runtime log for the exact path and error.

### Console colors look wrong

Run with:

```powershell
python DupCls.py --no-color
```

## License and Attribution

This project is released under the MIT License.

You are free to use, copy, modify, publish, distribute, sublicense, and use this project in your own projects, including free or commercial projects.

However, if you copy, modify, publish, distribute, or include substantial parts of this project in another project, you must keep the original copyright and license notice.

Please preserve this attribution:

DupCls - Copyright (c) 2026 Kiaro Sama  
Original author: Kiaro Sama  
GitHub: https://github.com/KiaroSama  
Original repository: https://github.com/KiaroSama/DupCls  
Licensed under the MIT License.

## Donate

If this project helps you, donations are appreciated.

| Currency | Network | Address |
| --- | --- | --- |
| Bitcoin (BTC) | Bitcoin | `bc1qmth5m03pu5hujw5xw5jmywam3jj3sqwqupesdt` |
| USDT, BNB, USDC, etc. | BEP20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| USDT, TRX, USDC, etc. | TRC20 | `TWBA3xFTqgZAeAYMxqo85xWnzvty3DcAhw` |
| Ethereum (ETH) | ERC20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| TON | TON | `UQCN8Umo_OfOWqImZetQsrNStPcmLkMAKajFyiCOhso23NDb` |
| Litecoin (LTC) | LTC | `ltc1qntqnnrunadurnw4cshv3qgspywrueyyeyngwuy` |
| Solana (SOL) | Solana | `7B2wkczUjmkDhETwQuknBL8sUsbuV7nErxc317TmQuwR` |
| Polygon (POL) | Polygon | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |

## Author

Author: Kiaro Sama  
GitHub: https://github.com/KiaroSama
