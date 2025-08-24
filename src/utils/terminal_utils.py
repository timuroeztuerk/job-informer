"""
Terminal Utilities module
"""
import sys
import time
from loguru import logger
from ..config.settings import Config

def _sleep_quiet(self, seconds: float, prefix: str = "pause") -> None:
    try:
        total = max(0.0, float(seconds))
    except Exception:
        total = 0.0
    if total <= 0:
        return
    # minimal spinner
    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    tick = 0.1
    elapsed = 0.0
    while elapsed < total:
        ch = spinner[int((elapsed / tick)) % len(spinner)]
        remaining = max(0.0, total - elapsed)
        try:
            sys.stdout.write(f"\r{prefix} {ch} {remaining:4.1f}s")
            sys.stdout.flush()
        except Exception:
            pass
        time.sleep(min(tick, total - elapsed))
        elapsed += tick
    # At end, clear the spinner line and restore last progress (if any)
    try:
        sys.stdout.write("\r" + " " * 120 + "\r")
        if getattr(self, "_last_progress_line", ""):
            sys.stdout.write(self._last_progress_line)
        sys.stdout.flush()
    except Exception:
        pass

def _sleep_with_feedback(self, seconds: float, label: str = "waiting") -> None:
    """Sleep with periodic feedback logs so the user knows we're alive."""
    try:
        total = max(0.0, float(seconds))
    except Exception:
        total = 0.0
    if total <= 0:
        return
    tick = max(0.25, float(getattr(self, '_wait_tick_seconds', 1.0)))
    remaining = total
    logger.info(f"{label} — sleeping {total:.1f}s")
    while remaining > 0:
        step = min(tick, remaining)
        time.sleep(step)
        remaining -= step
        if remaining > 0:
            logger.info(f"{label} — {remaining:.1f}s remaining")

def _progress_bar(self, current: int, total: int, width: int = 24) -> str:
    try:
        total = max(1, int(total))
        current = max(0, min(int(current), total))
        filled = int(width * current / total)
        return '█' * filled + '-' * (width - filled)
    except Exception:
        return '-' * width

def _print_progress(self, prefix: str, current: int, total: int, suffix: str = "") -> None:
    try:
        bar = self._progress_bar(current, total)
        line = f"\r{prefix} [{bar}] {current}/{total} {suffix}".rstrip()
        sys.stdout.write(line)
        sys.stdout.flush()
        # store last line to restore after pauses
        try:
            self._last_progress_line = line
        except Exception:
            pass
        if current >= total:
            sys.stdout.write("\n")
            sys.stdout.flush()
    except Exception:
        pass
