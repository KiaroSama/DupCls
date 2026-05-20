#!/usr/bin/env python3
"""
DupCls
Fast duplicate file cleaner using size grouping, quick hashing, and full hashing.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import importlib.util
import logging
import os
import platform
import re
import subprocess
import sys
import time
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


APP_NAME = "DupCls"
APP_DESCRIPTION = "Fast duplicate file cleaner"
PYTHON_MIN = (3, 10)
DEFAULT_QUICK_CHUNK_MB = 1
DEFAULT_FULL_CHUNK_MB = 16
DEFAULT_WORKERS = min(8, os.cpu_count() or 4)
BULK_RECYCLE_MAX_PATH_CHARS = 30000
BULK_RECYCLE_MAX_FILES = 250
MENU_PROMPT = "{back=0, admin=admin, quit=exit}"
DEFAULT_LOG_DIR = "reports"
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
COPY_NAME_PATTERNS = [
    re.compile(r"(^|[\s._-])copy(\s+of)?([\s._-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\s._-])(duplicate|dupe|clone)([\s._-]|$)", re.IGNORECASE),
    re.compile(r"(^|[\s._-])(backup|bak|old)([\s._-]|$)", re.IGNORECASE),
    re.compile(r"[\s._-]+copy(?:\s*\(\d+\))?$", re.IGNORECASE),
    re.compile(r"\(\d+\)$"),
    re.compile(r"\[\d+\]$"),
]

BLAKE3 = None
SEND2TRASH = None
LOG_FILE: Path | None = None
USE_COLOR = True
LOGGER = logging.getLogger(APP_NAME)
LOGGER.addHandler(logging.NullHandler())


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    ORANGE = "\033[38;5;214m"
    LIME = "\033[38;5;118m"
    PINK = "\033[38;5;213m"
    SKY = "\033[38;5;117m"
    AQUA = "\033[38;5;51m"
    GOLD = "\033[38;5;220m"


def enable_ansi_colors() -> None:
    """Enable ANSI escape sequences on supported Windows terminals."""
    if not USE_COLOR:
        return
    if os.name != "nt":
        return
    os.system("")
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        LOGGER.debug("Could not enable ANSI colors.", exc_info=True)


def color(text: object, code: str) -> str:
    if not USE_COLOR:
        return str(text)
    return f"{code}{text}{C.RESET}"


def configure_output(no_color: bool = False) -> None:
    """Configure terminal output for interactive consoles and CI logs."""
    global USE_COLOR
    USE_COLOR = not (no_color or os.environ.get("NO_COLOR") or not sys.stdout.isatty())


def parse_log_level(value: str) -> str:
    normalized = value.upper()
    if normalized not in LOG_LEVEL_NAMES:
        choices = ", ".join(LOG_LEVEL_NAMES)
        raise argparse.ArgumentTypeError(f"expected one of: {choices}")
    return normalized


def make_default_log_path(log_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"dupcls_run_{timestamp}.log"


def setup_logging(log_level: str, log_file: Path | None, log_dir: Path, no_log: bool) -> Path | None:
    """Initialize a file logger and return the active log file path."""
    global LOG_FILE

    LOGGER.handlers.clear()
    LOGGER.propagate = False
    LOGGER.setLevel(logging.DEBUG)

    if no_log:
        LOGGER.addHandler(logging.NullHandler())
        LOG_FILE = None
        return None

    requested_path = log_file if log_file is not None else make_default_log_path(log_dir)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        active_path = requested_path.expanduser().resolve()
        active_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(active_path, encoding="utf-8")
    except OSError as exc:
        fallback_path = Path(tempfile.gettempdir()) / APP_NAME / make_default_log_path(Path()).name
        try:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(fallback_path, encoding="utf-8")
            active_path = fallback_path
            print(color(f"Could not create requested log file: {exc}", C.YELLOW))
            print(color(f"Using fallback log file: {active_path}", C.YELLOW))
        except OSError as fallback_exc:
            LOGGER.addHandler(logging.NullHandler())
            LOG_FILE = None
            print(color(f"Logging disabled; could not create log file: {fallback_exc}", C.YELLOW))
            return None

    handler.setLevel(getattr(logging, log_level))
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOG_FILE = active_path

    LOGGER.info("Log started: %s", active_path)
    LOGGER.info("%s - %s", APP_NAME, APP_DESCRIPTION)
    LOGGER.info("Python: %s | executable=%s", sys.version.replace("\n", " "), sys.executable)
    LOGGER.info("Platform: %s", platform.platform())
    LOGGER.info("Working directory: %s", Path.cwd())
    LOGGER.debug("Arguments: %s", sys.argv)
    return active_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=APP_DESCRIPTION)
    parser.add_argument("--self-test", action="store_true", help="run the built-in non-interactive test suite")
    parser.add_argument("--log-level", type=parse_log_level, default=DEFAULT_LOG_LEVEL, help=f"file log level: {', '.join(LOG_LEVEL_NAMES)}")
    parser.add_argument("--log-file", type=Path, default=None, help="write the runtime log to this file")
    parser.add_argument("--log-dir", type=Path, default=Path(DEFAULT_LOG_DIR), help="directory for generated runtime logs")
    parser.add_argument("--no-log", action="store_true", help="disable runtime file logging")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors for plain CI/log output")
    return parser


def menu_title(text: str) -> str:
    return color(text, C.BOLD + C.GOLD)


def menu_option(number: str, label: str, accent: str) -> str:
    return f"{color(number + ')', C.BOLD + accent)} {color(label, C.WHITE)}"


def clear_screen() -> None:
    if not sys.stdout.isatty():
        return
    os.system("cls" if os.name == "nt" else "clear")


def print_header() -> None:
    clear_screen()
    print(color("=" * 72, C.AQUA))
    print(color(f"  {APP_NAME}", C.BOLD + C.GOLD))
    print(color(f"  {APP_DESCRIPTION}", C.SKY))
    if LOG_FILE is not None:
        print(color(f"  Log: {LOG_FILE}", C.DIM))
    print(color("  Size check", C.LIME) + color(" -> ", C.WHITE) + color("quick hash", C.CYAN) + color(" -> ", C.WHITE) + color("full hash", C.PINK) + color(" -> ", C.WHITE) + color("duplicate cleanup", C.ORANGE))
    print(color("=" * 72, C.AQUA))
    print()


def pause() -> None:
    """Wait until the user presses any key."""
    message = color("\nPress any key to continue...", C.DIM)
    print(message, end="", flush=True)

    if os.name == "nt":
        try:
            import msvcrt

            key = msvcrt.getwch()
            if key in {"\x00", "\xe0"}:
                msvcrt.getwch()
            print()
            return
        except Exception:
            LOGGER.debug("Windows single-key pause failed.", exc_info=True)

    if not sys.stdin.isatty():
        print()
        return

    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print()
    except Exception:
        LOGGER.debug("Raw terminal pause failed; falling back to input().", exc_info=True)
        input()


def is_running_as_admin() -> bool:
    """Return True when the current process has administrator rights."""
    if os.name != "nt":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        LOGGER.debug("Could not determine administrator status.", exc_info=True)
        return False


def relaunch_as_admin(reason: str | None = None) -> None:
    """Restart the script with administrator rights through Windows UAC."""
    if is_running_as_admin():
        if reason:
            print(color(f"Already running as administrator. Reason: {reason}", C.YELLOW))
            LOGGER.info("Administrator relaunch skipped; already elevated. reason=%s", reason)
        return

    if os.name != "nt":
        print(color("Automatic elevation is only supported on Windows.", C.RED))
        LOGGER.error("Administrator relaunch requested on a non-Windows platform. reason=%s", reason)
        raise SystemExit(1)

    if reason:
        print(color(f"Administrator mode requested: {reason}", C.YELLOW))
    LOGGER.warning("Requesting administrator relaunch. reason=%s", reason)

    script_path = Path(__file__).resolve()
    parameters = subprocess.list2cmdline([str(script_path), *sys.argv[1:]])

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            parameters,
            str(script_path.parent),
            1,
        )
    except Exception as exc:
        print(color(f"Could not request administrator mode: {exc}", C.RED))
        LOGGER.exception("Could not request administrator mode.")
        raise SystemExit(1)

    if result <= 32:
        print(color("Administrator mode was not started.", C.RED))
        LOGGER.error("Administrator relaunch failed. shell_execute_code=%s", result)
        raise SystemExit(1)

    LOGGER.info("Administrator relaunch started. shell_execute_code=%s", result)
    raise SystemExit


def handle_global_command(value: str) -> None:
    """Handle commands that should work from every input prompt."""
    normalized = value.strip().lower()
    if normalized == "exit":
        LOGGER.info("Global exit command received.")
        raise SystemExit
    if normalized == "admin":
        LOGGER.info("Global administrator command received.")
        relaunch_as_admin("manual request")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(color(f"{prompt}{suffix}: ", C.WHITE)).strip()
    handle_global_command(value)
    LOGGER.debug("Prompt answered. prompt=%r default=%r empty=%s", prompt, default, value == "")
    if value == "" and default is not None:
        return default
    return value


def ask_menu() -> str:
    value = input(color(f"{MENU_PROMPT}: ", C.WHITE)).strip()
    handle_global_command(value)
    LOGGER.debug("Menu choice received: %r", value)
    return value


def ask_yes_no(prompt: str, default_yes: bool = True) -> bool | None:
    """Ask a yes/no question. Return None when the user chooses back with 0."""
    hint = "Y/n" if default_yes else "y/N"
    while True:
        value = input(color(f"{prompt} [{hint}] {MENU_PROMPT}: ", C.WHITE)).strip().lower()
        handle_global_command(value)
        if value == "0":
            LOGGER.debug("Yes/no prompt returned back. prompt=%r", prompt)
            return None
        if value == "":
            LOGGER.debug("Yes/no prompt used default. prompt=%r default_yes=%s", prompt, default_yes)
            return default_yes
        if value in {"y", "yes"}:
            LOGGER.debug("Yes/no prompt answered yes. prompt=%r", prompt)
            return True
        if value in {"n", "no"}:
            LOGGER.debug("Yes/no prompt answered no. prompt=%r", prompt)
            return False
        print(color("Please enter y, n, or 0.", C.YELLOW))
        LOGGER.debug("Invalid yes/no answer. prompt=%r answer=%r", prompt, value)


def parse_positive_int_or_back(raw: str) -> int | None:
    """Parse a positive integer setting. Return None when the user chooses back."""
    normalized = raw.strip()
    if normalized == "0":
        return None
    return max(1, int(normalized))


def require_python_version() -> None:
    if sys.version_info < PYTHON_MIN:
        current = ".".join(map(str, sys.version_info[:3]))
        required = ".".join(map(str, PYTHON_MIN))
        print(color(f"Python {required}+ is required. Current version: {current}", C.RED))
        LOGGER.error("Unsupported Python version. current=%s required=%s", current, required)
        raise SystemExit(1)


def module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def install_packages(packages: list[str]) -> bool:
    print(color("\nInstalling dependencies...", C.CYAN))
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    LOGGER.info("Installing dependencies: %s", ", ".join(packages))
    try:
        result = subprocess.run(cmd, check=False)
        success = result.returncode == 0
        LOGGER.info("Dependency installation finished. success=%s returncode=%s", success, result.returncode)
        return success
    except Exception as exc:
        print(color(f"Dependency installation failed: {exc}", C.RED))
        LOGGER.exception("Dependency installation failed.")
        return False


def load_optional_modules() -> None:
    global BLAKE3, SEND2TRASH

    BLAKE3 = None
    SEND2TRASH = None

    try:
        import blake3 as blake3_module
        BLAKE3 = blake3_module
        LOGGER.debug("Loaded optional module: blake3")
    except Exception:
        BLAKE3 = None
        LOGGER.debug("Optional module unavailable: blake3", exc_info=True)

    try:
        from send2trash import send2trash as send2trash_func
        SEND2TRASH = send2trash_func
        LOGGER.debug("Loaded optional module: send2trash")
    except Exception:
        SEND2TRASH = None
        LOGGER.debug("Optional module unavailable: send2trash", exc_info=True)


def check_dependencies(interactive: bool = True, quiet_if_ok: bool = False) -> bool:
    """Check optional dependencies and return True when user attention is needed."""
    required = [
        ("blake3", "blake3", "Fast CPU hashing engine"),
        ("send2trash", "Send2Trash", "Move duplicates to Recycle Bin"),
    ]

    missing = []
    status_lines = []

    for module_name, package_name, description in required:
        if module_available(module_name):
            status_lines.append(("OK", package_name, description, C.GREEN))
            LOGGER.debug("Dependency available: %s", package_name)
        else:
            status_lines.append(("NO", package_name, description, C.YELLOW))
            missing.append(package_name)
            LOGGER.warning("Dependency missing: %s", package_name)

    should_print_status = not quiet_if_ok or bool(missing)
    if should_print_status:
        print(color("Checking dependencies...", C.CYAN))
        for status, package_name, description, status_color in status_lines:
            print(color(f"  {status:<2}  {package_name:<12} {description}", status_color))

    if missing and interactive:
        print(color("\nMissing dependencies:", C.YELLOW), ", ".join(missing))
        LOGGER.info("Missing dependencies need user decision: %s", ", ".join(missing))
        install_choice = ask_yes_no("Install missing dependencies now?", default_yes=True)
        if install_choice is True:
            success = install_packages(missing)
            importlib.invalidate_caches()
            if not success:
                print(color("Some dependencies could not be installed.", C.YELLOW))
                print(color("The script can still run, but some features may be limited.", C.YELLOW))
        elif install_choice is None:
            print(color("Dependency installation skipped. Returning back.", C.YELLOW))
            LOGGER.info("Dependency installation skipped by back command.")
        else:
            print(color("Skipped dependency installation.", C.YELLOW))
            LOGGER.info("Dependency installation declined by user.")

    load_optional_modules()

    warnings = []
    if BLAKE3 is None:
        warnings.append("BLAKE3 is unavailable. Falling back to hashlib.blake2b.")
    if SEND2TRASH is None:
        warnings.append("Send2Trash is unavailable. Recycle Bin deletion will be disabled.")

    user_attention_needed = bool(warnings)
    if warnings:
        if quiet_if_ok and not should_print_status:
            print(color("Checking dependencies...", C.CYAN))
        for warning in warnings:
            print(color(warning, C.YELLOW))
            LOGGER.warning(warning)
    else:
        LOGGER.info("Dependency check passed.")

    return user_attention_needed


def make_hasher(max_threads: int = 1):
    """Create the fastest available content hasher."""
    if BLAKE3 is not None:
        try:
            return BLAKE3.blake3(max_threads=max(1, int(max_threads)))
        except TypeError:
            return BLAKE3.blake3()
    return hashlib.blake2b(digest_size=32)


def parse_dropped_path(path_text: str) -> Path:
    """Normalize a typed or drag-and-dropped console path."""
    text = path_text.strip()

    if text.startswith("& "):
        text = text[2:].strip()

    if text.startswith("file:///"):
        text = text[8:]
        if os.name == "nt" and text.startswith("/"):
            text = text[1:]
        text = text.replace("/", os.sep)

    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]

    text = text.strip().strip('"').strip("'")
    parsed = Path(text).expanduser().resolve()
    LOGGER.debug("Parsed input path. raw=%r parsed=%s", path_text, parsed)
    return parsed


def format_bytes(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num} B"


def format_duration(seconds: float) -> str:
    """Format elapsed seconds as a readable duration."""
    seconds = max(0.0, seconds)
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    fraction = seconds - total_seconds

    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs + fraction:04.1f}"
    if minutes:
        return f"{minutes:02d}:{secs + fraction:04.1f}"
    return f"{seconds:.1f} seconds"


def print_total_elapsed(elapsed_seconds: float) -> None:
    """Print active operation time, excluding time spent waiting for user input."""
    print(color(f"Total time elapsed: {format_duration(elapsed_seconds)}", C.WHITE))


def iter_files(root: Path, recursive: bool, follow_symlinks: bool):
    if root.is_file():
        LOGGER.debug("Iterating single file: %s", root)
        yield root
        return

    if recursive:
        def on_walk_error(exc: OSError) -> None:
            LOGGER.warning("Could not scan directory during walk: %s", exc)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks, onerror=on_walk_error):
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    if path.is_file() and (follow_symlinks or not path.is_symlink()):
                        yield path
                except OSError as exc:
                    LOGGER.warning("Skipping file during recursive scan: %s | %s", path, exc)
                    continue
    else:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=follow_symlinks):
                            path = Path(entry.path)
                            if follow_symlinks or not path.is_symlink():
                                yield path
                    except OSError as exc:
                        LOGGER.warning("Skipping file during scan: %s | %s", entry.path, exc)
                        continue
        except OSError as exc:
            LOGGER.error("Could not scan root directory: %s | %s", root, exc)
            raise


def collect_files_for_target(target: Path, settings: dict) -> tuple[list[Path], Path | None, Path]:
    """Collect files for a folder scan or single-file reference scan."""
    recursive = settings["recursive"]
    follow_symlinks = settings["follow_symlinks"]

    if target.is_file():
        scan_root = target.parent
        files = list(iter_files(scan_root, recursive=recursive, follow_symlinks=follow_symlinks))
        unique = []
        seen = set()
        for path in [target, *files]:
            try:
                key = path.resolve() if path.exists() else path
            except OSError as exc:
                LOGGER.warning("Could not resolve path while collecting files: %s | %s", path, exc)
                key = path
            if key not in seen:
                seen.add(key)
                unique.append(path)
        LOGGER.info("Collected files for reference-file scan. target=%s scan_root=%s files=%s", target, scan_root, len(unique))
        return unique, target, scan_root

    files = list(iter_files(target, recursive=recursive, follow_symlinks=follow_symlinks))
    LOGGER.info("Collected files for folder scan. target=%s files=%s", target, len(files))
    return files, None, target


def get_file_size(path: Path) -> tuple[int | None, str | None]:
    try:
        return path.stat().st_size, None
    except OSError as exc:
        LOGGER.warning("Could not read file size: %s | %s", path, exc)
        return None, str(exc)


def quick_hash_file(path: Path, size: int, quick_chunk_size: int):
    """Hash the first and last parts of a file for a fast duplicate pre-check."""
    try:
        h = make_hasher()
        h.update(str(size).encode("utf-8"))

        with path.open("rb", buffering=0) as f:
            h.update(f.read(quick_chunk_size))
            if size > quick_chunk_size:
                f.seek(max(0, size - quick_chunk_size))
                h.update(f.read(quick_chunk_size))

        fully_covered = size <= quick_chunk_size * 2
        LOGGER.debug("Quick hash complete. path=%s size=%s fully_covered=%s", path, size, fully_covered)
        return path, size, h.hexdigest(), fully_covered, None
    except OSError as exc:
        LOGGER.warning("Quick hash failed. path=%s size=%s error=%s", path, size, exc)
        return path, size, None, False, str(exc)


def full_hash_file(path: Path, size: int, full_chunk_size: int):
    """Hash the complete file using mmap/BLAKE3 when available, otherwise a fast stream loop."""
    try:
        h = make_hasher()

        if BLAKE3 is not None and hasattr(h, "update_mmap") and size > 0:
            try:
                h.update_mmap(str(path))
                LOGGER.debug("Full hash complete with BLAKE3 mmap. path=%s size=%s", path, size)
                return path, size, h.hexdigest(), None
            except Exception:
                LOGGER.debug("BLAKE3 mmap failed; falling back to stream hashing. path=%s", path, exc_info=True)
                h = make_hasher()

        buffer = bytearray(full_chunk_size)
        view = memoryview(buffer)
        with path.open("rb", buffering=0) as f:
            while True:
                read_count = f.readinto(buffer)
                if not read_count:
                    break
                h.update(view[:read_count])

        LOGGER.debug("Full hash complete. path=%s size=%s", path, size)
        return path, size, h.hexdigest(), None
    except OSError as exc:
        LOGGER.warning("Full hash failed. path=%s size=%s error=%s", path, size, exc)
        return path, size, None, str(exc)


def run_parallel(items, worker_func, workers: int, label: str):
    total = len(items)
    results = []
    if total == 0:
        return results

    done = 0
    last_print = 0.0
    started = time.perf_counter()
    LOGGER.info("%s started. items=%s workers=%s", label, total, workers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker_func, *item) for item in items]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                LOGGER.exception("%s worker failed unexpectedly.", label)
                raise
            done += 1
            now = time.time()
            if done == total or done % 50 == 0 or now - last_print >= 0.5:
                percent = (done / total) * 100
                print(color(f"\r{label}: {done}/{total} ({percent:.1f}%)", C.CYAN), end="", flush=True)
                last_print = now
    print()
    LOGGER.info("%s finished. items=%s elapsed=%s", label, total, format_duration(time.perf_counter() - started))
    return results


def duplicate_keep_rank(path: Path) -> tuple[int, int, int, str, str]:
    """Rank duplicate candidates so cleaner/original-looking names are kept first."""
    name = path.name
    stem = path.stem
    lower_stem = stem.lower()

    copy_penalty = 0
    for pattern in COPY_NAME_PATTERNS:
        if pattern.search(lower_stem):
            copy_penalty += 1

    # Common Windows copy forms should lose against the clean base name.
    if lower_stem.startswith("copy of "):
        copy_penalty += 2
    if " - copy" in lower_stem or "_copy" in lower_stem or ".copy" in lower_stem:
        copy_penalty += 2

    # Prefer the shortest clean file name, then the shallowest path, then stable lexical order.
    path_depth = len(path.parts)
    return (copy_penalty, len(name), path_depth, name.lower(), str(path).lower())


def sort_duplicate_group(paths: list[Path]) -> list[Path]:
    """Sort one duplicate group so the first item is the safest keep candidate."""
    return sorted(paths, key=duplicate_keep_rank)


def write_duplicate_report(report_path: Path, duplicate_groups: list[list[Path]]) -> None:
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["group_id", "status", "size_bytes", "path"])
            for group_id, files in enumerate(duplicate_groups, start=1):
                keep = files[0]
                keep_size = keep.stat().st_size if keep.exists() else ""
                writer.writerow([group_id, "KEEP", keep_size, str(keep)])
                for duplicate in files[1:]:
                    dup_size = duplicate.stat().st_size if duplicate.exists() else ""
                    writer.writerow([group_id, "DELETE", dup_size, str(duplicate)])
        LOGGER.info("Duplicate report written. path=%s groups=%s", report_path, len(duplicate_groups))
    except Exception as exc:
        handle_permission_error(exc, "write the duplicate report")
        LOGGER.exception("Could not write duplicate report: %s", report_path)
        raise


def write_error_report(report_path: Path, errors: list[tuple[Path, str]]) -> None:
    if not errors:
        LOGGER.info("No unreadable file errors to report.")
        return
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["path", "error"])
            for path, error in errors:
                writer.writerow([str(path), error])
        LOGGER.warning("Unreadable/error report written. path=%s errors=%s", report_path, len(errors))
    except Exception as exc:
        handle_permission_error(exc, "write the error report")
        LOGGER.exception("Could not write unreadable/error report: %s", report_path)
        raise


def is_permission_error(exc: BaseException) -> bool:
    """Detect permission-related failures from common Windows and Python exceptions."""
    if isinstance(exc, PermissionError):
        return True
    winerror = getattr(exc, "winerror", None)
    errno = getattr(exc, "errno", None)
    message = str(exc).lower()
    return winerror == 5 or errno in {1, 13} or "access is denied" in message or "permission denied" in message


def handle_permission_error(exc: BaseException, action: str) -> None:
    """Elevate automatically when an operation fails because of missing permissions."""
    if is_permission_error(exc) and not is_running_as_admin():
        print(color(f"Permission error while trying to {action}: {exc}", C.YELLOW))
        LOGGER.warning("Permission error detected. action=%s error=%s", action, exc)
        relaunch_as_admin(f"permission required to {action}")


def duplicate_paths_from_groups(duplicate_groups: list[list[Path]]) -> list[Path]:
    """Flatten duplicate groups into delete targets while keeping the first item in every group."""
    return [path for group in duplicate_groups for path in group[1:]]


def chunk_paths_for_shell_operation(paths: list[Path]) -> list[list[Path]]:
    """Split paths into safe batches for the Windows shell file operation buffer."""
    chunks = []
    current = []
    current_chars = 1

    for path in paths:
        path_chars = len(str(path)) + 1
        if current and (len(current) >= BULK_RECYCLE_MAX_FILES or current_chars + path_chars >= BULK_RECYCLE_MAX_PATH_CHARS):
            chunks.append(current)
            current = []
            current_chars = 1

        current.append(path)
        current_chars += path_chars

    if current:
        chunks.append(current)

    return chunks


def recycle_paths_bulk_windows(paths: list[Path]) -> tuple[int, int, list[tuple[Path, str]]]:
    """Move many files to the Recycle Bin in batches using the native Windows shell API."""
    if os.name != "nt" or not paths:
        LOGGER.warning("Native Windows bulk recycle unavailable. os=%s paths=%s", os.name, len(paths))
        return 0, len(paths), [(path, "Native Windows bulk recycle is unavailable") for path in paths]

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    deleted = 0
    failed = 0
    failures: list[tuple[Path, str]] = []

    for batch_index, batch in enumerate(chunk_paths_for_shell_operation(paths), start=1):
        LOGGER.info("Starting native Recycle Bin batch. batch=%s files=%s", batch_index, len(batch))
        resolved_batch: list[tuple[Path, str]] = []
        for path in batch:
            try:
                resolved_batch.append((path, str(path.resolve())))
            except OSError as exc:
                failed += 1
                failures.append((path, str(exc)))
                LOGGER.warning("Could not resolve path for native Recycle Bin move: %s | %s", path, exc)

        if not resolved_batch:
            continue

        from_buffer = "\0".join(resolved for _path, resolved in resolved_batch) + "\0\0"
        operation = SHFILEOPSTRUCTW()
        operation.hwnd = None
        operation.wFunc = FO_DELETE
        operation.pFrom = from_buffer
        operation.pTo = None
        operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
        operation.fAnyOperationsAborted = 0
        operation.hNameMappings = None
        operation.lpszProgressTitle = None

        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        if result == 0 and not operation.fAnyOperationsAborted:
            deleted += len(resolved_batch)
            LOGGER.info("Native Recycle Bin batch succeeded. batch=%s files=%s", batch_index, len(resolved_batch))
            print(color(f"\rBulk Recycle Bin move: {min(deleted + failed, len(paths))}/{len(paths)}", C.CYAN), end="", flush=True)
        else:
            message = f"SHFileOperationW failed with code {result}"
            if operation.fAnyOperationsAborted:
                message = "Recycle Bin operation was aborted"
            failed += len(resolved_batch)
            failures.extend((path, message) for path, _resolved in resolved_batch)
            LOGGER.warning("Native Recycle Bin batch failed. batch=%s files=%s message=%s", batch_index, len(resolved_batch), message)
            print(color(f"\nBatch {batch_index} failed: {message}", C.YELLOW))

    if deleted:
        print()

    return deleted, failed, failures


def recycle_paths_fallback(paths: list[Path], workers: int) -> tuple[int, int, list[tuple[Path, str]]]:
    """Fallback Recycle Bin deletion for platforms without native bulk support."""
    if SEND2TRASH is None:
        LOGGER.error("Send2Trash fallback requested but unavailable. paths=%s", len(paths))
        return 0, len(paths), [(path, "Send2Trash is not available") for path in paths]

    def move_one(path: Path):
        try:
            SEND2TRASH(str(path))
            LOGGER.debug("Moved to Recycle Bin through fallback: %s", path)
            return path, True, ""
        except Exception as exc:
            LOGGER.warning("Fallback Recycle Bin move failed: %s | %s", path, exc)
            return path, False, str(exc)

    deleted = 0
    failures: list[tuple[Path, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        LOGGER.info("Fallback Recycle Bin move started. paths=%s workers=%s", len(paths), workers)
        future_map = [executor.submit(move_one, path) for path in paths]
        for index, future in enumerate(as_completed(future_map), start=1):
            path, ok, error = future.result()
            if ok:
                deleted += 1
            else:
                failures.append((path, error))
            if index == len(paths) or index % 50 == 0:
                print(color(f"\rRecycle Bin move: {index}/{len(paths)}", C.CYAN), end="", flush=True)
    print()
    LOGGER.info("Fallback Recycle Bin move finished. deleted=%s failed=%s", deleted, len(failures))

    return deleted, len(failures), failures


def permanent_delete_paths(paths: list[Path], workers: int) -> tuple[int, int, list[tuple[Path, str]]]:
    """Delete files permanently in parallel."""
    def delete_one(path: Path):
        try:
            path.unlink()
            LOGGER.debug("Permanently deleted file: %s", path)
            return path, True, ""
        except Exception as exc:
            LOGGER.warning("Permanent delete failed: %s | %s", path, exc)
            return path, False, str(exc)

    deleted = 0
    failures: list[tuple[Path, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        LOGGER.info("Permanent delete started. paths=%s workers=%s", len(paths), workers)
        future_map = [executor.submit(delete_one, path) for path in paths]
        for index, future in enumerate(as_completed(future_map), start=1):
            path, ok, error = future.result()
            if ok:
                deleted += 1
            else:
                failures.append((path, error))
            if index == len(paths) or index % 50 == 0:
                print(color(f"\rPermanent delete: {index}/{len(paths)}", C.CYAN), end="", flush=True)
    print()
    LOGGER.info("Permanent delete finished. deleted=%s failed=%s", deleted, len(failures))

    return deleted, len(failures), failures


def write_delete_log(log_path: Path, deleted_paths: list[Path], failures: list[tuple[Path, str]]) -> None:
    """Write a compact delete log without printing every deleted file to the console."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["status", "path", "error"])
            failed_set = {str(path): error for path, error in failures}
            for path in deleted_paths:
                if str(path) not in failed_set:
                    writer.writerow(["DELETED", str(path), ""])
            for path, error in failures:
                writer.writerow(["FAILED", str(path), error])
        LOGGER.info("Delete CSV log written. path=%s targets=%s failures=%s", log_path, len(deleted_paths), len(failures))
    except Exception as exc:
        handle_permission_error(exc, "write the delete log")
        LOGGER.exception("Could not write delete CSV log: %s", log_path)
        raise


