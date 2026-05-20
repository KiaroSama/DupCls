# DupCls v1.0.0

## Summary

First public release of DupCls, a fast console-based duplicate file cleaner for Windows.

DupCls scans a folder or single reference file, detects duplicates by content, keeps one safe copy from every duplicate group, and can produce reports, move duplicates to the Recycle Bin, or permanently delete duplicates after confirmation.

## Features

- Three-stage duplicate detection: file size, quick first/last chunk hash, then full content hash.
- Folder scan mode and single reference-file mode.
- Reference-file mode keeps the dropped file first when duplicates are found.
- Original-looking keep selection for normal folder scans.
- Optional BLAKE3 hashing with `hashlib.blake2b` fallback.
- Native Windows Recycle Bin deletion with batched shell operations.
- `Send2Trash` fallback when available.
- Permanent delete mode with a default `No` confirmation prompt.
- CSV duplicate reports, unreadable-file reports, delete logs, and runtime logs.
- Interactive settings for recursive scan, symlink handling, worker count, and hash chunk sizes.
- Built-in self-test for duplicate detection, keep selection, reference-file mode, and permanent deletion logic.

## Requirements

- Python 3.10 or newer.
- Windows 10 or Windows 11 recommended.
- Optional packages from `requirements.txt`:
  - `blake3`
  - `Send2Trash`

## Safety Notes

DupCls can move or permanently delete real files.

- Run option `1` first to create a report before deleting files.
- Review the CSV report before using Recycle Bin or permanent delete modes.
- Permanent deletion cannot be undone and defaults to `No`.
- Generated reports and logs may contain local file paths or private filenames.

## Installation and Quick Start

```powershell
git clone https://github.com/KiaroSama/DupCls.git
cd DupCls
python -m pip install -r requirements.txt
python DupCls.py
```

Run the self-test:

```powershell
python -m py_compile DupCls.py
python DupCls.py --self-test
```

Optional Windows launcher:

```powershell
.\run.ps1
```

Optional command installation:

```powershell
.\install-command.ps1
```

## Included Files

- `DupCls.py` - main Python CLI application.
- `run.ps1` - Windows PowerShell launcher.
- `DupCls.cmd` - Command Prompt wrapper.
- `install-command.ps1` - user PATH installer for the `DupCls` command.
- `requirements.txt` - optional Python dependencies.
- `.github/workflows/self-test.yml` - GitHub Actions compile and self-test workflow.
- `.gitignore` - public-release ignore rules.
- `README.md` - project documentation.
- `LICENSE` - MIT License.
- `ATTRIBUTION.md` - attribution notice.
- `GITHUB_RELEASE_NOTES.md` - first-release draft notes.

## License

DupCls is released under the MIT License.

## Attribution

DupCls - Copyright (c) 2026 Kiaro Sama  
Original author: Kiaro Sama  
GitHub: https://github.com/KiaroSama  
Original repository: https://github.com/KiaroSama/DupCls  
Licensed under the MIT License.