def delete_duplicates(duplicate_groups: list[list[Path]], permanent: bool, log_path: Path, workers: int) -> tuple[int, int]:
    """Delete duplicates using batch operations instead of slow per-file console work."""
    paths = duplicate_paths_from_groups(duplicate_groups)
    if not paths:
        write_delete_log(log_path, [], [])
        LOGGER.info("No duplicate delete targets.")
        return 0, 0

    print(color(f"Processing {len(paths)} delete targets...", C.CYAN))
    LOGGER.warning("Deletion requested. permanent=%s targets=%s", permanent, len(paths))

    if permanent:
        deleted, failed, failures = permanent_delete_paths(paths, workers=workers)
    elif os.name == "nt":
        deleted, failed, failures = recycle_paths_bulk_windows(paths)
        if failures and SEND2TRASH is not None:
            print(color("Falling back to Send2Trash for failed batch items...", C.YELLOW))
            LOGGER.warning("Retrying failed native recycle paths with Send2Trash. failures=%s", len(failures))
            retry_paths = [path for path, _error in failures]
            retry_deleted, retry_failed, retry_failures = recycle_paths_fallback(retry_paths, workers=workers)
            deleted += retry_deleted
            failed = retry_failed
            failures = retry_failures
    else:
        deleted, failed, failures = recycle_paths_fallback(paths, workers=workers)

    for path, error in failures:
        try:
            raise RuntimeError(error)
        except RuntimeError as exc:
            handle_permission_error(exc, "delete duplicate files")
        print(color(f"Failed: {path} | {error}", C.RED))
        LOGGER.error("Delete target failed. path=%s error=%s", path, error)

    write_delete_log(log_path, paths, failures)
    LOGGER.warning("Deletion finished. deleted=%s failed=%s", deleted, failed)
    return deleted, failed


def choose_target() -> Path | None:
    """Ask for a required target path until it is valid or the user goes back."""
    while True:
        raw = ask("Drop or enter target folder/file path (0=back)")
        normalized = raw.strip()

        if normalized == "0":
            LOGGER.info("Target selection cancelled by user.")
            return None

        if normalized == "":
            print(color("Path is required. Drag/drop a folder or file, type a valid path, or type 0 to go back.", C.RED))
            LOGGER.debug("Empty target path rejected.")
            continue

        target = parse_dropped_path(normalized)
        if target.exists() and (target.is_dir() or target.is_file()):
            LOGGER.info("Target selected: %s", target)
            return target

        print(color(f"Invalid path: {target}", C.RED))
        print(color("Please enter a valid folder/file path, drag/drop it into this window, or type 0 to go back.", C.YELLOW))
        LOGGER.warning("Invalid target path rejected: %s", target)


def filter_reference_file_groups(duplicate_groups: list[list[Path]], reference_file: Path | None) -> list[list[Path]]:
    if reference_file is None:
        return duplicate_groups

    try:
        reference_resolved = reference_file.resolve()
    except OSError:
        reference_resolved = reference_file
        LOGGER.warning("Could not resolve reference file for filtering: %s", reference_file)

    filtered = []
    for group in duplicate_groups:
        resolved_paths = []
        for path in group:
            try:
                resolved_paths.append(path.resolve())
            except OSError:
                resolved_paths.append(path)

        if reference_resolved in resolved_paths:
            reordered = [reference_file]
            for path in group:
                try:
                    same = path.resolve() == reference_resolved
                except OSError:
                    same = path == reference_file
                if not same:
                    reordered.append(path)
            filtered.append(reordered)

    LOGGER.info("Reference-file filter applied. before=%s after=%s reference=%s", len(duplicate_groups), len(filtered), reference_file)
    return filtered


def scan_duplicates(target: Path, settings: dict) -> tuple[list[list[Path]], list[tuple[Path, str]], int, Path | None, Path]:
    workers = settings["workers"]
    quick_chunk_size = settings["quick_chunk_mb"] * 1024 * 1024
    full_chunk_size = settings["full_chunk_mb"] * 1024 * 1024

    print(color("\nScanning files...", C.CYAN))
    LOGGER.info("Scan started. target=%s settings=%s", target, settings)
    files, reference_file, scan_root = collect_files_for_target(target, settings)
    print(color(f"Scan root: {scan_root}", C.WHITE))
    if reference_file is not None:
        print(color(f"Reference file mode: {reference_file}", C.YELLOW))
        print(color("The reference file will be kept first if duplicates are found.", C.YELLOW))
    print(color(f"Total files found: {len(files)}", C.WHITE))

    size_groups = defaultdict(list)
    errors = []

    print(color("Grouping by file size...", C.CYAN))
    for path in files:
        size, error = get_file_size(path)
        if error:
            errors.append((path, error))
        else:
            size_groups[size].append(path)

    candidate_size_groups = {size: paths for size, paths in size_groups.items() if len(paths) > 1}
    candidate_files = sum(len(paths) for paths in candidate_size_groups.values())
    skipped_files = len(files) - candidate_files

    print(color(f"Files skipped by unique size: {skipped_files}", C.GREEN))
    print(color(f"Candidate files after size check: {candidate_files}", C.WHITE))
    LOGGER.info("Size grouping finished. total=%s skipped_unique_size=%s candidate_files=%s candidate_groups=%s errors=%s", len(files), skipped_files, candidate_files, len(candidate_size_groups), len(errors))

    if candidate_files == 0:
        LOGGER.info("Scan finished with no duplicate candidates after size check.")
        return [], errors, 0, reference_file, scan_root

    quick_items = []
    for size, paths in candidate_size_groups.items():
        for path in paths:
            quick_items.append((path, size, quick_chunk_size))

    print(color("\nRunning quick hash check...", C.CYAN))
    quick_results = run_parallel(quick_items, quick_hash_file, workers, "Quick hashing")

    quick_groups = defaultdict(list)
    quick_covered_groups = defaultdict(list)
    for path, size, digest, fully_covered, error in quick_results:
        if error:
            errors.append((path, error))
        elif fully_covered:
            quick_covered_groups[(size, digest)].append(path)
        else:
            quick_groups[(size, digest)].append(path)

    full_groups = defaultdict(list)
    quick_resolved_files = 0
    for (size, digest), paths in quick_covered_groups.items():
        if len(paths) > 1:
            quick_resolved_files += len(paths)
            full_groups[(size, f"quick-covered:{digest}")].extend(paths)

    full_items = []
    for (size, _digest), paths in quick_groups.items():
        if len(paths) > 1:
            for path in paths:
                full_items.append((path, size, full_chunk_size))

    if quick_resolved_files:
        print(color(f"Files fully resolved by quick hash: {quick_resolved_files}", C.GREEN))
    print(color(f"Candidate files after quick hash: {len(full_items)}", C.WHITE))
    LOGGER.info("Quick hash stage finished. quick_resolved_files=%s full_hash_candidates=%s errors=%s", quick_resolved_files, len(full_items), len(errors))

    if full_items:
        print(color("\nRunning full hash check...", C.CYAN))
        full_results = run_parallel(full_items, full_hash_file, workers, "Full hashing")

        for path, size, digest, error in full_results:
            if error:
                errors.append((path, error))
            else:
                full_groups[(size, digest)].append(path)

    if not full_groups:
        LOGGER.info("Scan finished with no duplicate groups after hashing.")
        return [], errors, 0, reference_file, scan_root

    duplicate_groups = []
    for paths in full_groups.values():
        if len(paths) > 1:
            duplicate_groups.append(sort_duplicate_group(paths))

    duplicate_groups.sort(key=lambda group: str(group[0]).lower())
    duplicate_groups = filter_reference_file_groups(duplicate_groups, reference_file)

    reclaim_bytes = 0
    for group in duplicate_groups:
        for path in group[1:]:
            try:
                reclaim_bytes += path.stat().st_size
            except OSError as exc:
                LOGGER.warning("Could not read reclaim size for duplicate: %s | %s", path, exc)
                pass

    LOGGER.info("Scan finished. duplicate_groups=%s duplicate_files=%s reclaim_bytes=%s errors=%s", len(duplicate_groups), sum(len(group) - 1 for group in duplicate_groups), reclaim_bytes, len(errors))
    return duplicate_groups, errors, reclaim_bytes, reference_file, scan_root


def run_job(delete_mode: str, settings: dict) -> None:
    target = choose_target()
    if target is None:
        return

    reports_dir = Path.cwd() / "reports"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"dupcls_duplicates_report_{timestamp}.csv"
    error_path = reports_dir / f"dupcls_unreadable_files_{timestamp}.csv"
    delete_log_path = reports_dir / f"dupcls_delete_log_{timestamp}.csv"

    total_active_elapsed = 0.0
    LOGGER.info("Job started. mode=%s target=%s reports_dir=%s settings=%s", delete_mode, target, reports_dir, settings)

    scan_start = time.perf_counter()
    duplicate_groups, errors, reclaim_bytes, reference_file, scan_root = scan_duplicates(target, settings)
    scan_elapsed = time.perf_counter() - scan_start
    total_active_elapsed += scan_elapsed

    duplicate_count = sum(len(group) - 1 for group in duplicate_groups)

    report_start = time.perf_counter()
    write_duplicate_report(report_path, duplicate_groups)
    write_error_report(error_path, errors)
    total_active_elapsed += time.perf_counter() - report_start
    LOGGER.info("Reports finished. duplicate_report=%s error_report=%s errors=%s", report_path, error_path if errors else None, len(errors))

    print(color("\nResult", C.BOLD + C.CYAN))
    print(color(f"Target: {target}", C.WHITE))
    print(color(f"Scan root: {scan_root}", C.WHITE))
    print(color(f"Mode: {'single reference file' if reference_file else 'folder scan'}", C.WHITE))
    print(color(f"Duplicate groups: {len(duplicate_groups)}", C.WHITE))
    print(color(f"Duplicate files: {duplicate_count}", C.WHITE))
    print(color(f"Reclaimable size: {format_bytes(reclaim_bytes)}", C.WHITE))
    print(color(f"Scan time: {format_duration(scan_elapsed)}", C.WHITE))
    print(color(f"Report saved: {report_path}", C.GREEN))

    if errors:
        print(color(f"Unreadable/error report saved: {error_path}", C.YELLOW))

    if not duplicate_groups:
        print(color("No duplicate files found.", C.GREEN))
        print_total_elapsed(total_active_elapsed)
        if LOG_FILE is not None:
            print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
        LOGGER.info("Job finished. mode=%s result=no_duplicates elapsed=%s", delete_mode, format_duration(total_active_elapsed))
        return

    if delete_mode == "dry_run":
        print(color("Dry run finished. No files were deleted.", C.YELLOW))
        print_total_elapsed(total_active_elapsed)
        if LOG_FILE is not None:
            print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
        LOGGER.info("Job finished. mode=dry_run duplicate_files=%s elapsed=%s", duplicate_count, format_duration(total_active_elapsed))
        return

    permanent = delete_mode == "permanent"

    if not permanent and os.name != "nt" and SEND2TRASH is None:
        print(color("Recycle Bin deletion is unavailable because Send2Trash is not installed.", C.RED))
        print(color("Run dependency installation or use permanent deletion from the menu.", C.YELLOW))
        print_total_elapsed(total_active_elapsed)
        if LOG_FILE is not None:
            print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
        LOGGER.error("Recycle deletion unavailable on this platform because Send2Trash is missing.")
        return

    print(color("\nDeletion summary", C.BOLD + C.YELLOW))
    print(color(f"Files to delete: {duplicate_count}", C.YELLOW))
    print(color(f"Space to reclaim: {format_bytes(reclaim_bytes)}", C.YELLOW))
    print(color("Cleaner/original-looking names are kept first in each duplicate group.", C.YELLOW))
    if reference_file is not None:
        print(color("Reference-file mode keeps the dropped file first.", C.YELLOW))
    print(color(f"Full report: {report_path}", C.YELLOW))

    if permanent:
        print(color("WARNING: Permanent deletion cannot be undone.", C.RED))
        confirm_delete = ask_yes_no("Permanently delete duplicate files now?", default_yes=False)
        if confirm_delete is None:
            print(color("Back. No files were deleted.", C.YELLOW))
            print_total_elapsed(total_active_elapsed)
            if LOG_FILE is not None:
                print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
            LOGGER.info("Permanent deletion skipped by back command.")
            return
        if confirm_delete is False:
            print(color("Deletion cancelled.", C.YELLOW))
            print_total_elapsed(total_active_elapsed)
            if LOG_FILE is not None:
                print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
            LOGGER.info("Permanent deletion declined.")
            return
    else:
        confirm_delete = ask_yes_no("Move duplicate files to Recycle Bin now?", default_yes=True)
        if confirm_delete is None:
            print(color("Back. No files were deleted.", C.YELLOW))
            print_total_elapsed(total_active_elapsed)
            if LOG_FILE is not None:
                print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
            LOGGER.info("Recycle deletion skipped by back command.")
            return
        if confirm_delete is False:
            print(color("Deletion cancelled.", C.YELLOW))
            print_total_elapsed(total_active_elapsed)
            if LOG_FILE is not None:
                print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
            LOGGER.info("Recycle deletion declined.")
            return

    delete_start = time.perf_counter()
    deleted, failed = delete_duplicates(duplicate_groups, permanent=permanent, log_path=delete_log_path, workers=settings["workers"])
    total_active_elapsed += time.perf_counter() - delete_start
    print(color("\nDeletion finished", C.BOLD + C.CYAN))
    print(color(f"Deleted: {deleted}", C.GREEN))
    print(color(f"Failed: {failed}", C.RED if failed else C.GREEN))
    print(color(f"Delete log saved: {delete_log_path}", C.GREEN))
    print_total_elapsed(total_active_elapsed)
    if LOG_FILE is not None:
        print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
    LOGGER.info("Job finished. mode=%s deleted=%s failed=%s elapsed=%s", delete_mode, deleted, failed, format_duration(total_active_elapsed))


def settings_menu(settings: dict) -> None:
    while True:
        print_header()
        print(menu_title("Settings"))
        print(f"{menu_option('1', 'Recursive scan:', C.LIME)}      {color(str(settings['recursive']), C.GREEN if settings['recursive'] else C.YELLOW)}")
        print(f"{menu_option('2', 'Follow symlinks:', C.SKY)}     {color(str(settings['follow_symlinks']), C.YELLOW if settings['follow_symlinks'] else C.GREEN)}")
        print(f"{menu_option('3', 'Workers:', C.CYAN)}             {color(str(settings['workers']), C.AQUA)}")
        print(f"{menu_option('4', 'Quick chunk size MB:', C.PINK)} {color(str(settings['quick_chunk_mb']), C.PINK)}")
        print(f"{menu_option('5', 'Full chunk size MB:', C.ORANGE)}  {color(str(settings['full_chunk_mb']), C.ORANGE)}")
        print(menu_option("0", "Back", C.GOLD))
        choice = ask_menu()

        if choice == "0":
            LOGGER.info("Leaving settings menu.")
            return
        if choice == "1":
            settings["recursive"] = not settings["recursive"]
            LOGGER.info("Setting changed. recursive=%s", settings["recursive"])
        elif choice == "2":
            settings["follow_symlinks"] = not settings["follow_symlinks"]
            LOGGER.info("Setting changed. follow_symlinks=%s", settings["follow_symlinks"])
        elif choice == "3":
            raw = ask("Workers", str(settings["workers"]))
            try:
                value = parse_positive_int_or_back(raw)
                if value is None:
                    LOGGER.info("Setting change cancelled by back command. setting=workers")
                    continue
                settings["workers"] = value
                LOGGER.info("Setting changed. workers=%s", settings["workers"])
            except ValueError:
                print(color("Invalid number.", C.RED))
                LOGGER.warning("Invalid workers setting rejected: %r", raw)
                pause()
        elif choice == "4":
            raw = ask("Quick chunk size in MB", str(settings["quick_chunk_mb"]))
            try:
                value = parse_positive_int_or_back(raw)
                if value is None:
                    LOGGER.info("Setting change cancelled by back command. setting=quick_chunk_mb")
                    continue
                settings["quick_chunk_mb"] = value
                LOGGER.info("Setting changed. quick_chunk_mb=%s", settings["quick_chunk_mb"])
            except ValueError:
                print(color("Invalid number.", C.RED))
                LOGGER.warning("Invalid quick chunk setting rejected: %r", raw)
                pause()
        elif choice == "5":
            raw = ask("Full chunk size in MB", str(settings["full_chunk_mb"]))
            try:
                value = parse_positive_int_or_back(raw)
                if value is None:
                    LOGGER.info("Setting change cancelled by back command. setting=full_chunk_mb")
                    continue
                settings["full_chunk_mb"] = value
                LOGGER.info("Setting changed. full_chunk_mb=%s", settings["full_chunk_mb"])
            except ValueError:
                print(color("Invalid number.", C.RED))
                LOGGER.warning("Invalid full chunk setting rejected: %r", raw)
                pause()
        else:
            print(color("Invalid option.", C.RED))
            LOGGER.warning("Invalid settings menu option: %r", choice)
            pause()


def main_menu() -> None:
    settings = {
        "recursive": True,
        "follow_symlinks": False,
        "workers": DEFAULT_WORKERS,
        "quick_chunk_mb": DEFAULT_QUICK_CHUNK_MB,
        "full_chunk_mb": DEFAULT_FULL_CHUNK_MB,
    }

    while True:
        print_header()
        print(menu_title("Main Menu"))
        print(menu_option("1", "Scan only and create duplicate report", C.LIME))
        print(menu_option("2", "Scan and move duplicates to Recycle Bin", C.SKY))
        print(menu_option("3", "Scan and permanently delete duplicates", C.ORANGE))
        print(menu_option("4", "Settings", C.PINK))
        print(menu_option("5", "Check dependencies again", C.CYAN))
        print(menu_option("0", "Exit", C.GOLD))

        choice = ask_menu()

        if choice == "0":
            print(color("Goodbye.", C.GREEN))
            LOGGER.info("Main menu exit selected.")
            return
        if choice == "1":
            LOGGER.info("Main menu selected: dry run")
            run_job("dry_run", settings)
            pause()
        elif choice == "2":
            LOGGER.info("Main menu selected: recycle delete")
            run_job("recycle", settings)
            pause()
        elif choice == "3":
            LOGGER.info("Main menu selected: permanent delete")
            run_job("permanent", settings)
            pause()
        elif choice == "4":
            LOGGER.info("Main menu selected: settings")
            settings_menu(settings)
        elif choice == "5":
            LOGGER.info("Main menu selected: dependency check")
            check_dependencies(interactive=True)
            pause()
        else:
            print(color("Invalid option.", C.RED))
            LOGGER.warning("Invalid main menu option: %r", choice)
            pause()


def self_test() -> int:
    """Run a small non-interactive test suite for core duplicate detection."""
    LOGGER.info("Self-test started.")
    load_optional_modules()
    settings = {
        "recursive": True,
        "follow_symlinks": False,
        "workers": 2,
        "quick_chunk_mb": 1,
        "full_chunk_mb": 1,
    }

    with tempfile.TemporaryDirectory(prefix="dupcls_test_") as temp_name:
        if parse_positive_int_or_back("0") is not None:
            print(color("Self-test failed: numeric back command mismatch.", C.RED))
            LOGGER.error("Self-test failed: numeric back command mismatch.")
            return 1
        if parse_positive_int_or_back("5") != 5 or parse_positive_int_or_back("-2") != 1:
            print(color("Self-test failed: numeric setting parsing mismatch.", C.RED))
            LOGGER.error("Self-test failed: numeric setting parsing mismatch.")
            return 1

        root = Path(temp_name)
        nested = root / "nested"
        nested.mkdir()

        keep = root / "Video.bin"
        dupe1 = root / "Video - Copy.bin"
        dupe2 = nested / "Video (1).bin"
        unique = root / "unique.bin"

        keep.write_bytes(b"duplicate-data" * 1024)
        dupe1.write_bytes(b"duplicate-data" * 1024)
        dupe2.write_bytes(b"duplicate-data" * 1024)
        unique.write_bytes(b"unique-data" * 1024)

        folder_groups, folder_errors, folder_reclaim, _, _ = scan_duplicates(root, settings)
        if folder_errors:
            print(color(f"Self-test failed: unexpected errors: {folder_errors}", C.RED))
            LOGGER.error("Self-test failed: unexpected folder scan errors: %s", folder_errors)
            return 1
        if len(folder_groups) != 1 or sum(len(group) - 1 for group in folder_groups) != 2:
            print(color("Self-test failed: folder duplicate detection mismatch.", C.RED))
            LOGGER.error("Self-test failed: folder duplicate detection mismatch.")
            return 1
        if folder_groups[0][0] != keep:
            print(color("Self-test failed: clean-name keep selection mismatch.", C.RED))
            LOGGER.error("Self-test failed: clean-name keep selection mismatch. keep=%s actual=%s", keep, folder_groups[0][0])
            return 1
        expected_reclaim = dupe1.stat().st_size + dupe2.stat().st_size
        if folder_reclaim != expected_reclaim:
            print(color("Self-test failed: reclaim size mismatch.", C.RED))
            LOGGER.error("Self-test failed: reclaim size mismatch. expected=%s actual=%s", expected_reclaim, folder_reclaim)
            return 1

        file_groups, file_errors, _, reference_file, _ = scan_duplicates(keep, settings)
        if file_errors:
            print(color(f"Self-test failed: unexpected file-mode errors: {file_errors}", C.RED))
            LOGGER.error("Self-test failed: unexpected file-mode errors: %s", file_errors)
            return 1
        if reference_file != keep or len(file_groups) != 1 or file_groups[0][0] != keep:
            print(color("Self-test failed: reference-file mode mismatch.", C.RED))
            LOGGER.error("Self-test failed: reference-file mode mismatch.")
            return 1

        log_path = root / "logs" / "delete_log.csv"
        deleted, failed = delete_duplicates([file_groups[0]], permanent=True, log_path=log_path, workers=settings["workers"])
        if deleted != 2 or failed != 0:
            print(color("Self-test failed: permanent deletion mismatch.", C.RED))
            LOGGER.error("Self-test failed: permanent deletion mismatch. deleted=%s failed=%s", deleted, failed)
            return 1
        if dupe1.exists() or dupe2.exists() or not keep.exists():
            print(color("Self-test failed: incorrect file state after deletion.", C.RED))
            LOGGER.error("Self-test failed: incorrect file state after deletion.")
            return 1
        if not log_path.exists():
            print(color("Self-test failed: delete log was not written.", C.RED))
            LOGGER.error("Self-test failed: delete CSV log was not written.")
            return 1

    print(color("Self-test passed.", C.GREEN))
    if LOG_FILE is not None:
        print(color(f"Runtime log saved: {LOG_FILE}", C.GREEN))
    LOGGER.info("Self-test passed.")
    return 0


def main(argv: list[str] | None = None) -> None:
    raw_args = sys.argv[1:] if argv is None else argv
    configure_output(no_color="--no-color" in raw_args)
    args = build_arg_parser().parse_args(raw_args)
    configure_output(no_color=args.no_color)
    enable_ansi_colors()
    setup_logging(args.log_level, args.log_file, args.log_dir, args.no_log)
    require_python_version()

    if args.self_test:
        raise SystemExit(self_test())

    needs_pause = check_dependencies(interactive=True, quiet_if_ok=True)
    if needs_pause:
        pause()
    main_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(color("\nCancelled by user.", C.YELLOW))
        LOGGER.warning("Cancelled by user.")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        if code == 0 and LOG_FILE is not None:
            print(color("\nExiting.", C.GREEN))
        LOGGER.info("Process exiting. code=%s", code)
        raise
    except Exception as exc:
        LOGGER.exception("Unhandled error.")
        print(color(f"\nUnhandled error: {exc}", C.RED))
        if LOG_FILE is not None:
            print(color(f"Runtime log saved: {LOG_FILE}", C.YELLOW))
        raise
